#!/usr/bin/env python3
"""
snowflake-query-optimizer helper script.

Analyzes the slowest queries in a window using QUERY_HISTORY's own
diagnostic columns (no need for GET_QUERY_OPERATOR_STATS, which requires
parsing a per-operator VARIANT tree) and flags concrete, evidence-backed
issues: disk spilling, poor partition pruning, and warehouse queueing.

Uses snowflake-connector-python with a locally-configured named connection
(~/.snowflake/connections.toml, or SNOWFLAKE_* env vars). Never touches
credentials directly.

Usage:
    python query_optimizer.py [--connection NAME] [--days N] [--limit N] [--query-id ID]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import sys

import snowflake.connector

# Heuristic thresholds -- documented here since they're the actual
# "what counts as a problem" logic a reviewer needs to see.
POOR_PRUNING_RATIO = 0.5        # scanning >50% of a table's partitions
POOR_PRUNING_MIN_PARTITIONS = 10  # ignore tiny tables -- ratio is noisy
QUEUEING_THRESHOLD_MS = 1000     # >1s queued waiting for warehouse capacity

SLOW_QUERIES_SQL = """
SELECT
    query_id,
    warehouse_name,
    warehouse_size,
    LEFT(query_text, 200) AS query_text_preview,
    execution_time,
    compilation_time,
    partitions_scanned,
    partitions_total,
    bytes_spilled_to_local_storage,
    bytes_spilled_to_remote_storage,
    bytes_scanned,
    queued_provisioning_time,
    queued_overload_time,
    queued_repair_time
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
    AND execution_status = 'SUCCESS'
    AND execution_time > 0
    -- Serverless system compute pools (tasks, Snowpipe, etc.) aren't
    -- user-managed warehouses -- nothing here is actionable by the
    -- person running this skill, so they're excluded from the default view.
    AND warehouse_name NOT ILIKE 'COMPUTE_SERVICE_WH%%'
ORDER BY execution_time DESC
LIMIT %(scan_limit)s
"""

SINGLE_QUERY_SQL = """
SELECT
    query_id,
    warehouse_name,
    warehouse_size,
    LEFT(query_text, 200) AS query_text_preview,
    execution_time,
    compilation_time,
    partitions_scanned,
    partitions_total,
    bytes_spilled_to_local_storage,
    bytes_spilled_to_remote_storage,
    bytes_scanned,
    queued_provisioning_time,
    queued_overload_time,
    queued_repair_time
FROM snowflake.account_usage.query_history
WHERE query_id = %(query_id)s
"""


def diagnose(row):
    """Turn raw QUERY_HISTORY columns into a list of concrete, evidence-backed issues."""
    issues = []

    local_spill = row.get("bytes_spilled_to_local_storage") or 0
    remote_spill = row.get("bytes_spilled_to_remote_storage") or 0
    if remote_spill > 0:
        issues.append({
            "issue": "spilling_to_remote_storage",
            "severity": "high",
            "evidence": f"{remote_spill} bytes spilled to remote storage",
            "recommendation": "Warehouse is undersized for this query's working set -- increase warehouse size, or reduce the data volume processed (filter earlier, avoid a full unfiltered join).",
        })
    elif local_spill > 0:
        issues.append({
            "issue": "spilling_to_local_storage",
            "severity": "medium",
            "evidence": f"{local_spill} bytes spilled to local disk",
            "recommendation": "Query needs more memory than the warehouse provides. Consider a larger warehouse size, or reduce intermediate result size (push filters/aggregations earlier).",
        })

    partitions_total = row.get("partitions_total") or 0
    partitions_scanned = row.get("partitions_scanned") or 0
    if partitions_total >= POOR_PRUNING_MIN_PARTITIONS:
        ratio = partitions_scanned / partitions_total
        if ratio > POOR_PRUNING_RATIO:
            issues.append({
                "issue": "poor_partition_pruning",
                "severity": "medium",
                "evidence": f"scanned {partitions_scanned} of {partitions_total} partitions ({ratio:.0%})",
                "recommendation": "Query is scanning most of the table. Check whether the WHERE clause filters on the table's clustering key, or consider re-clustering on the columns this query filters by.",
            })

    queued_overload = row.get("queued_overload_time") or 0
    if queued_overload > QUEUEING_THRESHOLD_MS:
        issues.append({
            "issue": "warehouse_queueing",
            "severity": "medium",
            "evidence": f"{queued_overload} ms queued waiting for warehouse capacity",
            "recommendation": "Warehouse was at capacity when this query ran. Consider multi-cluster warehouse scaling, or isolating this workload onto its own warehouse.",
        })

    queued_provisioning = row.get("queued_provisioning_time") or 0
    if queued_provisioning > QUEUEING_THRESHOLD_MS:
        issues.append({
            "issue": "cold_start_provisioning",
            "severity": "low",
            "evidence": f"{queued_provisioning} ms spent provisioning the warehouse before this query could start",
            "recommendation": "Warehouse was suspended and had to spin up. If this query runs on a schedule, consider a longer AUTO_SUSPEND or a warm-up query beforehand.",
        })

    return issues


def rank_results(all_results, limit):
    """Ranks diagnosed queries issues-first, duration second. Found live
    2026-07-16: a real spilling query (1.5s) ranked outside a
    top-10-by-duration window where the slowest query ran 13.8s, and was
    missed entirely -- the SQL-level LIMIT happened before diagnosis ever
    ran. Fixed at the caller by scanning a wider pool (--scan-limit)
    before this function ever sees the rows; this function then ranks
    the diagnosed results so that EVERY flagged query is always included
    in the final output, never truncated away by --limit -- only the
    issue-free queries shown for context are capped."""
    with_issues = [r for r in all_results if r["issues"]]
    without_issues = [r for r in all_results if not r["issues"]]
    results = with_issues + without_issues[:max(0, limit - len(with_issues))]
    results.sort(key=lambda r: (not r["issues"], -r["execution_ms"]))
    return results


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


def run_query(cur, sql, params):
    cur.execute(sql, params)
    columns = [c[0].lower() for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=10,
                        help="Max number of ISSUE-FREE queries to include for context (every flagged query is always included, never dropped)")
    parser.add_argument("--scan-limit", type=int, default=50,
                        help="How many of the slowest queries to fetch and diagnose before ranking -- wider than --limit so a real issue on a moderate-duration query isn't silently missed")
    parser.add_argument("--query-id", default=None)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
        if args.query_id:
            rows = run_query(cur, SINGLE_QUERY_SQL, {"query_id": args.query_id})
        else:
            rows = run_query(cur, SLOW_QUERIES_SQL, {"days": args.days, "scan_limit": args.scan_limit})
        cur.close()
        conn.close()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    all_results = []
    for row in rows:
        issues = diagnose(row)
        all_results.append({
            "query_id": row["query_id"],
            "warehouse_name": row["warehouse_name"],
            "warehouse_size": row["warehouse_size"],
            "query_text_preview": row.get("query_text_preview"),
            "execution_ms": row["execution_time"],
            "issues": issues,
        })

    results = rank_results(all_results, args.limit)

    output = {
        "window_days": args.days if not args.query_id else None,
        "scan_limit": args.scan_limit if not args.query_id else None,
        "analyzed_query_count": len(all_results),
        "queries_with_issues": sum(1 for r in all_results if r["issues"]),
        "results": results,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
