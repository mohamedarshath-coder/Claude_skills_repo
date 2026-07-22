#!/usr/bin/env python3
"""
Unit tests for scaling_advisor's pure-logic functions -- summarize_load
and every build_advisory branch. The SQL-based data pulls
(get_load_history, get_warehouse_settings) and the write-confirm/loop
path (apply_scaling_change, verify_change_applied, wait_for_new_load_data)
are proven live against a real account instead.

Run: python test_scaling_advisor.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from scaling_advisor import summarize_load, build_advisory  # noqa: E402

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- summarize_load ---

check("None input (no rows) returns None", summarize_load([]) is None)

rows_no_queue = [
    {"avg_running": 0.5, "avg_queued_load": 0.0, "avg_queued_provisioning": 0.0, "avg_blocked": 0.0},
    {"avg_running": 0.8, "avg_queued_load": 0.0, "avg_queued_provisioning": 0.0, "avg_blocked": 0.0},
]
summary = summarize_load(rows_no_queue)
check("no queueing across all samples yields 0% queued", summary["queued_sample_pct"] == 0.0)
check("max_avg_running_observed picks the real max, not the last value", summary["max_avg_running_observed"] == 0.8)

rows_with_queue = [
    {"avg_running": 1.0, "avg_queued_load": 0.0, "avg_queued_provisioning": 0.0, "avg_blocked": 0.0},
    {"avg_running": 1.0, "avg_queued_load": 2.0, "avg_queued_provisioning": 0.0, "avg_blocked": 0.0},
    {"avg_running": 1.0, "avg_queued_load": 0.0, "avg_queued_provisioning": 1.0, "avg_blocked": 0.0},
    {"avg_running": 1.0, "avg_queued_load": 0.0, "avg_queued_provisioning": 0.0, "avg_blocked": 0.0},
]
summary2 = summarize_load(rows_with_queue)
check("2 of 4 samples had real queueing (queued_load OR queued_provisioning) -> 50%", summary2["queued_sample_pct"] == 50.0)
check("sample count is correct", summary2["samples"] == 4)


# --- build_advisory: every branch ---

def make_settings(min_c=1, max_c=1, policy="STANDARD"):
    return {"name": "TEST_WH", "size": "X-Small", "min_cluster_count": min_c, "max_cluster_count": max_c, "scaling_policy": policy, "state": "STARTED"}


def make_load_summary(queued_pct, max_avg_running, samples=20):
    return {"samples": samples, "queued_sample_count": int(samples * queued_pct / 100), "queued_sample_pct": queued_pct,
            "max_avg_running_observed": max_avg_running, "avg_avg_running": max_avg_running / 2}


check(
    "not_enough_load_history fires when there's no data at all",
    build_advisory(make_settings(), None, min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "not_enough_load_history",
)
check(
    "not_enough_load_history fires when samples are below the floor",
    build_advisory(make_settings(), make_load_summary(0, 0.5, samples=3), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "not_enough_load_history",
)

check(
    "recommend_enable_multi_cluster fires for real queueing on a single-cluster warehouse",
    build_advisory(make_settings(min_c=1, max_c=1), make_load_summary(20.0, 0.9), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "recommend_enable_multi_cluster",
)

check(
    "recommend_scale_up fires for real queueing even with existing multi-cluster headroom",
    build_advisory(make_settings(min_c=1, max_c=3), make_load_summary(20.0, 2.5), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "recommend_scale_up",
)

check(
    "recommend_scale_down fires when spare headroom is real but never exercised",
    build_advisory(make_settings(min_c=1, max_c=4), make_load_summary(0.0, 0.8), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "recommend_scale_down",
)

check(
    "already_well_scaled fires when headroom exists AND is genuinely being used, with no queueing",
    build_advisory(make_settings(min_c=1, max_c=4), make_load_summary(0.0, 3.5), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "already_well_scaled",
)

check(
    "no_action_needed fires when min == max (no spare headroom to evaluate) and no queueing",
    build_advisory(make_settings(min_c=2, max_c=2), make_load_summary(0.0, 1.5), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0)["status"] == "no_action_needed",
)

# Every actionable branch must include a concrete recommended_action.
for status_case, expected_status in [
    (build_advisory(make_settings(min_c=1, max_c=1), make_load_summary(20.0, 0.9), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0), "recommend_enable_multi_cluster"),
    (build_advisory(make_settings(min_c=1, max_c=4), make_load_summary(0.0, 0.8), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0), "recommend_scale_down"),
]:
    check(f"{expected_status} includes a concrete recommended_action", status_case["recommended_action"] is not None)

# Non-actionable branches must NOT include a recommended_action.
for status_case in [
    build_advisory(make_settings(), None, min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0),
    build_advisory(make_settings(min_c=2, max_c=2), make_load_summary(0.0, 1.5), min_samples=10, queue_threshold_pct=5.0, idle_cluster_threshold_pct=50.0),
]:
    check("non-actionable branches have recommended_action == None", status_case["recommended_action"] is None)

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All scaling_advisor unit tests passed.")
sys.exit(0)
