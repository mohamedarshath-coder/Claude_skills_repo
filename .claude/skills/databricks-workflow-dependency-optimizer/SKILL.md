---
name: databricks-workflow-dependency-optimizer
description: Finds tasks in a Databricks job's DAG that have no declared dependency between them, then checks real historical run timing to see whether they actually run concurrently or always run back-to-back anyway -- real evidence of unnecessary serialization, not a guess from the DAG shape alone. Use when asked to review a Databricks job's task dependencies, why a multi-task job takes longer than expected, or whether independent tasks could run in parallel.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Could this job run faster if independent tasks ran in parallel?" today means manually reading a job's task graph, guessing which tasks are truly independent, and eyeballing the Gantt-style run view for whether they actually overlapped. This skill answers it from real evidence: it computes the job's true dependency structure (including transitive dependencies, not just direct edges), finds every pair of tasks the DAG genuinely does not require to run in order, and checks real historical run timestamps to see whether those pairs ever actually ran at the same time.

## When to use

Use this skill when asked to: review a Databricks job's task dependencies, explain why a multi-task job takes longer than its critical path suggests, or find opportunities to parallelize independent tasks.

## Steps

1. Confirm the Databricks connection: `DATABRICKS_HOST`/`DATABRICKS_TOKEN` env vars, or a named profile in `~/.databrickscfg` (pass via `--profile`). Never handled directly by this skill.
2. Run `python {{SKILL_DIR}}/scripts/workflow_dependency_optimizer.py --job-id ID` (accepts `--profile`, `--run-limit` [default 10], `--min-evidence-runs` [default 3]).
3. The script:
   - Fetches the job's real task graph and computes the **full transitive ancestor closure** for every task — not just direct `depends_on` edges, since a task 3 levels downstream is still constrained by everything above it
   - Finds every pair of tasks where **neither is an ancestor of the other** — genuinely independent by the DAG's own definition, free to run concurrently in principle
   - Pulls real per-task start/end timestamps from historical runs, and checks whether each independent pair's time windows ever actually overlapped
   - **Flags a pair only if it never overlapped across at least `--min-evidence-runs` runs where both tasks had valid timing data** — a single coincidental non-overlap isn't enough evidence
   - If a flagged pair shares the same cluster, that's surfaced as a plausible root cause (shared/undersized compute); if not, the message says the cause isn't obviously shared-cluster contention
   - Computes the job's **critical path** — the longest real dependency chain by observed average duration, not just task count — and compares it to the real average total job duration, surfacing any gap
4. Distinguishes honest outcomes rather than forcing a verdict: `single_task_job`, `fully_linear_dag` (no independent pairs exist — serialized by design), `not_enough_run_history`, `unnecessary_serialization_found`, and `already_parallelized` (independent pairs exist and were confirmed running concurrently — no issue).
5. Turn the JSON into a Markdown report. Always cite the actual task keys, run counts, and overlap evidence — never a bare "tasks 3 and 5 should run in parallel" without the timing data behind it.

## Helper scripts

- `{{SKILL_DIR}}/scripts/workflow_dependency_optimizer.py` — pure `databricks-sdk` calls plus real graph/interval-overlap analysis; no dependency beyond the SDK.

## Output format

Render as Markdown:

1. **Summary line** — bolded: job name, task count, and the overall status in one phrase.
2. **Flagged pairs (table)** — only if non-empty:

   | Task A | Task B | Runs checked | Shared cluster |
   |---|---|---|---|
   | `1_bronze` | `2_silver` | 8 | `shared_small_cluster` |

3. **Critical path** — the real longest chain by observed duration, vs. the actual average total job duration, and the gap between them.
4. **Bottom line** — one bolded sentence: the single pair to investigate first, or an explicit "no action — already parallelized" / "no action — fully linear by design."

## Verified live (not just fixture-tested)

Run against real jobs in the workspace, plus a deliberately-built test job, on 2026-07-16.

**Two real bugs found and fixed before this ever produced a trustworthy number:**

