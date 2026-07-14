#!/usr/bin/env python3
"""
snowflake-schema-explorer helper script.

Two modes:
  1. Overview (no --table): lists every table/view in the database (or a
     single --schema if given), with row count, size, and type -- a
     quick data dictionary of "what exists here."
  2. Detail (--schema + --table): describes one table's columns (name,
     type, nullable, default), and with --profile also runs a single
     aggregate query per table computing null count and approximate
     distinct count for every column -- real profiling, not just schema.

Uses snowflake-connector-python with a locally-configured named connection
(~/.snowflake/connections.toml, or SNOWFLAKE_* env vars). Never touches
credentials directly.

Usage:
    python schema_explorer.py [--connection NAME] [--schema NAME] [--table NAME] [--profile]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import sys

import snowflake.connector

TABLES_OVERVIEW_SQL = """
SELECT
    table_schema,
    table_name,
    table_type,
    row_count,
    bytes
FROM information_schema.tables
WHERE table_schema != 'INFORMATION_SCHEMA'
{schema_filter}
ORDER BY table_schema, table_name
"""

COLUMNS_SQL = """
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = %(schema)s AND table_name = %(table)s
ORDER BY ordinal_position
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


def profile_columns(cur, schema, table, columns):
    """One aggregate query for the whole table: total row count, plus
    null count and approx distinct count per column. Views work too --
    these are just SELECT aggregates, not table-metadata lookups."""
    select_parts = ["COUNT(*) AS total_rows"]
    for col in columns:
        name = col["column_name"]
        safe = name.replace('"', '""')
        select_parts.append(f'COUNT(*) - COUNT("{safe}") AS "{safe}__null_count"')
        select_parts.append(f'APPROX_COUNT_DISTINCT("{safe}") AS "{safe}__distinct_count"')

    sql = f'SELECT {", ".join(select_parts)} FROM "{schema}"."{table}"'
    rows = run_query(cur, sql)
    if not rows:
        return None
    row = rows[0]

    total_rows = row.get("total_rows", 0)
    profile = {"total_rows": total_rows, "columns": {}}
    for col in columns:
        name = col["column_name"]
        # run_query() lowercases every result-set key (from cursor.description),
        # but the alias itself was built from the original-case column name --
        # look up using name.lower() to match, or every value comes back None.
        key = name.lower()
        profile["columns"][name] = {
            "null_count": row.get(f"{key}__null_count"),
            "distinct_count": row.get(f"{key}__distinct_count"),
        }
    return profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--schema", default=None)
    parser.add_argument("--table", default=None)
    parser.add_argument("--profile", action="store_true", help="Also compute null/distinct counts per column (detail mode only)")
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()

        if args.table:
            if not args.schema:
                raise ValueError("--table requires --schema")
            columns = run_query(cur, COLUMNS_SQL, {"schema": args.schema.upper(), "table": args.table.upper()})
            if not columns:
                output = {"error": f"No columns found for {args.schema}.{args.table} -- check the schema/table name and access."}
            else:
                output = {
                    "mode": "detail",
                    "schema": args.schema,
                    "table": args.table,
                    "columns": columns,
                    "profile": profile_columns(cur, args.schema.upper(), args.table.upper(), columns) if args.profile else None,
                }
        else:
            schema_filter = ""
            params = {}
            if args.schema:
                schema_filter = "AND table_schema = %(schema)s"
                params["schema"] = args.schema.upper()
            tables = run_query(cur, TABLES_OVERVIEW_SQL.format(schema_filter=schema_filter), params)
            output = {"mode": "overview", "table_count": len(tables), "tables": tables}

        cur.close()
        conn.close()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
