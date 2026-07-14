---
name: databricks-job-triage
description: Diagnoses recently failed Databricks job runs by pulling each one's actual error/stack trace (pre-filtered), and suggests a root cause and fix. Use when asked about failed Databricks jobs, job run errors, or "why did this job fail."
risk: read-only
loop-tier: on-demand
---

## Purpose

A failed Databricks job today means opening the workspace UI, finding the run, digging through a multi-task DAG, and reading a Spark stack trace that's often 90% retry/noise before the actual exception. This skill collapses that into one command: find recent failures, pull each one's real error, and explain what likely broke.

## When to use

Use this skill when asked about: failed Databricks jobs, why a job/run failed, recent job errors, or a Databricks job triage/diagnosis request.

## Steps

1. Confirm the Databricks connection: `DATABRICKS_HOST` and `DATABRICKS_TOKEN` env vars, or a named profile in `~/.databrickscfg` (pass via `--profile`). Configured locally by the user ahead of time — this skill never asks for or handles the token directly.
2. Run `python {{SKILL_DIR}}/scripts/job_triage.py` with the desired lookback window (default 3 days, accepts `--days N`) and a cap on how many failed runs to inspect (default 20, accepts `--max-runs N`).
3. The script:
   - Lists completed job runs in the window and filters to `result_state == FAILED`
   - For multi-task jobs, identifies the specific failed task(s), not just "the job failed"
   - Pulls each failed task's error message and stack trace via the Jobs API
   - **Mandatory log pre-filtering (see below)** — the trace never reaches you raw
4. Read the script's JSON output and turn it into a Markdown report (see Output format). Group by job, not by raw run ID list.
5. For each failure, give your best-guess root cause from the error message/trace (e.g. `OutOfMemoryError` → cluster undersized or a skewed join; `FileNotFoundException` → an upstream path/table dependency that didn't land in time) and a concrete next step — cite the evidence, don't guess without it.
6. If nothing failed in the window, say so plainly.

## Log pre-filtering (mandatory — this skill touches raw logs)

Per `.claude/rules/skill-template.md`, any skill ingesting raw logs must pre-filter *before* the payload reaches the agent:
- **Collapse repetition**: `job_triage.py` collapses runs of 3+ identical consecutive lines (a Spark/py4j retry-loop signature) into one line + a repeat count.
- **Degrade gracefully, not silently**: only the **last 40 lines** of each (collapsed) trace are kept. The JSON always reports `trace_total_lines` and `trace_suppressed_lines` so the report can say "showing the last 40 of 340 lines" rather than pretending the trace was short.
- Never dump a full multi-hundred-line trace into the response — summarize using the tail + the suppressed-count, and link back to the run in the workspace UI for anyone who needs the full trace.

## Helper scripts

- `{{SKILL_DIR}}/scripts/job_triage.py` — lists failed runs via the Databricks Jobs API, resolves multi-task failures to the specific failing task, and pre-filters each trace server-side before returning JSON. Uses `databricks-sdk`'s `WorkspaceClient`, which reads the connection from env vars or a named profile; never handles the token directly.

## Output format

Render as Markdown, grouped by job:

1. **Summary line** — bolded: how many failed runs in the window, across how many distinct jobs.
2. **Per failed job (repeat per job):**
   - Job name, run ID, when it failed
   - Failed task key (if multi-task)
   - Error message (the actual exception line, not the full trace)
   - Root-cause guess + suggested next step, clearly labeled as a guess if the evidence is ambiguous
   - Note if the trace was truncated: "(showing last 40 of N lines)"
3. **No action needed** if `failed_run_count` is 0 — state this explicitly.

Always cite the actual run ID, job name, and error text pulled by the script — never a vague "something failed" without evidence.

## Verification status per branch (honest status, not hidden)

| Path | Live workspace | Unit-tested |
|---|---|---|
| Single-task failure resolution + real trace | ✅ (5 real failed runs, masked-dbt-error finding) | ✅ |
| **Multi-task resolution to the specific failing task** | ❌ every real failure so far was single-task | ✅ `test-fixtures/test_triage.py` (1-of-3 failed, 2-of-3 failed, output fetched only for failed task run_ids) |
| ANSI stripping / repetition collapse / tail cap | ✅ (real ANSI-laden dbt traces) | ✅ (incl. 50x-repeat collapse, 200-line cap, suppressed-count honesty) |

The multi-task path is covered by unit tests running the real `summarize_run()` against constructed run objects with a stub client; live confirmation awaits a real multi-task job failure occurring naturally.

## Loop tier & future promotion

Currently **Tier 1 (on-demand)**, per repo rule (`.claude/rules/loop-engineering.md`) that no skill starts above Tier 1.

This is one of the strongest Tier-3 (scheduled/event-triggered) candidates in the catalog — ideally triggered by a job-failure webhook rather than a fixed interval, so it fires the moment a run fails instead of on a timer. Promotion requires its own explicit PR adding: an event trigger (webhook) or polling schedule, a notification channel, a per-run budget, a kill switch, and run logging — same checklist as every other Tier-3 promotion in this repo. Not done here.
