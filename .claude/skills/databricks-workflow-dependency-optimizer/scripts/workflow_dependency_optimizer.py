#!/usr/bin/env python3
"""
databricks-workflow-dependency-optimizer helper script.

Finds tasks in a Databricks job's DAG that have NO declared dependency
between them (directly or transitively), then checks REAL historical run
timing to see whether they actually ran concurrently or always ran back
to back anyway -- the latter is real evidence of unnecessary
serialization (commonly a shared, undersized job cluster), not a guess
from the DAG shape alone.

Also computes the job's real critical path (the longest true dependency
chain, by observed average task duration) against the actual observed
total run duration -- a real gap between the two is further evidence
independent tasks aren't running in parallel as they could.

Auth: uses databricks-sdk's WorkspaceClient, which reads DATABRICKS_HOST /
DATABRICKS_TOKEN env vars (or a named profile in ~/.databrickscfg) that
each user configures locally. Never touches credentials directly.

Usage:
    python workflow_dependency_optimizer.py --job-id ID
        [--profile NAME] [--run-limit N] [--min-evidence-runs N]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import json
import os
import sys

from databricks.sdk import WorkspaceClient

MIN_EVIDENCE_RUNS_DEFAULT = 3


def get_client(profile):
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def fetch_section(errors, section_name, fetch_fn):
    try:
        return fetch_fn()
    except Exception as e:
        errors[section_name] = str(e)[:300]
        return None


def build_ancestor_closure(tasks):
    """tasks: list of {"task_key": str, "depends_on": [task_key, ...]}.
    Returns {task_key: set(all transitive ancestor task_keys)} -- the
    real ordering constraints implied by the DAG, not just direct edges."""
    direct_deps = {t["task_key"]: set(t.get("depends_on", [])) for t in tasks}
    ancestors = {}

    def resolve(tk, visiting):
        if tk in ancestors:
            return ancestors[tk]
        if tk in visiting:
            return set()  # cycle guard -- shouldn't occur in a valid Databricks DAG
        visiting = visiting | {tk}
        result = set()
        for dep in direct_deps.get(tk, set()):
            result.add(dep)
            result |= resolve(dep, visiting)
        ancestors[tk] = result
        return result

    for t in tasks:
        resolve(t["task_key"], set())
    return ancestors


def find_independent_pairs(tasks, ancestors):
    """Pairs of tasks where NEITHER is an ancestor of the other -- i.e.
    the DAG genuinely imposes no ordering between them, so they are
    theoretically free to run concurrently."""
    keys = [t["task_key"] for t in tasks]
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            if b not in ancestors.get(a, set()) and a not in ancestors.get(b, set()):
                pairs.append((a, b))
    return pairs


def intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def analyze_pair_across_runs(pair, per_run_timing, min_evidence_runs):
    """per_run_timing: list of {task_key: (start_ms, end_ms)}, one dict
    per historical run. Only counts a run as valid evidence if BOTH tasks
    in the pair actually have timing data in that run."""
    a, b = pair
    valid_runs = 0
    overlap_count = 0
    for run in per_run_timing:
        if a in run and b in run:
            valid_runs += 1
            if intervals_overlap(run[a][0], run[a][1], run[b][0], run[b][1]):
                overlap_count += 1

    if valid_runs < min_evidence_runs:
        return {"status": "not_enough_runs", "valid_runs": valid_runs}
    if overlap_count == 0:
        return {"status": "always_sequential", "valid_runs": valid_runs, "overlap_count": 0}
    return {"status": "parallel_confirmed", "valid_runs": valid_runs, "overlap_count": overlap_count}


def compute_critical_path(tasks, avg_durations_sec):
    """Longest chain by real observed average duration, not task count --
    a 3-task chain of 1-second tasks matters less than a 2-task chain of
    10-minute tasks. Returns (chain_task_keys, total_seconds); a task with
    no observed duration data is treated as 0s rather than dropped, so the
    chain is still computable even with partial timing data."""
    direct_deps = {t["task_key"]: t.get("depends_on", []) for t in tasks}
    memo = {}

    def longer_of(candidate, current_best):
        """Prefers strictly greater duration; ties broken by more tasks in
        the chain, so a real chain is still surfaced when duration data is
        all zero (no run history yet) instead of collapsing to length 1."""
        if current_best is None:
            return candidate
        cand_len, cand_chain = candidate
        best_len, best_chain = current_best
        if cand_len > best_len or (cand_len == best_len and len(cand_chain) > len(best_chain)):
            return candidate
        return current_best

    def longest_to(tk):
        if tk in memo:
            return memo[tk]
        deps = direct_deps.get(tk, [])
        own = avg_durations_sec.get(tk, 0.0)
        if not deps:
            memo[tk] = (own, [tk])
            return memo[tk]
        best = None
        for dep in deps:
            best = longer_of(longest_to(dep), best)
        memo[tk] = (best[0] + own, best[1] + [tk])
        return memo[tk]

    best_overall = None
    for t in tasks:
        best_overall = longer_of(longest_to(t["task_key"]), best_overall)
    return best_overall[1], round(best_overall[0], 1)


def get_job_tasks(client, job_id):
    job = client.jobs.get(job_id=job_id)
    settings = job.settings
    tasks = []
    for t in (settings.tasks or []):
        cluster_key = None
        if getattr(t, "job_cluster_key", None):
            cluster_key = t.job_cluster_key
        elif getattr(t, "existing_cluster_id", None):
            cluster_key = f"existing:{t.existing_cluster_id}"
        tasks.append({
            "task_key": t.task_key,
            "depends_on": [d.task_key for d in (t.depends_on or [])],
            "cluster_key": cluster_key,
        })
    return settings.name, tasks


def get_run_task_timings(client, job_id, run_limit):
    """Returns a list of {task_key: (start_ms, end_ms)} dicts, one per
    completed run, using only tasks that have BOTH a start and end time
    -- a still-running or skipped task has no valid window to compare.

    IMPORTANT: jobs.list_runs() returns a lightweight summary whose
    `tasks` field is ALWAYS an empty list, regardless of how many tasks
    the run actually had -- found live, the same "list vs. get" API gap
    already seen with job task definitions (jobs.list() vs jobs.get()),
    just resurfacing one level deeper at the run level. Per-task timing
    requires a separate jobs.get_run(run_id=...) call for each run."""
    per_run = []
    avg_duration_accum = {}
    total_run_durations = []
    for run_summary in client.jobs.list_runs(job_id=job_id, limit=run_limit):
        run = client.jobs.get_run(run_id=run_summary.run_id)
        if not run.tasks:
            continue
        run_timing = {}
        for t in run.tasks:
            if t.start_time and t.end_time and t.end_time > t.start_time:
                run_timing[t.task_key] = (t.start_time, t.end_time)
                dur = (t.end_time - t.start_time) / 1000.0
                avg_duration_accum.setdefault(t.task_key, []).append(dur)
        if run_timing:
            per_run.append(run_timing)
        if run.start_time and run.end_time:
            total_run_durations.append((run.end_time - run.start_time) / 1000.0)

    avg_durations_sec = {tk: sum(vals) / len(vals) for tk, vals in avg_duration_accum.items()}
    avg_actual_job_seconds = round(sum(total_run_durations) / len(total_run_durations), 1) if total_run_durations else None
    return per_run, avg_durations_sec, avg_actual_job_seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--job-id", required=True, type=int)
    parser.add_argument("--run-limit", type=int, default=10)
    parser.add_argument("--min-evidence-runs", type=int, default=MIN_EVIDENCE_RUNS_DEFAULT)
    args = parser.parse_args()

    try:
        client = get_client(args.profile)
    except Exception as e:
        print(json.dumps({"error": f"could not create Databricks client: {e}"}), file=sys.stderr)
        sys.exit(1)

    errors = {}

    job_info = fetch_section(errors, "job_tasks", lambda: get_job_tasks(client, args.job_id))
    if job_info is None:
        print(json.dumps({"error": errors.get("job_tasks", "could not fetch job")}), file=sys.stderr)
        sys.exit(1)
    job_name, tasks = job_info

    if len(tasks) < 2:
        output = {
            "job_id": args.job_id,
            "job_name": job_name,
            "task_count": len(tasks),
            "overall_status": "single_task_job",
            "detail": "Job has fewer than 2 tasks -- there is nothing to parallelize.",
            "independent_pairs_checked": 0,
            "flagged_pairs": [],
            "critical_path": None,
            "errors": errors,
        }
        print(json.dumps(output, indent=2, default=str))
        return

    ancestors = build_ancestor_closure(tasks)
    independent_pairs = find_independent_pairs(tasks, ancestors)

    run_data = fetch_section(errors, "run_history",
                             lambda: get_run_task_timings(client, args.job_id, args.run_limit))
    per_run_timing, avg_durations_sec, avg_actual_job_seconds = run_data or ([], {}, None)

    critical_chain, critical_seconds = compute_critical_path(tasks, avg_durations_sec)

    if not independent_pairs:
        overall_status = "fully_linear_dag"
        detail = f"All {len(tasks)} tasks form a single dependency chain -- there is no independent pair to parallelize; this job is serialized by design, not by accident."
        flagged_pairs = []
    elif not per_run_timing:
        overall_status = "not_enough_run_history"
        detail = f"{len(independent_pairs)} independent task pair(s) exist in the DAG, but no completed run has timing data to check whether they actually run in parallel."
        flagged_pairs = []
    else:
        flagged_pairs = []
        confirmed_parallel_count = 0
        insufficient_evidence_count = 0
        for pair in independent_pairs:
            result = analyze_pair_across_runs(pair, per_run_timing, args.min_evidence_runs)
            if result["status"] == "always_sequential":
                same_cluster = None
                cluster_by_key = {t["task_key"]: t["cluster_key"] for t in tasks}
                if cluster_by_key.get(pair[0]) and cluster_by_key.get(pair[0]) == cluster_by_key.get(pair[1]):
                    same_cluster = cluster_by_key[pair[0]]
                flagged_pairs.append({
                    "task_a": pair[0],
                    "task_b": pair[1],
                    "runs_checked": result["valid_runs"],
                    "overlap_count": 0,
                    "shared_cluster": same_cluster,
                    "evidence": f"No dependency exists between '{pair[0]}' and '{pair[1]}' in the DAG, but across {result['valid_runs']} real runs where both executed, they NEVER overlapped in time." +
                               (f" Both use the same cluster ('{same_cluster}'), a plausible root cause." if same_cluster else " They use different compute, so the cause isn't obviously shared-cluster contention -- worth checking the job's own scheduling/orchestration logic."),
                })
            elif result["status"] == "parallel_confirmed":
                confirmed_parallel_count += 1
            else:
                insufficient_evidence_count += 1

        if flagged_pairs:
            overall_status = "unnecessary_serialization_found"
            detail = f"{len(flagged_pairs)} of {len(independent_pairs)} independent task pair(s) never ran concurrently across real runs, despite no dependency requiring that."
        elif confirmed_parallel_count > 0:
            overall_status = "already_parallelized"
            detail = f"All {confirmed_parallel_count} independent pair(s) with enough run evidence were confirmed running concurrently in at least one real run -- no serialization issue found."
        else:
            overall_status = "not_enough_run_history"
            detail = f"{len(independent_pairs)} independent pair(s) exist, but none had {args.min_evidence_runs}+ runs with timing data for both tasks."

    output = {
        "job_id": args.job_id,
        "job_name": job_name,
        "task_count": len(tasks),
        "runs_analyzed": len(per_run_timing),
        "independent_pairs_checked": len(independent_pairs),
        "flagged_pairs": flagged_pairs,
        "critical_path": {
            "chain": critical_chain,
            "estimated_seconds": critical_seconds,
            "avg_actual_job_seconds": avg_actual_job_seconds,
            "gap_seconds": round(avg_actual_job_seconds - critical_seconds, 1) if avg_actual_job_seconds is not None else None,
        } if critical_chain else None,
        "overall_status": overall_status,
        "detail": detail,
        "errors": errors,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
