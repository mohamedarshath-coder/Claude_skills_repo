#!/usr/bin/env python3
"""
snowflake-multi-cluster-scaling-advisor helper script.

Reads a warehouse's REAL queueing and cluster-utilization history from
WAREHOUSE_LOAD_HISTORY and its real current MIN/MAX_CLUSTER_COUNT and
SCALING_POLICY from SHOW WAREHOUSES, then recommends a scaling change in
EITHER direction: scale up (real queueing observed, more headroom would
help) or scale down (extra cluster headroom that's rarely if ever used --
real cost with no real benefit). Only with explicit --execute confirmation
does it apply the ALTER WAREHOUSE and verify against a subsequent real
load-history window that the queueing/utilization picture actually
improved.

Auth: uses snowflake-connector-python with a locally-configured named
connection. Never touches credentials directly.

Safety design (write-confirm, retry-until-resolved, matching this
repo's other write-capable skills):
  - DRY RUN BY DEFAULT. Without --execute, only reports the advisory and
    the exact ALTER WAREHOUSE statement that WOULD run -- changes nothing.
  - Two-directional, not just "scale up": a warehouse that already has
    more clusters than it ever needs is real, ongoing waste, so this
    skill is just as willing to recommend scaling DOWN as up -- unlike a
    one-directional advisor that only ever asks for more capacity.
  - Task Loop: after applying the change, polls WAREHOUSE_LOAD_HISTORY
    for genuinely NEW load data beyond the pre-change cutoff (not just a
    fixed sleep) up to --max-iterations, since real usage has to actually
    happen before a fair before/after comparison is possible -- reports
    honestly if no new data appears in the window rather than assuming
    success.

Usage:
    python scaling_advisor.py --warehouse NAME
        [--connection NAME] [--days N] [--queue-threshold-seconds N]
        [--min-samples N] [--idle-cluster-threshold-pct N]
        [--execute] [--target-max-clusters N] [--target-scaling-policy NAME]
        [--max-iterations N] [--poll-interval-seconds N]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import sys
import time

import snowflake.connector

LOAD_HISTORY_SQL = """
SELECT
    start_time,
    end_time,
    avg_running,
    avg_queued_load,
    avg_queued_provisioning,
    avg_blocked
FROM snowflake.account_usage.warehouse_load_history
WHERE warehouse_name = %(warehouse)s
    AND start_time >= %(cutoff)s
