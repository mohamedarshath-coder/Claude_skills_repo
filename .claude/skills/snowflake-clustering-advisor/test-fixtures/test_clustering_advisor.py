#!/usr/bin/env python3
"""
Unit tests for clustering_advisor's pure-logic functions -- specifically
the branches that can't fire live against this account, since every real
table here has at most 2 micro-partitions (the min_partitions=10 filter
excludes all of them from ever counting as "poor pruning" evidence).

Covers: strip_select_list, extract_candidate_columns, and every
recommendation.status branch in build_recommendation.

Run: python test_clustering_advisor.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from clustering_advisor import (  # noqa: E402
    strip_select_list,
    extract_candidate_columns,
    build_recommendation,
)

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- strip_select_list ---

check(
    "strips a simple SELECT-list, keeps FROM/WHERE",
    strip_select_list("SELECT customer_id, email FROM raw.raw_customers WHERE region = 'US'")
    == "FROM raw.raw_customers WHERE region = 'US'",
)
check(
    "falls back to original text when no FROM exists",
    strip_select_list("SELECT 1") == "SELECT 1",
)
check(
    "leaves a query with FROM before SELECT (malformed/unusual) unchanged",
    strip_select_list("FROM raw.raw_customers SELECT customer_id") == "FROM raw.raw_customers SELECT customer_id",
)

# --- extract_candidate_columns ---

real_columns = ["CUSTOMER_ID", "REGION", "EMAIL", "SIGNUP_DATE"]


def row(query_text, scanned=8, total=10):
    return {"query_text": query_text, "partitions_scanned": scanned, "partitions_total": total}


rows = [
    row("SELECT customer_id, email FROM raw.raw_customers WHERE region = 'US'"),
    row("SELECT customer_id FROM raw.raw_customers WHERE region = 'EU'"),
    row("SELECT email FROM raw.raw_customers WHERE signup_date > '2020-01-01'"),
]
candidates = extract_candidate_columns(rows, real_columns)
candidate_names = [c["column"] for c in candidates]
check("region found as the most frequent real filter column", candidates[0]["column"] == "REGION")
check("region mention_count is 2", candidates[0]["mention_count"] == 2)
check("signup_date also found once", "SIGNUP_DATE" in candidate_names)
check(
    "customer_id NOT counted as a candidate -- it only appears in the stripped SELECT list, never in WHERE",
    "CUSTOMER_ID" not in candidate_names,
)

# A query mentioning no real column in its filter at all -- must produce no candidates.
no_match_rows = [row("SELECT customer_id FROM raw.raw_customers WHERE 1=1")]
check("no real-column filter mention yields zero candidates", extract_candidate_columns(no_match_rows, real_columns) == [])

# Empty input must not crash and must return an empty list.
check("empty poorly-pruned list returns empty candidates", extract_candidate_columns([], real_columns) == [])

# --- build_recommendation: every branch ---

base_args = dict(min_table_gb=1.0, min_evidence_queries=3)


def rec(**overrides):
    args = dict(
        table_bytes=5 * (1024 ** 3),
        queries_analyzed=10,
        poorly_pruned_rows=[{"partitions_scanned": 8, "partitions_total": 10}] * 5,
        candidates=[{"column": "REGION", "mention_count": 5, "avg_pruning_ratio_when_present": 0.8}],
        existing_clustering={"clustered": False, "cluster_by_keys": None},
    )
    args.update(overrides)
    return build_recommendation(**base_args, **args)


check("table_too_small fires below the GB floor", rec(table_bytes=1024)["status"] == "table_too_small")
check("not_enough_query_history fires below the evidence floor", rec(queries_analyzed=1)["status"] == "not_enough_query_history")
check("pruning_already_good fires when nothing pruned poorly", rec(poorly_pruned_rows=[])["status"] == "pruning_already_good")
check(
    "insufficient_column_evidence fires when poor pruning exists but no candidate columns found",
    rec(candidates=[])["status"] == "insufficient_column_evidence",
)
check(
    "recommend fires for a big, poorly-pruned, unclustered table with a clear candidate",
    rec()["status"] == "recommend" and rec()["suggested_keys"] == ["REGION"],
)
check(
    "reclustering_may_help fires when a clustering key already exists but pruning is still poor",
    rec(existing_clustering={"clustered": True, "cluster_by_keys": "LINEAR(OTHER_COL)"})["status"] == "reclustering_may_help",
)

# table_too_small must win even if table_bytes is None-safe (no crash on missing size data)
check("no crash when table_bytes is None (treated as not-too-small)", rec(table_bytes=None)["status"] == "recommend")

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All clustering_advisor unit tests passed.")
sys.exit(0)
