#!/usr/bin/env python3
"""
Unit tests for query_optimizer.diagnose() -- the four issue-detection
branches (remote/local spill, poor pruning, queueing, cold-start) have
never fired against real account data: every real query seen so far has
been clean (which proved the no-false-positives path, but left every
positive-detection path unexecuted).

Why unit tests instead of a live trigger: forcing a genuine disk spill on
the real X-Small warehouse means deliberately running a huge cartesian
join that burns real credits; and even cheap triggers can't be verified
in-session because ACCOUNT_USAGE.QUERY_HISTORY lags up to ~45 minutes.

Run: python test_diagnose.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from query_optimizer import diagnose, rank_results  # noqa: E402


def make_row(**overrides):
    row = {
        "bytes_spilled_to_local_storage": 0,
        "bytes_spilled_to_remote_storage": 0,
        "partitions_scanned": 0,
        "partitions_total": 0,
        "queued_overload_time": 0,
        "queued_provisioning_time": 0,
    }
    row.update(overrides)
    return row


def issue_names(row):
    return [i["issue"] for i in diagnose(row)]


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # --- The four previously-never-fired positive branches ---
    check("remote spill fires (high severity)",
          any(i["issue"] == "spilling_to_remote_storage" and i["severity"] == "high"
              for i in diagnose(make_row(bytes_spilled_to_remote_storage=52428800))))
    check("local spill fires when no remote spill (medium severity)",
          any(i["issue"] == "spilling_to_local_storage" and i["severity"] == "medium"
              for i in diagnose(make_row(bytes_spilled_to_local_storage=1048576))))
    check("remote spill takes precedence over local (only one spill issue emitted)",
          issue_names(make_row(bytes_spilled_to_remote_storage=100, bytes_spilled_to_local_storage=100))
          == ["spilling_to_remote_storage"])
    check("poor pruning fires at 82% scanned of 22410 partitions",
          "poor_partition_pruning" in issue_names(make_row(partitions_scanned=18320, partitions_total=22410)))
    check("warehouse queueing fires above 1000ms overload",
          "warehouse_queueing" in issue_names(make_row(queued_overload_time=5000)))
    check("cold-start provisioning fires above 1000ms (low severity)",
          any(i["issue"] == "cold_start_provisioning" and i["severity"] == "low"
              for i in diagnose(make_row(queued_provisioning_time=3000))))

    # --- Boundary/negative cases: the false positives the skill must not emit ---
    check("pruning NOT flagged on tiny table (9 partitions, below min)",
          "poor_partition_pruning" not in issue_names(make_row(partitions_scanned=9, partitions_total=9)))
    check("pruning NOT flagged at exactly 50% (threshold is strictly greater-than)",
          "poor_partition_pruning" not in issue_names(make_row(partitions_scanned=50, partitions_total=100)))
    check("pruning fires just above 50% on a big-enough table",
          "poor_partition_pruning" in issue_names(make_row(partitions_scanned=51, partitions_total=100)))
    check("queueing NOT flagged at exactly 1000ms (threshold is strictly greater-than)",
          "warehouse_queueing" not in issue_names(make_row(queued_overload_time=1000)))
    check("None values treated as zero, not a crash",
          issue_names(make_row(bytes_spilled_to_local_storage=None, queued_overload_time=None,
                               partitions_scanned=None, partitions_total=None)) == [])
    check("fully clean query emits zero issues",
          issue_names(make_row(partitions_scanned=261, partitions_total=101039)) == [])

    # --- Compound case: a genuinely bad query trips multiple branches at once ---
    bad = make_row(bytes_spilled_to_remote_storage=999999, partitions_scanned=90, partitions_total=100,
                   queued_overload_time=4000, queued_provisioning_time=2000)
    check("a bad query can carry all four issue types simultaneously",
          set(issue_names(bad)) == {"spilling_to_remote_storage", "poor_partition_pruning",
                                    "warehouse_queueing", "cold_start_provisioning"})

    # --- rank_results: the top-10-by-duration coverage gap, found live 2026-07-16 ---
    # A real spilling query (1.5s) ranked outside a top-10-by-duration
    # window where the slowest query ran 13.8s, and was completely
    # missed by the old "SQL LIMIT before diagnosis" design.
    def r(qid, ms, issues):
        return {"query_id": qid, "execution_ms": ms, "issues": issues}

    finding = [{"issue": "spilling_to_local_storage", "severity": "medium", "evidence": "x", "recommendation": "y"}]
    pool = [r("slow_clean_1", 13800, []), r("slow_clean_2", 9000, []), r("slow_clean_3", 8000, []),
            r("modest_but_flagged", 1500, finding)]
    ranked = rank_results(pool, limit=3)
    check("REGRESSION: a flagged query ranked outside the duration cutoff is still included",
          any(x["query_id"] == "modest_but_flagged" for x in ranked))
    check("flagged query is ranked ABOVE clean queries despite lower duration",
          ranked[0]["query_id"] == "modest_but_flagged")

    many_issues_pool = [r(f"bad_{i}", 100 - i, finding) for i in range(15)]
    ranked_many = rank_results(many_issues_pool, limit=3)
    check("every flagged query is included even when there are more issues than --limit",
          len(ranked_many) == 15)

    clean_pool = [r(f"clean_{i}", 100 - i, []) for i in range(10)]
    ranked_clean = rank_results(clean_pool, limit=3)
    check("issue-free results are capped at --limit when nothing is flagged",
          len(ranked_clean) == 3)
    check("issue-free results still favor higher duration first",
          [x["query_id"] for x in ranked_clean] == ["clean_0", "clean_1", "clean_2"])

    mixed_pool = [r("bad_1", 50, finding), r("clean_a", 200, []), r("clean_b", 150, []), r("clean_c", 100, [])]
    ranked_mixed = rank_results(mixed_pool, limit=2)
    check("with 1 flagged query and limit=2, exactly 1 clean query fills the remaining slot",
          len(ranked_mixed) == 2 and ranked_mixed[0]["query_id"] == "bad_1" and ranked_mixed[1]["query_id"] == "clean_a")

    print(f"\n{len(failures)} failure(s) of 18 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
