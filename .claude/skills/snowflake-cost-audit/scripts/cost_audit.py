#!/usr/bin/env python3
"""
snowflake-cost-audit helper script.

Pre-aggregates warehouse cost data via SQL (server-side) so only a small,
pre-filtered JSON payload reaches the agent -- never raw ACCOUNT_USAGE rows.

Uses snowflake-connector-python with a locally-configured named connection
(~/.snowflake/connections.toml, or SNOWFLAKE_* env vars as a fallback).
Never touches or stores credentials directly -- the connection profile is
each user's own local setup.

Degrades gracefully: each data section is fetched independently, so a
permissions gap on one view (e.g. QUERY_ATTRIBUTION_HISTORY) drops that
section into `errors` instead of aborting the whole audit.

Note: ACCOUNT_USAGE views lag reality -- up to ~45 min for QUERY_HISTORY
and up to ~3 h for metering. The JSON output carries this caveat so the
report can surface it.

Usage:
    python cost_audit.py [--connection NAME] [--days N]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import sys

import snowflake.connector

# Scope note: WAREHOUSE_METERING_HISTORY covers user-managed warehouses only.
# Serverless features (tasks, Snowpipe, etc.) bill separately and are NOT
# included in this total -- the output labels the number accordingly.
WAREHOUSE_CREDITS_SQL = """
SELECT
    warehouse_name,
    DATE_TRUNC('day', start_time) AS usage_day,
    SUM(credits_used) AS credits_used
FROM snowflake.account_usage.warehouse_metering_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
GROUP BY 1, 2
ORDER BY 1, 2
"""

QUERY_ACTIVITY_SQL = """
SELECT
    warehouse_name,
    AVG(execution_time) / 1000.0 AS avg_query_seconds,
    COUNT(*) AS query_count
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
GROUP BY 1
"""

# Real per-query credit attribution (compute only) -- ranks by actual cost,
# not duration. Joined to QUERY_HISTORY for a text preview where visible.
TOP_QUERIES_BY_COST_SQL = """
SELECT
    a.query_id,
    a.warehouse_name,
    a.credits_attributed_compute,
    q.execution_time / 1000.0 AS execution_seconds,
    LEFT(q.query_text, 120) AS query_text_preview
FROM snowflake.account_usage.query_attribution_history a
LEFT JOIN snowflake.account_usage.query_history q
    ON a.query_id = q.query_id
WHERE a.start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
ORDER BY a.credits_attributed_compute DESC NULLS LAST
LIMIT 10
"""

# Fallback if attribution view is not readable by this role: rank by
# duration and say so honestly (the output flags which ranking was used).
TOP_QUERIES_BY_DURATION_SQL = """
SELECT
    query_id,
    warehouse_name,
    LEFT(query_text, 120) AS query_text_preview,
    execution_time / 1000.0 AS execution_seconds
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
    AND execution_status = 'SUCCESS'
