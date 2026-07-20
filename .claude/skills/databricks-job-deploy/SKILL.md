---
name: databricks-job-deploy
description: Creates or updates a Databricks job from a validated JSON spec, then triggers a real run and retries until it succeeds, stopping early if the exact same real error repeats (a deterministic bug, not a transient failure). Use when asked to deploy or update a Databricks job from a spec, or to run a job until it succeeds.
risk: write-confirm
loop-tier: retry-until-resolved
---

## Purpose

Deploying a Databricks job today means hand-clicking through the UI or writing a one-off script against the Jobs API, then manually re-triggering it if the first run fails for a transient reason (a cluster that was still spinning up, a brief network blip). This skill does both from one validated JSON spec: creates or updates the job, runs it, and retries real failures — but stops immediately, rather than burning the retry budget, the moment it sees the exact same real error twice, since that's strong evidence of a deterministic bug in the spec itself.

## When to use

Use this skill when asked to: deploy a Databricks job from a spec, update an existing job and re-run it, or run a job until it succeeds rather than just once.

## Steps

1. Prepare a spec file: a JSON object with a `tasks` list (Databricks Jobs API task shape — `task_key`, `notebook_task`, `job_cluster_key`, etc.) and optionally a `job_clusters` list. This skill does minimal structural validation (every task has a `task_key`, the tasks list is non-empty) — it is not a full Jobs API schema validator.
2. **Always run without `--execute` first.** `python {{SKILL_DIR}}/scripts/job_deploy.py --job-name NAME --spec-file PATH` validates the spec and reports what would happen — it touches nothing in the workspace.
3. Present the plan to the user and get explicit confirmation before ever passing `--execute`, per this repo's standing rule for every write-capable skill.
4. Only after confirmation, run again with `--execute` (accepts `--max-retries`, default 3). The script then:
   - Creates the job if none exists with this name, or updates (`jobs.reset`) the existing one — never creates a duplicate
   - Triggers a real run and waits for its real terminal state
   - **This is the Task Loop:** on `SUCCESS`, converges immediately. On `FAILED`, pulls the specific failing task's real error message (the same extraction approach as `databricks-job-triage`), and retries with a genuinely new run — **unless the exact same error message repeats from the previous attempt**, in which case it stops immediately rather than exhausting `--max-retries` on a failure nothing about the retry could fix.
   - Hard-caps at `--max-retries` regardless, per `.claude/rules/loop-engineering.md`.
5. Reports a full audit log: every attempt's real run ID, timestamp, and outcome.
6. **Never deletes or destroys anything** — only creates/updates the one named job and triggers runs on it.

## Helper scripts

- `{{SKILL_DIR}}/scripts/job_deploy.py` — orchestrates `databricks-sdk`'s `WorkspaceClient` (job create/reset/run/poll); no dependency beyond the SDK.

## Output format

Render as Markdown:

1. **Summary line** — bolded: job name, created or updated, converged or not, in how many attempts.
2. **Audit log (table)** — attempt number, run ID, real outcome, error message if failed.
3. **If not converged:** state plainly whether it hit the retry cap or stopped early due to a repeated identical error — the latter should be reported as "this is very likely a real bug in the spec, fix it before retrying," not as "try again."
4. **Bottom line** — one bolded sentence: "Deployed and running successfully" or naming the exact real error to fix.

## Verified live (not just fixture-tested)

Built and tested against a real Databricks workspace.

- **Dry run + invalid-spec validation**: confirmed a valid 1-task spec reports `dry_run_validated` and touches nothing; a spec with a missing `task_key` is correctly rejected before any API call.
- **Real success path**: a real job (`job_deploy_test_success`) was created, then a second invocation correctly found and *updated* the existing job (`action_taken: "updated"`) rather than creating a duplicate, triggered a real run, and converged (`converged: true`) after exactly 1 attempt with a real run ID and timestamp.
- **Real deterministic-failure path**: a second real job (`job_deploy_test_fail`) with a notebook that always raises the same `RuntimeError` was run with `--max-retries 3`. It correctly failed twice with the **identical** real error message both times, and correctly stopped after attempt 2 (`stopped_reason: "identical_error_repeated_stopping_early"`) — it did **not** waste the 3rd allowed attempt on a failure two real attempts had already proven was deterministic.

Test job and notebooks deleted after verification.

## Verification status per branch (honest status, not hidden)

| Path | Live |
|---|---|
| Dry run, valid spec | ✅ |
| Invalid spec rejected before any API call | ✅ (missing `task_key`, empty `tasks` list, non-object spec) |
| Job creation (no existing job with this name) | ✅ (real `job_deploy_test_fail` job) |
| Job update (existing job with this name) | ✅ (real `job_deploy_test_success` job, second invocation) |
| Converged success (1 attempt) | ✅ |
| Early-stop on identical repeated error | ✅ (real 2-attempt case, stopped before the 3-attempt cap) |
| `max_retries_reached` (genuinely different errors each time, cap hit) | not yet observed live — would need a spec that fails differently each attempt; the early-stop path is proven, this variant is unit-tested only via `should_stop_early` |

## Loop tier & safety limits

The repo's fourth **Task Loop (retry-until-resolved)** skill, and its third **write-confirm** one. Per `.claude/rules/loop-engineering.md`:
- **Hard retry cap:** `--max-retries` (default 3).
- **Each retry changes something real:** every attempt triggers a genuinely new run and waits for its real terminal state — never a cached or assumed result.
- **Early escalation over blind retrying:** an identical real error across two attempts means retrying again wouldn't "change something" in any meaningful sense — the loop stops itself rather than mechanically exhausting the cap.
- **Full audit log:** every attempt's real run ID, timestamp, and outcome — required for any loop above on-demand, doubly so for one that deploys and runs real jobs.
- **Confirm every time:** dry run is the default; `--execute` must be passed explicitly, and the agent must present the plan and get the user's go-ahead first, per this repo's standing rule.
