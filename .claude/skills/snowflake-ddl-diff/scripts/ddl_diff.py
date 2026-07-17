#!/usr/bin/env python3
"""
snowflake-ddl-diff helper script.

Compares two schemas' real table/column structure (INFORMATION_SCHEMA,
not a hand-maintained spec) and, only when --execute is explicitly
passed, generates and applies SAFE, additive migration DDL to bring the
target schema in line with the source -- then re-diffs and repeats until
converged or a hard retry cap is hit.

Safety design (this is the repo's only write-confirm, retry-until-resolved
skill, and the only skill that executes DDL at all):

  - DRY RUN BY DEFAULT. Without --execute, this only ever reports the
    diff and the exact DDL it WOULD run -- it never touches the target
    schema. --execute must be passed explicitly and separately, every
    single invocation; there is no "confirm once, run forever" mode.
  - ONLY ADDITIVE DDL IS EVER AUTO-GENERATED: CREATE TABLE for a table
    missing in the target, ALTER TABLE ... ADD COLUMN for a column
    missing in the target. Nothing else is ever auto-generated.
  - NEVER AUTO-GENERATED, EVER: DROP TABLE, DROP COLUMN, or ALTER COLUMN
    TYPE. A type mismatch or an extra object in the target (present in
    target but not source) is always reported as "manual_review_required"
    and is NEVER touched by this script, --execute or not -- dropping or
    retyping something automatically from a diff alone is exactly the
    kind of destructive, hard-to-reverse action this repo's rules
    require a human to decide, not a loop.
  - Hard retry cap (--max-iterations, default 5) per
    .claude/rules/loop-engineering.md -- well above the 1-2 iterations
    real convergence should take, so hitting it is itself a signal
    something is wrong (e.g. a permissions issue silently no-op'ing
    every ALTER TABLE).
  - Each retry only proceeds if the previous iteration actually changed
    something (applied at least one statement) -- if a round finds zero
    safely-fixable differences, the loop stops immediately, converged.
  - Full audit log: every DDL statement this script ever executes is
    recorded with a real timestamp and its real outcome (success or the
    real error), success or failure, returned in the output -- an
    unattended write-capable loop must be auditable after the fact.

Auth: uses snowflake-connector-python with a locally-configured named
connection (~/.snowflake/connections.toml, or SNOWFLAKE_* env vars).
Never touches credentials directly.

Usage:
    python ddl_diff.py --source-schema NAME --target-schema NAME
        [--connection NAME] [--execute] [--max-iterations N]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import datetime
import json
import os
import sys

import snowflake.connector

MAX_ITERATIONS_DEFAULT = 5

COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable, character_maximum_length,
       numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_schema = %(schema)s
ORDER BY table_name, ordinal_position
"""

TABLES_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = %(schema)s AND table_type = 'BASE TABLE'
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