ORDER BY execution_time DESC
LIMIT 10
"""


def get_connection(connection_name):
    """
    Connect using a named profile from ~/.snowflake/connections.toml
    (account, user, role, warehouse, database, schema) merged at runtime
    with credentials pulled from environment variables -- the password
    itself is never written to connections.toml or committed anywhere.
    Never accepts a password/credential on the command line.
    """
    overrides = {}
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        overrides["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    if os.environ.get("SNOWFLAKE_AUTHENTICATOR"):
        overrides["authenticator"] = os.environ["SNOWFLAKE_AUTHENTICATOR"]

    try:
        return snowflake.connector.connect(connection_name=connection_name, **overrides)
    except TypeError:
        # Older connector versions without connection_name support.
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            **overrides,
        )


def run_query(cur, sql, params):
    cur.execute(sql, params)
    columns = [c[0].lower() for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_warehouse_config(cur):
    """
    Warehouse size/auto_suspend config isn't exposed via ACCOUNT_USAGE --
    SHOW WAREHOUSES is the correct source and works for any role with
    USAGE on the warehouse, no ACCOUNT_USAGE grant required.
    """
    rows = run_query(cur, "SHOW WAREHOUSES", {})
    return [
        {
            "warehouse_name": r["name"],
            "warehouse_size": r["size"],
            "auto_suspend": int(r["auto_suspend"]) if r.get("auto_suspend") not in (None, "") else None,
            "auto_resume": r.get("auto_resume"),
        }
        for r in rows
    ]


def detect_cost_anomalies(credits_rows, threshold_pct=50, min_anomaly_credits=0.05):
    """
    Flags a warehouse whose most recent day of credit usage is more than
    `threshold_pct` above its trailing average over the rest of the window.
    This is the signal a future scheduled (product-loop) version of this
    skill would use to decide whether to speak at all -- see the Loop Tier
    section in SKILL.md.

    `min_anomaly_credits` is a deliberate materiality floor, applied from
    the start (not deferred as originally planned): a warehouse going
    from 0.00003 to 0.00006 credits is a real ">50% increase" by the
    math, but both numbers are financially meaningless -- found live
    during testing (2026-07-15) and closed here using the same pattern
    already proven in aws-cost-report's detect_service_anomalies.
    """
    by_warehouse = {}
    for row in credits_rows:
        by_warehouse.setdefault(row["warehouse_name"], []).append(row)

    anomalies = []
    for wh, rows in by_warehouse.items():
        rows_sorted = sorted(rows, key=lambda r: str(r["usage_day"]))
        if len(rows_sorted) < 2:
            continue
        *prior, latest = rows_sorted
        prior_values = [float(r["credits_used"] or 0) for r in prior]
        trailing_avg = sum(prior_values) / len(prior_values) if prior_values else 0
        latest_value = float(latest["credits_used"] or 0)
        if latest_value < min_anomaly_credits:
            continue
        if trailing_avg > 0 and latest_value > trailing_avg * (1 + threshold_pct / 100):
            pct_increase = round(((latest_value / trailing_avg) - 1) * 100, 1)
            anomalies.append({
                "warehouse": wh,
                "latest_day": str(latest["usage_day"]),
                "latest_credits": round(latest_value, 3),
                "trailing_avg_credits": round(trailing_avg, 3),
                "pct_increase": pct_increase,
            })
    return anomalies


def flag_idle_or_oversized(config_rows, activity_rows):
    activity_by_wh = {r["warehouse_name"]: r for r in activity_rows}
    flags = []
    for wh in config_rows:
        name = wh["warehouse_name"]
        activity = activity_by_wh.get(name)
        auto_suspend = wh.get("auto_suspend")

        if auto_suspend is None or auto_suspend > 600:
            flags.append({
                "warehouse": name,
                "issue": "auto_suspend_high_or_disabled",
                "auto_suspend_seconds": auto_suspend,
            })

        big_sizes = ("LARGE", "X-LARGE", "2X-LARGE", "3X-LARGE", "4X-LARGE")
        if activity and activity.get("avg_query_seconds", 0) < 15 and wh.get("warehouse_size") in big_sizes:
            flags.append({
                "warehouse": name,
                "issue": "possibly_oversized",
                "warehouse_size": wh.get("warehouse_size"),
                "avg_query_seconds": activity.get("avg_query_seconds"),
            })
    return flags


def fetch_section(errors, section_name, fetch_fn):
    """Run one data fetch; on failure record the error and return None
    so the rest of the audit still completes (graceful degradation)."""
    try:
        return fetch_fn()
    except Exception as e:
        errors[section_name] = str(e)[:300]
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-anomaly-credits", type=float, default=0.05,
                        help="Minimum latest-day credits for a warehouse to be eligible as a cost anomaly")
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    params = {"days": args.days}
    errors = {}

    credits_rows = fetch_section(errors, "warehouse_credits",
                                 lambda: run_query(cur, WAREHOUSE_CREDITS_SQL, params))
    config_rows = fetch_section(errors, "warehouse_config",
                                lambda: get_warehouse_config(cur))
    activity_rows = fetch_section(errors, "query_activity",
                                  lambda: run_query(cur, QUERY_ACTIVITY_SQL, params))

    # Prefer real credit attribution; fall back to duration ranking and
    # record which one was used so the report never mislabels the metric.
    top_queries = fetch_section(errors, "top_queries_by_cost",
                                lambda: run_query(cur, TOP_QUERIES_BY_COST_SQL, params))
    if top_queries is not None:
        top_queries_ranking = "credits_attributed_compute"
    else:
        top_queries = fetch_section(errors, "top_queries_by_duration",
                                    lambda: run_query(cur, TOP_QUERIES_BY_DURATION_SQL, params))
        top_queries_ranking = "execution_time (duration only -- attribution view not accessible)"

    cur.close()
    conn.close()

    total_credits = sum(float(r.get("credits_used", 0) or 0) for r in (credits_rows or []))
    flags = flag_idle_or_oversized(config_rows or [], activity_rows or [])
    anomalies = detect_cost_anomalies(credits_rows or [], min_anomaly_credits=args.min_anomaly_credits)

    output = {
        "window_days": args.days,
        "min_anomaly_credits": args.min_anomaly_credits,
        "scope_note": "Totals cover user-managed warehouses only (WAREHOUSE_METERING_HISTORY). Serverless features (tasks, Snowpipe, etc.) bill separately and are not included.",
        "data_latency_note": "ACCOUNT_USAGE views lag real time: up to ~45 min for query history, up to ~3 h for metering.",
        "total_warehouse_credits_used": round(total_credits, 2),
        "credits_by_warehouse_by_day": credits_rows or [],
        "flagged_warehouses": flags,
        "cost_anomalies": anomalies,
        "top_queries_ranking": top_queries_ranking,
        "top_queries": top_queries or [],
        "errors": errors,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
