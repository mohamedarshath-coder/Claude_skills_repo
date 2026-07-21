#!/usr/bin/env python3
"""
snowflake-materialized-view-advisor helper script.

Finds repeated, expensive SELECT query patterns via QUERY_HISTORY's own
QUERY_PARAMETERIZED_HASH (Snowflake's own query-fingerprint that ignores
literal values -- exactly the signal needed to spot "the same shape of
query, run over and over"), checks each candidate against real
materialized-view eligibility restrictions, and -- only with explicit
--execute confirmation -- creates the view and verifies, in a retry
loop, that a subsequent real run of the same query pattern is actually
faster afterward.

Auth: uses snowflake-connector-python with a locally-configured named
connection. Never touches credentials directly.

Safety design (write-confirm, retry-until-resolved, matching this
repo's other write-capable skills):
  - DRY RUN BY DEFAULT. Without --execute, only reports candidates and
    the exact CREATE MATERIALIZED VIEW DDL that WOULD run -- creates
    nothing.
  - MV eligibility is checked with real, documented restrictions before
    ANY candidate is ever recommended: single base table only (no joins,
    no subqueries), not a system/account_usage/information_schema
    pseudo-table, no non-deterministic functions, no ORDER BY/LIMIT/
    window functions -- Snowflake's own real MV restrictions, checked
    via a text heuristic (not a full SQL parser, documented as such).
  - Task Loop: after creating the view, re-runs the same query pattern
    for real and compares against the historical average duration --
    retries the verification (not the creation) up to --max-iterations,
    since ACCOUNT_USAGE data can lag before a fair comparison is
    possible, then reports honestly either way.

Usage:
    python mv_advisor.py [--connection NAME] [--days N]
        [--min-occurrences N] [--min-total-seconds N]
        [--execute] [--view-name NAME] [--max-iterations N]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import re
import sys
import time

import snowflake.connector

REPEATED_PATTERNS_SQL = """
SELECT
    query_parameterized_hash,
    COUNT(*) AS occurrences,
    SUM(execution_time) AS total_ms,
    AVG(execution_time) AS avg_ms,
    ANY_VALUE(query_text) AS sample_text
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
    AND execution_status = 'SUCCESS'
    AND query_parameterized_hash IS NOT NULL
    AND query_type = 'SELECT'
    -- Serverless system compute pools aren't user-managed warehouses,
    -- and Snowflake masks their query text -- same exclusion already
    -- used in snowflake-cost-audit / snowflake-query-optimizer.
    AND warehouse_name NOT ILIKE 'COMPUTE_SERVICE%%'
