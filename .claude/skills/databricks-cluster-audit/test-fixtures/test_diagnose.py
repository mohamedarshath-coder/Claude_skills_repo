#!/usr/bin/env python3
"""
Unit tests for cluster_audit.diagnose() -- specifically the branches that
have never fired against real workspace data (no_autotermination,
large_fixed_size_cluster), plus regression coverage for the branches that
have.

Why unit tests instead of a live scenario: exercising these branches live
would mean creating real clusters in a shared company workspace -- a write
operation with real compute cost, in a workspace other people use. Out of
scope for a read-only skill's verification. These tests prove the branch
logic against controlled inputs; the SKILL.md still honestly notes that
live-workspace confirmation awaits a naturally occurring case.

Run: python test_diagnose.py   (exit 0 = all pass; also picked up by tools/ci/unit_tests.py)
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from cluster_audit import diagnose  # noqa: E402

IDLE_THRESHOLD = 120
LARGE_WORKERS = 10


def make_cluster(source="UI", state="TERMINATED", autoterm=30, num_workers=0, autoscale=None):
    return SimpleNamespace(
        cluster_source=source,
        state=state,
        autotermination_minutes=autoterm,
        num_workers=num_workers,
        autoscale=autoscale,
    )


def issue_names(cluster):
    return [i["issue"] for i in diagnose(cluster, IDLE_THRESHOLD, LARGE_WORKERS)]


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # --- The two previously-never-fired branches ---
    check("no_autotermination fires on UI cluster with autoterm=0",
          "no_autotermination" in issue_names(make_cluster(autoterm=0)))
    check("no_autotermination fires on UI cluster with autoterm=None",
          "no_autotermination" in issue_names(make_cluster(autoterm=None)))
    check("no_autotermination severity is high",
          any(i["issue"] == "no_autotermination" and i["severity"] == "high"
              for i in diagnose(make_cluster(autoterm=0), IDLE_THRESHOLD, LARGE_WORKERS)))
    check("large_fixed_size_cluster fires at exactly the threshold (10 workers, no autoscale)",
          "large_fixed_size_cluster" in issue_names(make_cluster(num_workers=10)))
    check("large_fixed_size_cluster fires above threshold (15 workers)",
          "large_fixed_size_cluster" in issue_names(make_cluster(num_workers=15)))

    # --- Negative cases: the false positives the skill promises never to emit ---
    check("JOB cluster with autoterm=0 is NOT flagged (externally managed lifecycle)",
          "no_autotermination" not in issue_names(make_cluster(source="JOB", autoterm=0)))
    check("PIPELINE cluster with autoterm=0 is NOT flagged",
          "no_autotermination" not in issue_names(make_cluster(source="PIPELINE", autoterm=0)))
    check("large cluster WITH autoscale is NOT flagged as large_fixed_size",
          "large_fixed_size_cluster" not in issue_names(
              make_cluster(num_workers=None, autoscale=SimpleNamespace(min_workers=2, max_workers=20))))
    check("9 fixed workers (below threshold) is NOT flagged",
          "large_fixed_size_cluster" not in issue_names(make_cluster(num_workers=9)))

    # --- Regression coverage for the branches already live-proven ---
    check("permits_long_idle_billing fires on UI cluster above idle threshold",
          "permits_long_idle_billing" in issue_names(make_cluster(autoterm=4320)))
    check("currently_running fires on a RUNNING cluster",
          "currently_running" in issue_names(make_cluster(state="RUNNING")))
    check("healthy UI cluster (autoterm=30, 0 workers) has zero issues",
          issue_names(make_cluster()) == [])

    print(f"\n{len(failures)} failure(s) of 12 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
