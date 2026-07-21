#!/usr/bin/env python3
"""
Unit tests for liquid_clustering_advisor's pure-logic functions. The real
end-to-end pipeline (query-history mining, ALTER TABLE CLUSTER BY, OPTIMIZE,
verify-improvement) was proven live against a real Databricks SQL Warehouse
and Delta table instead -- see SKILL.md's "Verified live" section.

Run: python test_liquid_clustering_advisor.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from liquid_clustering_advisor import (  # noqa: E402
    strip_select_list,
    extract_candidate_columns,
    build_advisory,
    find_queries_touching_table,
)

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- strip_select_list ---

check(
    "strips a simple SELECT-list, keeps FROM/WHERE",
    strip_select_list("SELECT event_id, amount FROM dev.claude_skills_test.lc_test_events WHERE region = 'US'")
    == "FROM dev.claude_skills_test.lc_test_events WHERE region = 'US'",
)
check("falls back to original text when no FROM exists", strip_select_list("SELECT 1") == "SELECT 1")
check(
    "leaves a query with FROM before SELECT (malformed/unusual) unchanged",
    strip_select_list("FROM t SELECT event_id") == "FROM t SELECT event_id",
)


# --- extract_candidate_columns ---

def make_query(query_text, read=8, pruned=0):
    return SimpleNamespace(query_text=query_text, metrics=SimpleNamespace(read_files_count=read, pruned_files_count=pruned))


real_columns = ["EVENT_ID", "REGION", "CUSTOMER_ID", "AMOUNT"]

poorly_pruned = [
    make_query("SELECT event_id, amount FROM lc_test_events WHERE region = 'US'"),
    make_query("SELECT event_id FROM lc_test_events WHERE region = 'EU'"),
    make_query("SELECT amount FROM lc_test_events WHERE customer_id = 5"),
]
candidates = extract_candidate_columns(poorly_pruned, real_columns)
candidate_names = [c["column"] for c in candidates]
check("region found as the most frequent real filter column", candidates[0]["column"] == "REGION")
check("region mention_count is 2", candidates[0]["mention_count"] == 2)
check("customer_id also found once", "CUSTOMER_ID" in candidate_names)
check(
    "event_id NOT counted -- it only appears in the stripped SELECT list, never in WHERE",
    "EVENT_ID" not in candidate_names,
)
check("no real-column filter mention yields zero candidates", extract_candidate_columns([make_query("SELECT event_id FROM t WHERE 1=1")], real_columns) == [])
check("empty poorly-pruned list returns empty candidates", extract_candidate_columns([], real_columns) == [])


# --- find_queries_touching_table ---

q_select = SimpleNamespace(query_text="SELECT * FROM dev.claude_skills_test.lc_test_events", statement_type="QueryStatementType.SELECT", metrics=SimpleNamespace(read_files_count=1, pruned_files_count=0))
q_no_metrics = SimpleNamespace(query_text="SELECT * FROM dev.claude_skills_test.lc_test_events", statement_type="QueryStatementType.SELECT", metrics=None)
q_other_table = SimpleNamespace(query_text="SELECT * FROM other_table", statement_type="QueryStatementType.SELECT", metrics=SimpleNamespace(read_files_count=1, pruned_files_count=0))
q_insert = SimpleNamespace(query_text="INSERT INTO dev.claude_skills_test.lc_test_events VALUES (1)", statement_type="QueryStatementType.INSERT", metrics=SimpleNamespace(read_files_count=1, pruned_files_count=0))
q_no_text = SimpleNamespace(query_text=None, statement_type="QueryStatementType.SELECT", metrics=SimpleNamespace(read_files_count=1, pruned_files_count=0))

matches = find_queries_touching_table([q_select, q_no_metrics, q_other_table, q_insert, q_no_text], "dev.claude_skills_test.lc_test_events")
check("only the real matching SELECT-with-metrics query survives filtering", matches == [q_select])


# --- build_advisory: every branch ---

base_args = dict(min_table_gb=0.1, min_evidence_queries=3)


def adv(**overrides):
    args = dict(
        table_bytes=5 * (1024 ** 3),
        queries_analyzed=6,
        poorly_pruned=[1, 2, 3, 4],
        candidates=[{"column": "REGION", "mention_count": 4, "avg_read_ratio_when_present": 1.0}],
        existing_clustering=[],
    )
    args.update(overrides)
    return build_advisory(**base_args, **args)


check("table_too_small fires below the GB floor", adv(table_bytes=1024)["status"] == "table_too_small")
check("not_enough_query_history fires below the evidence floor", adv(queries_analyzed=1)["status"] == "not_enough_query_history")
check("pruning_already_good fires when nothing pruned poorly", adv(poorly_pruned=[])["status"] == "pruning_already_good")
check(
    "insufficient_column_evidence fires when poor pruning exists but no candidate columns found",
    adv(candidates=[])["status"] == "insufficient_column_evidence",
)
check(
    "recommend fires for a big, poorly-pruned, unclustered table with a clear candidate",
    adv()["status"] == "recommend" and adv()["suggested_keys"] == ["REGION"],
)
check(
    "reclustering_may_help fires when a clustering key already exists but pruning is still poor",
    adv(existing_clustering=["OTHER_COL"])["status"] == "reclustering_may_help",
)
check("no crash when table_bytes is None (treated as not-too-small)", adv(table_bytes=None)["status"] == "recommend")

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All liquid_clustering_advisor unit tests passed.")
sys.exit(0)
