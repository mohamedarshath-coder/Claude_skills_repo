#!/usr/bin/env python3
"""
snowflake-data-quality helper script.

Runs EXACT data-quality checks against one table (unlike
snowflake-schema-explorer's --profile, which uses approximate distinct
counts): duplicate detection on a key column, null counts per column,
and freshness of date/timestamp columns.

Privacy rule (repo ground rule: no client data/PII in outputs): findings
report COUNTS and AGGREGATES only. Duplicate keys are reported as "N keys
have duplicates, worst key appears M times" -- never the key values
themselves, since a key value can itself be identifying.

Uses snowflake-connector-python with a locally-configured named connection.
Never touches credentials directly.

Usage:
    python data_quality.py --schema NAME --table NAME [--key-column COL]
        [--connection NAME] [--freshness-threshold-days N]

If --key-column is omitted, duplicate checking is skipped (the script
never guesses which column should be unique -- that's a judgment call for
the caller, not a heuristic).

Requires: snowflake-connector-python.
"""
import argparse
import json
import os
import sys

import snowflake.connector

DATE_TYPES = ("DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ")
DEFAULT_FRESHNESS_DAYS = 7


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


def get_columns(cur, schema, table):
    return run_query(cur, """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %(schema)s AND table_name = %(table)s
        ORDER BY ordinal_position
    """, {"schema": schema, "table": table})


def q(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def check_duplicates(cur, schema, table, key_column):
    """Exact duplicate check. Reports counts only -- never key values."""
    rows = run_query(cur, f"""
        SELECT
            COUNT(*) AS total_rows,
            COUNT({q(key_column)}) AS non_null_keys,
            COUNT(DISTINCT {q(key_column)}) AS distinct_keys
        FROM {q(schema)}.{q(table)}
    """)
    stats = rows[0]
    dup_rows = run_query(cur, f"""
        SELECT COUNT(*) AS keys_with_duplicates, MAX(cnt) AS worst_key_occurrences
        FROM (
            SELECT {q(key_column)}, COUNT(*) AS cnt
            FROM {q(schema)}.{q(table)}
            WHERE {q(key_column)} IS NOT NULL
            GROUP BY {q(key_column)}
            HAVING COUNT(*) > 1
        )
    """)[0]
    duplicate_key_count = dup_rows["keys_with_duplicates"] or 0
    return {
        "key_column": key_column,
        "total_rows": stats["total_rows"],
        "non_null_keys": stats["non_null_keys"],
        "distinct_keys": stats["distinct_keys"],
        "duplicate_key_count": duplicate_key_count,
        "excess_rows_from_duplicates": (stats["non_null_keys"] or 0) - (stats["distinct_keys"] or 0),
        "worst_key_occurrences": dup_rows["worst_key_occurrences"],
        "passed": duplicate_key_count == 0,
    }


def check_nulls(cur, schema, table, columns):
    """One aggregate query: exact null count per column."""
    parts = ["COUNT(*) AS total_rows"]
    for col in columns:
        name = col["column_name"]
        parts.append(f"COUNT(*) - COUNT({q(name)}) AS {q(name + '__nulls')}")
    row = run_query(cur, f"SELECT {', '.join(parts)} FROM {q(schema)}.{q(table)}")[0]
    total = row["total_rows"]
    results = []
    for col in columns:
        nulls = row.get(col["column_name"].lower() + "__nulls", 0) or 0
        results.append({
            "column": col["column_name"],
            "null_count": nulls,
            "null_pct": round(100.0 * nulls / total, 2) if total else 0.0,
        })
    return {"total_rows": total, "columns": results,
            "columns_with_nulls": [r for r in results if r["null_count"] > 0]}


# Columns whose latest value being old is expected, not staleness -- a
# birth date SHOULD be old. Matched case-insensitively as substrings.
# Found the hard way: the first live run flagged DATE_OF_BIRTH as "stale"
# at 9328 days, which is semantically absurd.
HISTORICAL_NAME_HINTS = ("birth", "dob")


def check_freshness(cur, schema, table, columns, threshold_days, freshness_columns=None):
    """Latest value in each date/timestamp column vs. now.

    The stale verdict only applies to columns that plausibly represent
    data recency. If --freshness-columns names them explicitly, only
    those are judged; otherwise all date columns are MEASURED (facts)
    but obviously-historical ones (birth dates) get verdict=None with a
    reason instead of a nonsense 'stale' flag.
    """
    date_cols = [c["column_name"] for c in columns if c["data_type"] in DATE_TYPES]
    if not date_cols:
        return {"date_columns_found": 0, "columns": [], "note": "no date/timestamp columns in this table"}
    parts = [f"DATEDIFF('day', MAX({q(c)}), CURRENT_TIMESTAMP()) AS {q(c + '__age')}" for c in date_cols]
    row = run_query(cur, f"SELECT {', '.join(parts)} FROM {q(schema)}.{q(table)}")[0]

    judged = {c.upper() for c in freshness_columns} if freshness_columns else None
    results = []
    for c in date_cols:
        age = row.get(c.lower() + "__age")
        entry = {"column": c, "days_since_latest_value": age}
        if judged is not None and c.upper() not in judged:
            entry["stale"] = None
            entry["verdict_note"] = "measured only -- not named in --freshness-columns"
        elif judged is None and any(hint in c.lower() for hint in HISTORICAL_NAME_HINTS):
            entry["stale"] = None
            entry["verdict_note"] = "measured only -- historical column (a birth date is supposed to be old)"
        else:
            entry["stale"] = age is not None and age > threshold_days
        results.append(entry)
    return {"date_columns_found": len(date_cols), "threshold_days": threshold_days, "columns": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--key-column", default=None)
    parser.add_argument("--freshness-threshold-days", type=int, default=DEFAULT_FRESHNESS_DAYS)
    parser.add_argument("--freshness-columns", nargs="*", default=None,
                        help="Only these date columns get a stale verdict; others are measured only")
    args = parser.parse_args()

    schema, table = args.schema.upper(), args.table.upper()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
        columns = get_columns(cur, schema, table)
        if not columns:
            raise ValueError(f"no columns found for {schema}.{table} -- check name and access")

        output = {
            "schema": schema,
            "table": table,
            "duplicates": check_duplicates(cur, schema, table, args.key_column.upper()) if args.key_column else
                          {"skipped": "no --key-column given; the script never guesses which column should be unique"},
            "nulls": check_nulls(cur, schema, table, columns),
            "freshness": check_freshness(cur, schema, table, columns, args.freshness_threshold_days,
                                         args.freshness_columns),
        }
        cur.close()
        conn.close()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