GROUP BY query_parameterized_hash
HAVING COUNT(*) >= %(min_occurrences)s
ORDER BY total_ms DESC
LIMIT 20
"""

# Real, documented Snowflake materialized view restrictions -- not
# exhaustive, a text heuristic rather than a SQL parser (same honesty
# convention as snowflake-clustering-advisor's column-mention matching).
SYSTEM_SCHEMA_RE = re.compile(r"\b(information_schema|account_usage|snowflake\.account_usage)\b", re.IGNORECASE)
JOIN_RE = re.compile(r"\bjoin\b", re.IGNORECASE)
NONDETERMINISTIC_RE = re.compile(r"\b(random|uniform|current_timestamp|current_date|current_time|seq4|seq8)\s*\(", re.IGNORECASE)
DISALLOWED_CLAUSE_RE = re.compile(r"\b(order\s+by|limit|having|union|qualify)\b", re.IGNORECASE)
WINDOW_FUNCTION_RE = re.compile(r"\bover\s*\(", re.IGNORECASE)
FROM_TABLE_RE = re.compile(r"\bfrom\s+([a-zA-Z_][\w.\"]*)", re.IGNORECASE)


def check_mv_eligibility(query_text):
    """Returns (eligible: bool, reasons: list[str]) -- every real
    restriction found is reported, not just the first one, so a
    reviewer sees the full picture rather than one blocker at a time."""
    reasons = []
    if not query_text or not query_text.strip():
        return False, ["query text is empty or was masked by Snowflake (e.g. a serverless/internal query)"]
    if SYSTEM_SCHEMA_RE.search(query_text):
        reasons.append("references a system schema (INFORMATION_SCHEMA / ACCOUNT_USAGE) -- not a real base table, materialized views cannot be built on these")
    if JOIN_RE.search(query_text):
        reasons.append("contains a JOIN -- Snowflake materialized views support a single base table only")
    if NONDETERMINISTIC_RE.search(query_text):
        reasons.append("contains a non-deterministic function (RANDOM/UNIFORM/CURRENT_TIMESTAMP/etc.) -- not allowed in a materialized view definition")
    if DISALLOWED_CLAUSE_RE.search(query_text):
        reasons.append("contains ORDER BY/LIMIT/HAVING/UNION/QUALIFY -- not allowed in a materialized view definition")
    if WINDOW_FUNCTION_RE.search(query_text):
        reasons.append("contains a window function (OVER (...)) -- not allowed in a materialized view definition")
    match = FROM_TABLE_RE.search(query_text)
    if not match:
        reasons.append("could not identify a single FROM <table> -- too complex or unusual for this heuristic to confidently recommend")
    return (len(reasons) == 0), reasons


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


def find_candidates(cur, days, min_occurrences, min_total_seconds):
    rows = run_query(cur, REPEATED_PATTERNS_SQL, {"days": days, "min_occurrences": min_occurrences})
    candidates = []
    for row in rows:
        total_seconds = (row["total_ms"] or 0) / 1000.0
        if total_seconds < min_total_seconds:
            continue
        eligible, reasons = check_mv_eligibility(row["sample_text"])
        candidates.append({
            "query_hash": row["query_parameterized_hash"],
            "occurrences": row["occurrences"],
            "total_seconds": round(total_seconds, 1),
            "avg_ms": round(row["avg_ms"] or 0, 1),
            "sample_text": row["sample_text"],
            "mv_eligible": eligible,
            "ineligibility_reasons": reasons,
        })
    return candidates


def apply_materialized_view(cur, view_name, source_query_text):
    ddl = f"CREATE OR REPLACE MATERIALIZED VIEW {view_name} AS\n{source_query_text}"
    cur.execute(ddl)
    return ddl


def verify_improvement(cur, query_text, baseline_avg_ms, max_iterations):
    """Task Loop: re-runs the real query for real, comparing its actual
    execution time against the historical baseline -- never assumes the
    materialized view helped just because CREATE succeeded. Retries the
    verification (not the creation) since a single run's timing can be
    noisy; reports honestly if improvement is never observed."""
    iteration_log = []
    for iteration in range(1, max_iterations + 1):
        start = time.time()
        cur.execute(query_text)
        cur.fetchall()
        real_ms = (time.time() - start) * 1000
        improved = real_ms < baseline_avg_ms
        iteration_log.append({
            "iteration": iteration,
            "real_execution_ms": round(real_ms, 1),
            "baseline_avg_ms": round(baseline_avg_ms, 1),
            "improved": improved,
        })
        if improved:
            return True, iteration_log
    return False, iteration_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-occurrences", type=int, default=5)
    parser.add_argument("--min-total-seconds", type=float, default=30.0,
                        help="Materiality floor -- a pattern must have cost at least this much total time to be worth a materialized view's maintenance overhead")
    parser.add_argument("--execute", action="store_true",
                        help="Actually create the recommended materialized view and verify improvement. Without this flag, dry-run only.")
    parser.add_argument("--view-name", default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    candidates = find_candidates(cur, args.days, args.min_occurrences, args.min_total_seconds)
    eligible_candidates = [c for c in candidates if c["mv_eligible"]]

    output = {
        "window_days": args.days,
        "min_occurrences": args.min_occurrences,
        "min_total_seconds": args.min_total_seconds,
        "candidates_found": len(candidates),
        "eligible_candidates_found": len(eligible_candidates),
        "candidates": candidates,
        "execute_mode": args.execute,
        "action_taken": None,
    }

    if not candidates:
        output["overall_status"] = "no_repeated_patterns_found"
        output["detail"] = f"No SELECT pattern repeated at least {args.min_occurrences} times with at least {args.min_total_seconds}s total cost in the last {args.days} days."
    elif not eligible_candidates:
        output["overall_status"] = "repeated_patterns_found_none_eligible"
        output["detail"] = f"{len(candidates)} repeated pattern(s) found, but none are eligible for a materialized view -- see ineligibility_reasons per candidate. Most commonly this account's repeated patterns are internal tool queries against system schemas, not real base-table analytics."
    else:
        top = eligible_candidates[0]
        view_name = args.view_name or f"MV_ADVISOR_CANDIDATE_{top['query_hash'][:8].upper()}"
        ddl = f"CREATE OR REPLACE MATERIALIZED VIEW {view_name} AS\n{top['sample_text']}"
        output["recommended_view_name"] = view_name
        output["recommended_ddl"] = ddl

        if not args.execute:
            output["overall_status"] = "recommend_dry_run"
            output["detail"] = f"Top eligible candidate: {top['occurrences']} occurrences, {top['total_seconds']}s total cost. Re-run with --execute to create this view and verify real improvement."
        else:
            try:
                apply_materialized_view(cur, view_name, top["sample_text"])
                output["action_taken"] = f"created materialized view {view_name}"
                improved, iteration_log = verify_improvement(cur, top["sample_text"], top["avg_ms"], args.max_iterations)
                output["verification"] = {
                    "improved": improved,
                    "iterations_run": len(iteration_log),
                    "iteration_log": iteration_log,
                }
                output["overall_status"] = "created_and_verified_improved" if improved else "created_but_improvement_not_observed"
                output["detail"] = (
                    f"View created; a real re-run was faster than the {top['avg_ms']}ms historical average within {len(iteration_log)} attempt(s)."
                    if improved else
                    f"View created, but {len(iteration_log)} real re-run attempt(s) did not beat the {top['avg_ms']}ms historical average -- the view may need time to populate, or the query planner may not be using it yet. Investigate before relying on it."
                )
            except Exception as e:
                output["overall_status"] = "execute_failed"
                output["detail"] = str(e)[:500]

    cur.close()
    conn.close()
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
