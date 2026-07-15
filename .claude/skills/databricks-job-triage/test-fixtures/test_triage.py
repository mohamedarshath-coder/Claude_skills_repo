#!/usr/bin/env python3
"""
Unit tests for job_triage's multi-task resolution and log pre-filtering.

The skill's central differentiator -- "resolves multi-task failures to the
specific failing task, not just 'the job failed'" -- has never been tested
against a real multi-task job: every real failure observed so far was a
single-task job. These tests exercise that path with constructed run
objects and a stub client, plus full coverage of pre_filter_trace's three
transformations (ANSI strip, repetition collapse, tail cap).

Run: python test_triage.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from job_triage import pre_filter_trace, summarize_run, get_failed_runs, TRACE_TAIL_LINES  # noqa: E402


class ListRunsClient:
    """Stub for get_failed_runs: client.jobs.list_runs() yields constructed
    runs, newest-first, matching the real API's ordering contract."""
    def __init__(self, runs):
        self.jobs = SimpleNamespace(list_runs=lambda **kw: iter(runs))


def make_state(result="FAILED", message="boom"):
    return SimpleNamespace(result_state=SimpleNamespace(value=result), state_message=message)


def make_task(key, run_id, result="FAILED"):
    return SimpleNamespace(task_key=key, run_id=run_id, state=make_state(result))


class StubClient:
    """Returns a distinct error per task_run_id, and records which run_ids
    were fetched -- so we can assert output was pulled ONLY for failed tasks."""
    def __init__(self):
        self.fetched = []
        self.jobs = SimpleNamespace(get_run_output=self._get_run_output)

    def _get_run_output(self, run_id):
        self.fetched.append(run_id)
        return SimpleNamespace(error=f"error-for-{run_id}", error_trace=f"trace line\ntrace line\nException at {run_id}")


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # --- Multi-task resolution: the never-live-tested differentiator ---
    client = StubClient()
    run = SimpleNamespace(
        run_name="multi-task-pipeline", run_id=1000, job_id=42, start_time=1234567890,
        state=make_state(),
        tasks=[make_task("extract", 1001, "SUCCESS"),
               make_task("transform", 1002, "FAILED"),
               make_task("load", 1003, "SKIPPED")],
    )
    entry = summarize_run(client, run)
    check("multi-task: only the FAILED task is reported (1 of 3)",
          len(entry["failed_tasks"]) == 1)
    check("multi-task: the right task_key is identified",
          entry["failed_tasks"][0]["task_key"] == "transform")
    check("multi-task: output fetched ONLY for the failed task's run_id",
          client.fetched == [1002])
    check("multi-task: the task's own error is captured, not the job-level message",
          entry["failed_tasks"][0]["error_message"] == "error-for-1002")

    client2 = StubClient()
    run2 = SimpleNamespace(
        run_name="two-failures", run_id=2000, job_id=43, start_time=1234567890,
        state=make_state(),
        tasks=[make_task("a", 2001, "FAILED"), make_task("b", 2002, "FAILED"), make_task("c", 2003, "SUCCESS")],
    )
    entry2 = summarize_run(client2, run2)
    check("multi-task: two simultaneous failed tasks both reported",
          sorted(t["task_key"] for t in entry2["failed_tasks"]) == ["a", "b"])

    # --- Single-task fallback (the live-proven path, as regression) ---
    client3 = StubClient()
    run3 = SimpleNamespace(
        run_name="single-task-job", run_id=3000, job_id=44, start_time=1234567890,
        state=make_state(), tasks=None,
    )
    entry3 = summarize_run(client3, run3)
    check("single-task (tasks=None): falls back to the run itself",
          len(entry3["failed_tasks"]) == 1 and client3.fetched == [3000])

    # --- get_failed_runs truncation reporting: the live-caught silent-cap bug ---
    # (14-day live sweep found 46 real failures against the default
    # --max-runs=20, with zero indication 26 more existed)
    def make_run(rid, days_ago=0):
        return SimpleNamespace(run_id=rid, start_time=1_800_000_000_000 - days_ago,
                               state=make_state())

    many_runs = [make_run(i) for i in range(5)]
    failed, truncated = get_failed_runs(ListRunsClient(many_runs), days=999, max_runs=3)
    check("cap hit: exactly max_runs returned",
          len(failed) == 3)
    check("cap hit: truncated=True (must not be silent)",
          truncated is True)

    failed, truncated = get_failed_runs(ListRunsClient(many_runs), days=999, max_runs=100)
    check("cap NOT hit (fewer failures than max_runs): truncated=False",
          len(failed) == 5 and truncated is False)

    # --- pre_filter_trace: the mandatory log pre-filtering ---
    ansi = "\x1b[0;31mException\x1b[0m: dbt failed"
    check("ANSI escape codes are stripped",
          pre_filter_trace(ansi)["tail"] == "Exception: dbt failed")

    noisy = ("retrying...\n" * 50) + "Exception: real error"
    filtered = pre_filter_trace(noisy)
    check("50 identical retry lines collapse to one line + repeat count",
          "[repeated 50x, collapsed]" in filtered["tail"] and filtered["tail"].count("retrying") == 1)
    check("collapse preserves the real exception at the end",
          filtered["tail"].endswith("Exception: real error"))

    long_trace = "\n".join(f"frame {i}" for i in range(200))
    capped = pre_filter_trace(long_trace)
    check(f"200 distinct lines cap to the last {TRACE_TAIL_LINES}",
          len(capped["tail"].splitlines()) == TRACE_TAIL_LINES)
    check("suppressed-line count reported honestly (degrade gracefully, not silently)",
          capped["suppressed_line_count"] == 200 - TRACE_TAIL_LINES and capped["total_lines"] == 200)
    check("empty trace handled without crash",
          pre_filter_trace("") == {"tail": "", "suppressed_line_count": 0, "total_lines": 0})

    print(f"\n{len(failures)} failure(s) of 12 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
