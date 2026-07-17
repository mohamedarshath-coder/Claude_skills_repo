#!/usr/bin/env python3
"""
Unit tests for ddl_diff's pure-logic functions -- compute_diff,
normalize_type, full_type_ddl. Also regression-pins the real bug found
live: generated DDL must always be schema-qualified with the actual
target schema, never a bare, unqualified table name (which silently
resolves against whatever schema the connection happens to default to).

Run: python test_ddl_diff.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from ddl_diff import compute_diff, normalize_type, full_type_ddl  # noqa: E402

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def col(data_type, char_len=None, precision=None, scale=None):
    return {"data_type": data_type, "character_maximum_length": char_len,
            "numeric_precision": precision, "numeric_scale": scale}


# --- normalize_type ---
check("identical VARCHAR lengths are equal", normalize_type(col("TEXT", 50)) == normalize_type(col("TEXT", 50)))
check("different VARCHAR lengths are NOT equal", normalize_type(col("TEXT", 50)) != normalize_type(col("TEXT", 200)))
check("different NUMBER precision/scale are NOT equal", normalize_type(col("NUMBER", None, 10, 2)) != normalize_type(col("NUMBER", None, 8, 0)))

# --- full_type_ddl ---
check("VARCHAR reconstructs with length", full_type_ddl(col("TEXT", 100)) == "VARCHAR(100)")
check("NUMBER reconstructs with precision/scale", full_type_ddl(col("NUMBER", None, 10, 2)) == "NUMBER(10,2)")
check("NUMBER with no explicit scale defaults to 0", full_type_ddl(col("NUMBER", None, 38)) == "NUMBER(38,0)")
check("a type with no special handling falls back to bare data_type", full_type_ddl(col("TIMESTAMP_NTZ")) == "TIMESTAMP_NTZ")

# --- compute_diff: every branch, and the schema-qualification regression test ---

source_tables = {"CUSTOMERS", "PRODUCTS", "ORDERS"}
source_cols = {
    "CUSTOMERS": {"CUSTOMER_ID": col("NUMBER", None, 38, 0), "EMAIL": col("TEXT", 255), "LOYALTY_TIER": col("TEXT", 20)},
    "PRODUCTS": {"PRODUCT_ID": col("NUMBER", None, 38, 0), "PRODUCT_NAME": col("TEXT", 200)},
    "ORDERS": {"ORDER_ID": col("NUMBER", None, 38, 0), "AMOUNT": col("NUMBER", None, 10, 2)},
}
target_tables = {"CUSTOMERS", "ORDERS", "LEGACY_AUDIT_LOG"}  # PRODUCTS missing entirely
target_cols = {
    "CUSTOMERS": {"CUSTOMER_ID": col("NUMBER", None, 38, 0), "EMAIL": col("TEXT", 255)},  # missing LOYALTY_TIER
    "ORDERS": {"ORDER_ID": col("NUMBER", None, 38, 0), "AMOUNT": col("NUMBER", None, 8, 0)},  # type mismatch
    "LEGACY_AUDIT_LOG": {"LOG_ID": col("NUMBER", None, 38, 0)},  # extra table, not in source
}

safe_actions, manual_review = compute_diff(source_tables, source_cols, target_tables, target_cols, "MY_TARGET_SCHEMA")

create_actions = [a for a in safe_actions if a["type"] == "create_table"]
check("missing table PRODUCTS is a create_table safe action", len(create_actions) == 1 and create_actions[0]["table"] == "PRODUCTS")
check(
    "REGRESSION: create_table DDL is schema-qualified with the real target schema, not a bare table name",
    'CREATE TABLE "MY_TARGET_SCHEMA"."PRODUCTS"' in create_actions[0]["ddl"],
)

add_col_actions = [a for a in safe_actions if a["type"] == "add_column"]
check("missing column LOYALTY_TIER is an add_column safe action", len(add_col_actions) == 1 and add_col_actions[0]["column"] == "LOYALTY_TIER")
check(
    "REGRESSION: add_column DDL is schema-qualified with the real target schema",
    'ALTER TABLE "MY_TARGET_SCHEMA"."CUSTOMERS"' in add_col_actions[0]["ddl"],
)

type_mismatches = [m for m in manual_review if m["type"] == "type_mismatch"]
check("AMOUNT type mismatch correctly flagged as manual review, not a safe action", len(type_mismatches) == 1 and type_mismatches[0]["column"] == "AMOUNT")
check("type mismatch is NEVER in safe_actions (never auto-fixed)", not any(a.get("column") == "AMOUNT" for a in safe_actions))

extra_tables = [m for m in manual_review if m["type"] == "extra_table_in_target"]
check("LEGACY_AUDIT_LOG correctly flagged as extra_table_in_target, never auto-dropped", len(extra_tables) == 1 and extra_tables[0]["table"] == "LEGACY_AUDIT_LOG")
check("extra table is NEVER in safe_actions (no DROP ever auto-generated)", not any("DROP" in a["ddl"] for a in safe_actions))
check("no DROP statement anywhere in generated DDL, ever", not any("DROP" in a["ddl"].upper() for a in safe_actions))

# --- extra column in target (not covered by the fixture above) ---
target_cols_extra_col = {
    "CUSTOMERS": {**target_cols["CUSTOMERS"], "DEPRECATED_FLAG": col("BOOLEAN")},
}
safe2, manual2 = compute_diff({"CUSTOMERS"}, {"CUSTOMERS": source_cols["CUSTOMERS"]},
                              {"CUSTOMERS"}, target_cols_extra_col, "T")
extra_cols = [m for m in manual2 if m["type"] == "extra_column_in_target"]
check("extra column DEPRECATED_FLAG in target correctly flagged, never auto-dropped", len(extra_cols) == 1 and extra_cols[0]["column"] == "DEPRECATED_FLAG")

# --- fully in sync: zero safe actions, zero manual review ---
safe3, manual3 = compute_diff({"A"}, {"A": {"X": col("NUMBER", None, 38, 0)}},
                              {"A"}, {"A": {"X": col("NUMBER", None, 38, 0)}}, "T")
check("fully in-sync schemas produce zero safe actions", safe3 == [])
check("fully in-sync schemas produce zero manual review items", manual3 == [])

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All ddl_diff unit tests passed.")
sys.exit(0)