def run_query(cur, sql, params):
    cur.execute(sql, params)
    columns = [c[0].lower() for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def normalize_type(col):
    """A single comparable type signature -- data_type alone isn't enough
    (VARCHAR(50) and VARCHAR(200) are both 'TEXT' in Snowflake's own
    reporting, so length/precision/scale must be compared too)."""
    return (
        col["data_type"],
        col.get("character_maximum_length"),
        col.get("numeric_precision"),
        col.get("numeric_scale"),
    )


def full_type_ddl(col):
    """Reconstructs a real, usable type clause for DDL generation --
    not just the bare data_type, which loses precision/length info."""
    dt = col["data_type"]
    if dt in ("TEXT", "VARCHAR", "STRING", "CHAR") and col.get("character_maximum_length"):
        return f"VARCHAR({col['character_maximum_length']})"
    if dt in ("NUMBER", "NUMERIC", "DECIMAL") and col.get("numeric_precision") is not None:
        scale = col.get("numeric_scale") or 0
        return f"NUMBER({col['numeric_precision']},{scale})"
    return dt


def fetch_schema_snapshot(cur, schema):
    tables = {r["table_name"] for r in run_query(cur, TABLES_SQL, {"schema": schema})}
    columns_by_table = {}
    for row in run_query(cur, COLUMNS_SQL, {"schema": schema}):
        columns_by_table.setdefault(row["table_name"], {})[row["column_name"]] = row
    return tables, columns_by_table


def compute_diff(source_tables, source_cols, target_tables, target_cols, target_schema):
    """Returns (safe_actions, manual_review) -- safe_actions are the only
    things this script will ever auto-generate DDL for; manual_review
    items are reported but NEVER acted on automatically, by design.

    Every generated statement is fully schema-qualified with the REAL
    target schema name -- found live: an unqualified `CREATE TABLE "X"`
    resolves against the connection's current default schema, not the
    intended target, and silently creates the object in the wrong place
    with no error at all. Never omit the schema qualifier again."""
    safe_actions = []
    manual_review = []

    missing_tables = sorted(source_tables - target_tables)
    for table in missing_tables:
        cols = source_cols.get(table, {})
        col_defs = ", ".join(f'"{c}" {full_type_ddl(col)}' for c, col in cols.items())
        safe_actions.append({
            "type": "create_table",
            "table": table,
            "ddl": f'CREATE TABLE "{target_schema}"."{table}" ({col_defs})',
        })

    extra_tables = sorted(target_tables - source_tables)
    for table in extra_tables:
        manual_review.append({
            "type": "extra_table_in_target",
            "table": table,
            "detail": f"Table '{table}' exists in the target but not the source -- never auto-dropped. Confirm with the table owner before deciding whether to remove it.",
        })

    common_tables = sorted(source_tables & target_tables)
    for table in common_tables:
        s_cols = source_cols.get(table, {})
        t_cols = target_cols.get(table, {})

        missing_cols = sorted(set(s_cols) - set(t_cols))
        for col_name in missing_cols:
            col = s_cols[col_name]
            safe_actions.append({
                "type": "add_column",
                "table": table,
                "column": col_name,
                "ddl": f'ALTER TABLE "{target_schema}"."{table}" ADD COLUMN "{col_name}" {full_type_ddl(col)}',
            })

        extra_cols = sorted(set(t_cols) - set(s_cols))
        for col_name in extra_cols:
            manual_review.append({
                "type": "extra_column_in_target",
                "table": table,
                "column": col_name,
                "detail": f"Column '{table}.{col_name}' exists in the target but not the source -- never auto-dropped. Confirm before removing.",
            })

        for col_name in sorted(set(s_cols) & set(t_cols)):
            s_sig = normalize_type(s_cols[col_name])
            t_sig = normalize_type(t_cols[col_name])
            if s_sig != t_sig:
                manual_review.append({
                    "type": "type_mismatch",
                    "table": table,
                    "column": col_name,
                    "source_type": s_sig,
                    "target_type": t_sig,
                    "detail": f"Column '{table}.{col_name}' is {s_sig} in source but {t_sig} in target -- type changes are never auto-generated (can be lossy/unsafe); resolve manually.",
                })

    return safe_actions, manual_review


def apply_ddl(cur, ddl, audit_log):
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ddl": ddl,
    }
    try:
        cur.execute(ddl)
        entry["outcome"] = "success"
    except Exception as e:
        entry["outcome"] = f"error: {str(e)[:300]}"
    audit_log.append(entry)
    return entry["outcome"] == "success"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--source-schema", required=True)
    parser.add_argument("--target-schema", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Actually apply generated DDL. Without this flag, dry-run only -- reports the plan, changes nothing.")
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS_DEFAULT)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    source_schema = args.source_schema.upper()
    target_schema = args.target_schema.upper()

    audit_log = []
    iterations = []
    manual_review_final = []
    converged = False
    stopped_reason = None

    for iteration in range(1, args.max_iterations + 1):
        source_tables, source_cols = fetch_schema_snapshot(cur, source_schema)
        target_tables, target_cols = fetch_schema_snapshot(cur, target_schema)
        safe_actions, manual_review = compute_diff(source_tables, source_cols, target_tables, target_cols, target_schema)
        manual_review_final = manual_review

        iterations.append({
            "iteration": iteration,
            "safe_actions_found": len(safe_actions),
            "manual_review_found": len(manual_review),
        })

        if not safe_actions:
            converged = True
            stopped_reason = "no_safe_fixable_differences_remaining"
            break

        if not args.execute:
            iterations[-1]["planned_ddl"] = [a["ddl"] for a in safe_actions]
            stopped_reason = "dry_run_stopped_after_one_pass"
            break

        applied_any = False
        for action in safe_actions:
            if apply_ddl(cur, action["ddl"], audit_log):
                applied_any = True
        iterations[-1]["ddl_applied"] = len(safe_actions)

        if not applied_any:
            stopped_reason = "no_statement_succeeded_this_iteration_stopping_to_avoid_infinite_loop"
            break
    else:
        stopped_reason = f"max_iterations_reached ({args.max_iterations})"

    cur.close()
    conn.close()

    output = {
        "source_schema": source_schema,
        "target_schema": target_schema,
        "execute_mode": args.execute,
        "iterations_run": len(iterations),
        "max_iterations": args.max_iterations,
        "converged": converged,
        "stopped_reason": stopped_reason,
        "iteration_log": iterations,
        "manual_review_required": manual_review_final,
        "audit_log": audit_log,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