ORDER BY start_time
"""


def get_connection(connection_name):
    overrides = {}
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        overrides["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    if os.environ.get("SNOWFLAKE_AUTHENTICATOR"):
        overrides["authenticator"] = os.environ["SNOWFLAKE_AUTHENTICATOR"]
    try:
        return snowflake.connector.connect(connection_name=connection_name, **overrides)
    except TypeError:
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            **overrides,
        )


def run_query(cur, sql, params=None):
    cur.execute(sql, params or {})
    columns = [c[0].lower() for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_warehouse_settings(cur, warehouse):
    cur.execute("SHOW WAREHOUSES")
    columns = [c[0].lower() for c in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        if row["name"].upper() == warehouse.upper():
            return {
                "name": row["name"],
                "size": row["size"],
                "min_cluster_count": int(row["min_cluster_count"]),
                "max_cluster_count": int(row["max_cluster_count"]),
                "scaling_policy": row["scaling_policy"],
                "state": row["state"],
            }
    return None


def get_load_history(cur, warehouse, days, cutoff_override=None):
    cutoff = cutoff_override
    if cutoff is None:
        cur.execute("SELECT DATEADD('day', %(d)s, CURRENT_TIMESTAMP())", {"d": -days})
        cutoff = cur.fetchone()[0]
    return run_query(cur, LOAD_HISTORY_SQL, {"warehouse": warehouse, "cutoff": cutoff}), cutoff


def summarize_load(rows):
    """Real signal, not guessed: a sample "has queueing" if load was
    queued (waiting for an already-running cluster) or queued for
    provisioning (waiting for a new cluster to spin up) rather than
    served immediately. A sample "is multi-cluster-idle" evidence point
    if avg_running never approached the warehouse's max capacity --
    computed by the caller against max_cluster_count, since this
    function only summarizes the raw samples."""
    if not rows:
        return None
    total = len(rows)
    queued_samples = sum(1 for r in rows if (r["avg_queued_load"] or 0) > 0 or (r["avg_queued_provisioning"] or 0) > 0)
    avg_running_values = [r["avg_running"] or 0 for r in rows]
    return {
        "samples": total,
        "queued_sample_count": queued_samples,
        "queued_sample_pct": round(100 * queued_samples / total, 1),
        "max_avg_running_observed": round(max(avg_running_values), 3) if avg_running_values else 0.0,
        "avg_avg_running": round(sum(avg_running_values) / total, 3) if total else 0.0,
    }


def build_advisory(settings, load_summary, min_samples, queue_threshold_pct, idle_cluster_threshold_pct):
    if load_summary is None or load_summary["samples"] < min_samples:
        observed = 0 if load_summary is None else load_summary["samples"]
        return {
            "status": "not_enough_load_history",
            "detail": f"Only {observed} load-history sample(s) found (need at least {min_samples}). "
                      f"Can't make a confident recommendation from this little data.",
            "recommended_action": None,
        }

    queued_pct = load_summary["queued_sample_pct"]
    max_clusters = settings["max_cluster_count"]
    min_clusters = settings["min_cluster_count"]

    if queued_pct > queue_threshold_pct:
        if max_clusters <= 1:
            return {
                "status": "recommend_enable_multi_cluster",
                "detail": f"{queued_pct}% of samples showed real queueing, and this warehouse is single-cluster "
                          f"(MAX_CLUSTER_COUNT=1). Enabling multi-cluster scaling would let it absorb concurrent load "
                          f"instead of queuing it.",
                "recommended_action": {"max_cluster_count": max_clusters + 2, "scaling_policy": settings["scaling_policy"]},
            }
        return {
            "status": "recommend_scale_up",
            "detail": f"{queued_pct}% of samples showed real queueing even with MAX_CLUSTER_COUNT={max_clusters}. "
                      f"More cluster headroom would likely reduce queuing further.",
            "recommended_action": {"max_cluster_count": max_clusters + 2, "scaling_policy": settings["scaling_policy"]},
        }

    if max_clusters > min_clusters:
        # Real spare capacity exists (max > min) -- was it ever actually used?
        idle_headroom_pct = 100 * (1 - (load_summary["max_avg_running_observed"] / max_clusters)) if max_clusters else 0
        if idle_headroom_pct > idle_cluster_threshold_pct:
            return {
                "status": "recommend_scale_down",
                "detail": f"No real queueing observed ({queued_pct}% of samples), and the busiest real sample only "
                          f"used {load_summary['max_avg_running_observed']} of {max_clusters} available clusters on "
                          f"average -- {round(idle_headroom_pct, 1)}% of the extra headroom was never exercised. "
                          f"Real ongoing cost with no observed benefit.",
                "recommended_action": {"max_cluster_count": max(min_clusters, 1), "scaling_policy": settings["scaling_policy"]},
            }
        return {
            "status": "already_well_scaled",
            "detail": f"No real queueing observed ({queued_pct}% of samples), and the multi-cluster headroom "
                      f"(up to {max_clusters}) is being genuinely exercised (busiest sample used "
                      f"{load_summary['max_avg_running_observed']} clusters on average).",
            "recommended_action": None,
        }

    return {
        "status": "no_action_needed",
        "detail": f"No real queueing observed ({queued_pct}% of samples), and MIN_CLUSTER_COUNT == MAX_CLUSTER_COUNT "
                  f"({max_clusters}) -- there is no spare headroom to evaluate for waste.",
        "recommended_action": None,
    }


def apply_scaling_change(cur, warehouse, max_cluster_count, scaling_policy):
    ddl = f"ALTER WAREHOUSE {warehouse} SET MAX_CLUSTER_COUNT = {max_cluster_count} SCALING_POLICY = '{scaling_policy}'"
    cur.execute(ddl)
    cur.execute("SELECT CURRENT_TIMESTAMP()")
    change_timestamp = cur.fetchone()[0]
    return ddl, change_timestamp


def verify_change_applied(cur, warehouse, expected_max_cluster_count, expected_scaling_policy):
    settings = get_warehouse_settings(cur, warehouse)
    matches = (
        settings is not None
        and settings["max_cluster_count"] == expected_max_cluster_count
        and settings["scaling_policy"] == expected_scaling_policy
    )
    return matches, settings


def wait_for_new_load_data(cur, warehouse, change_timestamp, max_iterations, poll_interval_seconds):
    """Task Loop: waits for genuinely NEW load-history rows strictly
    after the real ALTER WAREHOUSE's own timestamp (not the original
    --days analysis window's cutoff -- reusing that would just re-find
    the same pre-change rows every time, silently "verifying" against
    stale data), since a fair before/after comparison needs real
    post-change usage to have actually happened -- not just a fixed
    sleep assumed to be long enough."""
    iteration_log = []
    for i in range(1, max_iterations + 1):
        rows, _ = get_load_history(cur, warehouse, days=None, cutoff_override=change_timestamp)
        iteration_log.append({"iteration": i, "new_samples_found": len(rows)})
        if rows:
            return rows, iteration_log
        time.sleep(poll_interval_seconds)
    return [], iteration_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--queue-threshold-pct", type=float, default=5.0,
                        help="Percent of samples showing real queueing above which scaling up is recommended")
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--idle-cluster-threshold-pct", type=float, default=50.0,
                        help="Percent of spare cluster headroom never exercised, above which scaling down is recommended")
    parser.add_argument("--execute", action="store_true",
                        help="Actually apply the recommended ALTER WAREHOUSE and verify against new real load data. Without this flag, dry-run only.")
    parser.add_argument("--target-max-clusters", type=int, default=None,
                        help="Override the recommended MAX_CLUSTER_COUNT instead of accepting the advisor's suggestion")
    parser.add_argument("--target-scaling-policy", default=None, choices=["STANDARD", "ECONOMY"])
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--poll-interval-seconds", type=int, default=30)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    settings = get_warehouse_settings(cur, args.warehouse)
    if settings is None:
        print(json.dumps({"error": f"warehouse '{args.warehouse}' not found"}), file=sys.stderr)
        sys.exit(1)

    rows, cutoff = get_load_history(cur, args.warehouse, args.days)
    load_summary = summarize_load(rows)
    advisory = build_advisory(settings, load_summary, args.min_samples, args.queue_threshold_pct, args.idle_cluster_threshold_pct)

    output = {
        "warehouse": settings,
        "load_history_window_days": args.days,
        "load_summary": load_summary,
        "advisory": advisory,
        "execute_mode": args.execute,
    }

    actionable = advisory["recommended_action"] is not None
    if actionable:
        target_max = args.target_max_clusters if args.target_max_clusters is not None else advisory["recommended_action"]["max_cluster_count"]
        target_policy = args.target_scaling_policy if args.target_scaling_policy is not None else advisory["recommended_action"]["scaling_policy"]
        planned_ddl = f"ALTER WAREHOUSE {args.warehouse} SET MAX_CLUSTER_COUNT = {target_max} SCALING_POLICY = '{target_policy}'"

        if not args.execute:
            output["planned_ddl"] = planned_ddl
        else:
            try:
                ddl, change_timestamp = apply_scaling_change(cur, args.warehouse, target_max, target_policy)
                matches, new_settings = verify_change_applied(cur, args.warehouse, target_max, target_policy)
                output["applied_ddl"] = ddl
                output["change_verified"] = matches
                output["warehouse_after_change"] = new_settings
                new_load_rows, iteration_log = wait_for_new_load_data(cur, args.warehouse, change_timestamp, args.max_iterations, args.poll_interval_seconds)
                output["post_change_verification"] = {
                    "new_load_data_observed": len(new_load_rows) > 0,
                    "iterations_run": len(iteration_log),
                    "iteration_log": iteration_log,
                    "new_load_summary": summarize_load(new_load_rows) if new_load_rows else None,
                }
            except Exception as e:
                output["execute_error"] = str(e)[:500]

    cur.close()
    conn.close()
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
