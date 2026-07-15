#!/usr/bin/env python3
"""
databricks-job-triage helper script.

Finds recently FAILED job runs and pulls each one's actual error, with
MANDATORY log pre-filtering before the payload reaches the agent (per
.claude/rules/skill-template.md, section 5): raw Spark/py4j stack traces
are extremely repetitive (retry loops, duplicated frames) and can blow
past context limits before the real exception is ever reached.

Auth: uses databricks-sdk's WorkspaceClient, which reads DATABRICKS_HOST /
DATABRICKS_TOKEN env vars (or a named profile in ~/.databrickscfg) that
each user configures locally. Never touches credentials directly.

Usage:
    python job_triage.py [--days N] [--max-runs N] [--profile NAME]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import json
import os
import re
import sys
import time

from databricks.sdk import WorkspaceClient

# Degradation rule: cap the trace payload so a worse-than-expected log
# truncates gracefully instead of silently -- last N lines of the real
# failure, plus a count of what was suppressed.
TRACE_TAIL_LINES = 40

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def pre_filter_trace(raw_trace):
    """
    Strip repetitive noise before the payload reaches the agent:
    1. Strip ANSI color/formatting escape codes (Databricks notebook
       output includes these; they render as unreadable garbage in a
       plain-text or Markdown report).
    2. Collapse runs of identical consecutive lines (retry/GC-log style
       repetition) into a single line + a repeat count.
    3. Keep only the last TRACE_TAIL_LINES of the (collapsed) trace --
       that's where the actual exception almost always lives -- and
       report how many lines were suppressed.
    """
    if not raw_trace:
        return {"tail": "", "suppressed_line_count": 0, "total_lines": 0}

    raw_trace = ANSI_ESCAPE_RE.sub("", raw_trace)
    lines = raw_trace.splitlines()
    total_lines = len(lines)

    collapsed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i
        while j < len(lines) and lines[j] == line:
            j += 1
        repeat_count = j - i
        if repeat_count > 2:
            collapsed.append(f"{line}  [repeated {repeat_count}x, collapsed]")
        else:
            collapsed.extend(lines[i:j])
        i = j

    suppressed = max(0, len(collapsed) - TRACE_TAIL_LINES)
    tail = collapsed[-TRACE_TAIL_LINES:]
    return {
        "tail": "\n".join(tail),
        "suppressed_line_count": suppressed,
        "total_lines": total_lines,
    }


def get_failed_runs(client, days, max_runs):
    """Returns (failed_runs, truncated). truncated=True means the cap was
    hit before the time window was fully scanned -- there are MORE
    failures in the window than were returned. This must be surfaced,
    never silently dropped (found live: a 14-day window had 46 real
    failures against the default --max-runs=20, and the report gave no
    indication 26 more existed)."""
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    failed = []
    truncated = False
    for run in client.jobs.list_runs(expand_tasks=True, completed_only=True):
        if run.start_time is not None and run.start_time < cutoff_ms:
            break  # list_runs is newest-first; once past the window, stop naturally
        state = run.state
        result_state = getattr(state, "result_state", None)
        result_state_val = getattr(result_state, "value", result_state)
        if result_state_val != "FAILED":
            continue
        failed.append(run)
        if len(failed) >= max_runs:
            truncated = True
            break
    return failed, truncated


def summarize_run(client, run):
    job_name = run.run_name or f"job-{run.job_id}"
    entry = {
        "job_name": job_name,
        "run_id": run.run_id,
        "job_id": run.job_id,
        "start_time_epoch_ms": run.start_time,
        "state_message": getattr(run.state, "state_message", None),
        "failed_tasks": [],
    }

    # Multi-task jobs: find the specific task(s) that failed and pull
    # each one's error output. Single-task jobs have no `tasks` list --
    # fall back to the top-level run itself.
    task_runs = run.tasks or [run]

    for task in task_runs:
        task_run_id = getattr(task, "run_id", run.run_id)
        task_state = getattr(task, "state", run.state)
        task_result = getattr(getattr(task_state, "result_state", None), "value", None)
        if task_result != "FAILED":
            continue

        try:
            output = client.jobs.get_run_output(run_id=task_run_id)
            error = output.error or ""
            trace = output.error_trace or ""
        except Exception as e:
            error = f"(could not fetch run output: {e})"
            trace = ""

        filtered = pre_filter_trace(trace)
        entry["failed_tasks"].append({
            "task_key": getattr(task, "task_key", None),
            "task_run_id": task_run_id,
            "error_message": error,
            "trace_tail": filtered["tail"],
            "trace_suppressed_lines": filtered["suppressed_line_count"],
            "trace_total_lines": filtered["total_lines"],
        })

    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    args = parser.parse_args()

    try:
        client = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
        failed_runs, truncated = get_failed_runs(client, args.days, args.max_runs)
        summaries = [summarize_run(client, r) for r in failed_runs]
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    output = {
        "window_days": args.days,
        "failed_run_count": len(summaries),
        "truncated": truncated,
        "truncation_note": (
            f"Showing the first {args.max_runs} failures found (newest first) -- "
            f"there may be MORE failures in this {args.days}-day window. "
            f"Re-run with a higher --max-runs to see them all."
        ) if truncated else None,
        "failed_runs": summaries,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
