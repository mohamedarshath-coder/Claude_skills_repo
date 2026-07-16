#!/usr/bin/env python3
"""
Unit tests for workflow_dependency_optimizer's pure-logic functions --
specifically the run-timing analysis branches, since no real multi-task
job in this workspace has ANY run history yet (found live: both
Activity_job [13 tasks] and ml_creation [2 tasks] have zero completed
runs), so always_sequential / parallel_confirmed / the resulting
overall_status outcomes can't be exercised live.

Also regression-pins the critical-path bug found and fixed live: when
every task has zero observed duration (no run history), the strict '>'
comparison never updated from its initial baseline, silently collapsing
a real multi-task chain down to a length-1 chain or an empty one.

Run: python test_workflow_dependency_optimizer.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from workflow_dependency_optimizer import (  # noqa: E402
    build_ancestor_closure,
    find_independent_pairs,
    intervals_overlap,
    analyze_pair_across_runs,
    compute_critical_path,
)

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- build_ancestor_closure / find_independent_pairs ---

fanout_tasks = [
    {"task_key": "root", "depends_on": []},
    {"task_key": "branch_a", "depends_on": ["root"]},
    {"task_key": "branch_b", "depends_on": ["root"]},
    {"task_key": "branch_a_child", "depends_on": ["branch_a"]},
]
ancestors = build_ancestor_closure(fanout_tasks)
check("branch_a_child's ancestors include both branch_a and root (transitive)",
      ancestors["branch_a_child"] == {"branch_a", "root"})
check("root has no ancestors", ancestors["root"] == set())

pairs = find_independent_pairs(fanout_tasks, ancestors)
pair_set = {frozenset(p) for p in pairs}
check("branch_a/branch_b are independent (real sibling pair)", frozenset(["branch_a", "branch_b"]) in pair_set)
check("branch_a_child/branch_b are independent (transitive sibling)", frozenset(["branch_a_child", "branch_b"]) in pair_set)
check("root/branch_a are NOT independent (root is a real ancestor)", frozenset(["root", "branch_a"]) not in pair_set)
check("branch_a/branch_a_child are NOT independent (direct dependency)", frozenset(["branch_a", "branch_a_child"]) not in pair_set)

linear_tasks = [
    {"task_key": "a", "depends_on": []},
    {"task_key": "b", "depends_on": ["a"]},
    {"task_key": "c", "depends_on": ["b"]},
]
linear_ancestors = build_ancestor_closure(linear_tasks)
check("a fully linear DAG has zero independent pairs", find_independent_pairs(linear_tasks, linear_ancestors) == [])

# --- intervals_overlap ---

check("clearly overlapping intervals detected", intervals_overlap(0, 100, 50, 150) is True)
check("clearly non-overlapping (sequential) intervals detected", intervals_overlap(0, 100, 100, 200) is False)
check("fully-nested interval counts as overlap", intervals_overlap(0, 100, 20, 30) is True)

# --- analyze_pair_across_runs ---

# Real scenario 1: tasks genuinely never overlap across enough runs -> always_sequential
never_overlapping_runs = [
    {"a": (0, 100), "b": (100, 200)},
    {"a": (1000, 1100), "b": (1100, 1200)},
    {"a": (2000, 2100), "b": (2100, 2200)},
    {"a": (3000, 3100), "b": (3100, 3200)},
]
result = analyze_pair_across_runs(("a", "b"), never_overlapping_runs, min_evidence_runs=3)
check("always_sequential fires when zero overlaps across enough runs", result["status"] == "always_sequential")
check("always_sequential reports overlap_count 0", result["overlap_count"] == 0)

# Real scenario 2: tasks genuinely DO run concurrently at least once -> parallel_confirmed
overlapping_runs = [
    {"a": (0, 200), "b": (50, 150)},
    {"a": (1000, 1200), "b": (1050, 1150)},
    {"a": (2000, 2200), "b": (2050, 2150)},
]
result = analyze_pair_across_runs(("a", "b"), overlapping_runs, min_evidence_runs=3)
check("parallel_confirmed fires when tasks genuinely overlap", result["status"] == "parallel_confirmed")

# Real scenario 3: not enough runs where BOTH tasks have valid timing -> not_enough_runs
sparse_runs = [
    {"a": (0, 100), "b": (100, 200)},
    {"a": (1000, 1100)},  # b missing this run -- shouldn't count as evidence
]
result = analyze_pair_across_runs(("a", "b"), sparse_runs, min_evidence_runs=3)
check("not_enough_runs fires when fewer than the floor have BOTH tasks present", result["status"] == "not_enough_runs")
check("not_enough_runs correctly counts only the 1 run where both tasks had data", result["valid_runs"] == 1)

# --- compute_critical_path ---

# Regression test for the live-found bug: all-zero durations (no run
# history) must still surface the REAL structural chain, not collapse to
# a length-1 or empty chain because of a strict '>' comparison starting
# from a zero baseline.
chain, seconds = compute_critical_path(linear_tasks, {})
check("critical path with NO duration data still returns the full 3-task chain", chain == ["a", "b", "c"])
check("critical path seconds is 0.0 when no duration data exists", seconds == 0.0)

# With real, differentiated durations, the actual longest-duration chain must win,
# not just the longest-by-task-count chain.
branching_tasks = [
    {"task_key": "root", "depends_on": []},
    {"task_key": "short_branch", "depends_on": ["root"]},
    {"task_key": "long_branch", "depends_on": ["root"]},
    {"task_key": "long_branch_child", "depends_on": ["long_branch"]},
]
durations = {"root": 10.0, "short_branch": 500.0, "long_branch": 20.0, "long_branch_child": 20.0}
chain, seconds = compute_critical_path(branching_tasks, durations)
check("critical path picks the real longer-duration branch, not the longer-task-count one",
      chain == ["root", "short_branch"] and seconds == 510.0)

# Two-task job (found live: ml_creation) -- must return the full chain, not just 1 node.
two_task = [
    {"task_key": "first", "depends_on": []},
    {"task_key": "second", "depends_on": ["first"]},
]
chain, seconds = compute_critical_path(two_task, {})
check("2-task linear chain with no duration data returns both tasks (regression for the live bug)",
      chain == ["first", "second"])

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All workflow_dependency_optimizer unit tests passed.")
sys.exit(0)