1. **Critical-path calculation silently collapsed to a 1-node or empty chain whenever no run history existed.** The initial code compared candidate chain lengths with a strict `>` against a zero-initialized baseline — when every task has an observed duration of `0.0` (no runs yet), no candidate is ever strictly greater than zero, so the "best" chain never updates from its starting value. Found live on `ml_creation` (a real, genuine 2-task linear job): the critical path came back as a single node instead of the full 2-task chain. Fixed with a tie-break (prefer more tasks when duration is equal) at both the per-task and cross-task selection layers, and regression-pinned with unit tests.
2. **`jobs.list_runs()` returns a lightweight summary whose `tasks` field is always an empty list**, regardless of how many tasks the run actually had — the exact same "list vs. get" API gap already learned earlier the same session with job task definitions (`jobs.list()` vs `jobs.get()`), resurfacing one level deeper at the run level. This meant `runs_analyzed` was always `0` no matter how much real run history existed. Found live immediately after a real test run completed successfully but the script reported zero analyzable runs. Fixed by calling `jobs.get_run(run_id=...)` for each run found via `list_runs()`.

**Real graph-analysis logic confirmed against a genuine 13-task job (`Activity_job`, a real production-style DAG with a 3-way fan-out):** correctly computed 51 independent pairs (manually verified: 3 branches of 4 nodes each = 48 cross-branch pairs, plus 3 same-branch sibling pairs = 51). `single_task_job` and `fully_linear_dag` also confirmed live against real jobs (`insightops_dbt_demo_pipeline`, `ml_creation`).

**The full timing-analysis path is now live-verified end to end**, using a deliberately-built test job (`dep_optimizer_test_job`, 4 tasks: `root` → 3 independent siblings `branch_a`/`branch_b`/`branch_c`, each just sleeping ~25s, all sharing one deliberately small single-node cluster to try to force resource contention). Real result: **Databricks ran all 3 independent tasks fully concurrently anyway** (start times within ~220ms of each other) — correctly reported as `already_parallelized`, not a fabricated finding. The attempt to force serialization via a small shared cluster did not succeed, which is itself an honest, useful result: Databricks' scheduler parallelizes independent tasks more readily than assumed, even under constrained compute. The critical path calculation (`root` + `branch_a` ≈ 380.2s) matched the real observed average job duration (381.1s) within 0.9 seconds — confirming the math is accurate against real timestamps, not just plausible-looking.

## Verification status per branch (honest status, not hidden)

| Path | Live | Unit-tested |
|---|---|---|
| `single_task_job` | ✅ (real 1-task job) | — |
| `fully_linear_dag` | ✅ (real 2-task linear job, post critical-path-bug-fix) | ✅ |
| `not_enough_run_history` | ✅ (real 13-task job with zero runs) | — |
| Transitive ancestor closure / independent-pair detection | ✅ (real 13-task, 3-way-fanout job; 51 pairs manually cross-checked) | ✅ |
| `already_parallelized` | ✅ (real 4-task test job, 3 siblings confirmed genuinely concurrent) | — |
| Critical path vs. real observed duration | ✅ (380.2s estimated vs. 381.1s actual, 0.9s gap) | ✅ (regression-pinned for the zero-duration bug) |
| `unnecessary_serialization_found` (a pair that genuinely never overlaps) | ❌ the one real attempt to force this via a constrained shared cluster resulted in genuine parallelism instead | ✅ (constructed timing data proving zero-overlap detection works correctly) |

**What's still open:** `unnecessary_serialization_found` has not fired live — the one deliberate attempt to construct this scenario (a small shared cluster) resulted in real parallelism instead, which is itself a useful, honestly-reported finding rather than a gap to hide. Proving this branch live would need either a genuinely resource-starved cluster (more tasks than the cluster can truly run at once) or a real production job that happens to exhibit this pattern.

## Loop tier

**On-demand (Tier 1).** A plausible future Tier 3 candidate (a weekly scan across all multi-task jobs, notify only on newly-found serialization), but that requires the same explicit promotion checklist as every other Tier 3 skill in this repo. Not done here.
