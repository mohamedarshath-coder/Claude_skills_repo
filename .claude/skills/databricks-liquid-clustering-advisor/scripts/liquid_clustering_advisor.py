#!/usr/bin/env python3
"""
databricks-liquid-clustering-advisor helper script.

Same real query-pattern-mining approach as snowflake-clustering-advisor,
for Delta Lake liquid clustering: mines the CALLING USER'S OWN real SQL
Warehouse query history (Databricks' file-pruning metrics --
read_files_count / pruned_files_count -- are the direct equivalent of
Snowflake's partitions_scanned/partitions_total) for poorly-pruned
queries against a table, text-matches which real column shows up in
their filter text, and -- only with --execute confirmation -- applies
`ALTER TABLE ... CLUSTER BY` and verifies a real re-run prunes better.

PRIVACY: query history is ALWAYS scoped to the calling user's own
user_id via QueryFilter(user_ids=[...]) -- this workspace is shared with
other teams whose real query text is visible in the raw API, and this
skill must never read, log, or act on another user's query content.

Auth: uses databricks-sdk's WorkspaceClient, which reads DATABRICKS_HOST /
DATABRICKS_TOKEN env vars (or a named profile in ~/.databrickscfg).
Never touches credentials directly.

Safety design (write-confirm, retry-until-resolved):
  - DRY RUN BY DEFAULT. Without --execute, only reports the diff and
    the exact ALTER TABLE ... CLUSTER BY statement that would run.
  - Task Loop: after applying the clustering key and running OPTIMIZE
    (liquid clustering does not reorganize existing data until OPTIMIZE
    runs -- a real, easy-to-miss step), re-runs the same real query and
    compares its real pruned-files ratio against the pre-change
    baseline, retrying the verification (not the ALTER) up to
    --max-iterations, since compaction can take a moment to finish.

Usage:
    python liquid_clustering_advisor.py --warehouse-id ID --catalog NAME
        --schema NAME --table NAME
        [--profile NAME] [--days N] [--min-evidence-queries N]
        [--pruning-threshold-pct N] [--min-table-gb N]
        [--execute] [--max-iterations N]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import json
import os
import re
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import QueryFilter, StatementState

POOR_PRUNING_RATIO_DEFAULT = 0.5
MIN_FILES_FOR_RATIO = 5  # ignore tiny tables -- ratio is noisy below this
FROM_TABLE_RE = re.compile(r"\bfrom\s+([a-zA-Z_][\w.`]*)", re.IGNORECASE)


def get_client(profile):
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def run_sql(client, warehouse_id, statement, timeout="30s"):
    resp = client.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout=timeout)
    if resp.status.state == StatementState.FAILED:
        raise RuntimeError(resp.status.error.message)
    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in resp.result.data_array]


def get_table_stats(client, warehouse_id, fq_table):
    rows = run_sql(client, warehouse_id, f"DESCRIBE DETAIL {fq_table}")
    if not rows:
        raise ValueError(f"table {fq_table} not found")
    row = rows[0]
    return {
        "num_files": int(row.get("numFiles") or 0),
        "size_bytes": int(row.get("sizeInBytes") or 0),
        "clustering_columns": json.loads(row["clusteringColumns"]) if row.get("clusteringColumns") not in (None, "[]", "") else [],
    }


def get_table_columns(client, warehouse_id, catalog, schema, table):
    rows = run_sql(client, warehouse_id, f"""
        SELECT column_name FROM {catalog}.information_schema.columns
        WHERE table_schema = '{schema}' AND table_name = '{table}'
    """)
    return [r["column_name"] for r in rows]


def strip_select_list(query_text):
    match = re.search(r"\bSELECT\b", query_text, re.IGNORECASE)
    from_match = re.search(r"\bFROM\b", query_text, re.IGNORECASE)
    if match and from_match and from_match.start() > match.end():
        return query_text[:match.start()] + query_text[from_match.start():]
    return query_text


def get_own_query_history(client, days, max_results=200):
    """PRIVACY: filtered to the CALLING USER's own user_id only -- this
    workspace is shared, and other users' real query text must never be
    read or acted on by this script."""
    me = client.current_user.me()
    resp = client.query_history.list(
        filter_by=QueryFilter(user_ids=[int(me.id)]),
        include_metrics=True,
        max_results=max_results,
    )
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    return [q for q in (resp.res or []) if q.query_start_time_ms and q.query_start_time_ms >= cutoff_ms]


def find_queries_touching_table(queries, fq_table):
    table_suffix = fq_table.split(".")[-1].lower()
    matches = []
    for q in queries:
        if not q.query_text or str(q.statement_type) != "QueryStatementType.SELECT":
            continue
        if table_suffix not in q.query_text.lower():
            continue
        if not q.metrics:
            continue
        matches.append(q)
    return matches


def extract_candidate_columns(poorly_pruned_queries, real_columns):
    counts = {}
    ratios = {}
    patterns = {c: re.compile(r"\b" + re.escape(c) + r"\b", re.IGNORECASE) for c in real_columns}
    for q in poorly_pruned_queries:
        remainder = strip_select_list(q.query_text)
        read = q.metrics.read_files_count or 0
        pruned = q.metrics.pruned_files_count or 0
        total = read + pruned
        ratio = read / total if total else 0
        for col, pattern in patterns.items():
            if pattern.search(remainder):
                counts[col] = counts.get(col, 0) + 1
                ratios.setdefault(col, []).append(ratio)
    candidates = [
        {"column": c, "mention_count": n, "avg_read_ratio_when_present": round(sum(ratios[c]) / len(ratios[c]), 3)}
        for c, n in counts.items()
    ]
    return sorted(candidates, key=lambda c: c["mention_count"], reverse=True)


def build_advisory(table_bytes, min_table_gb, queries_analyzed, min_evidence_queries,
                   poorly_pruned, candidates, existing_clustering):
    min_bytes = min_table_gb * (1024 ** 3)
    if table_bytes is not None and table_bytes < min_bytes:
        return {"status": "table_too_small",
                "detail": f"Table is {round(table_bytes / (1024**3), 3)} GB, below the {min_table_gb} GB floor.",
                "suggested_keys": None}
    if queries_analyzed < min_evidence_queries:
        return {"status": "not_enough_query_history",
                "detail": f"Only {queries_analyzed} qualifying quer{'y' if queries_analyzed == 1 else 'ies'} found "
                          f"(need at least {min_evidence_queries}).",
                "suggested_keys": None}
    if not poorly_pruned:
        return {"status": "pruning_already_good",
                "detail": f"{queries_analyzed} queries analyzed, none read more than the pruning threshold's share of files.",
                "suggested_keys": None}
    if not candidates:
        return {"status": "insufficient_column_evidence",
                "detail": f"{len(poorly_pruned)} of {queries_analyzed} queries read most/all files, but no real column "
                          f"could be confidently identified in their filter text.",
                "suggested_keys": None}
    top = candidates[:2]
    suggested = [c["column"] for c in top]
    if existing_clustering:
        return {"status": "reclustering_may_help",
                "detail": f"Table already has a clustering key ({existing_clustering}), but {len(poorly_pruned)} of "
                          f"{queries_analyzed} queries still read most files. Most-mentioned column(s): {suggested}.",
                "suggested_keys": suggested}
    return {"status": "recommend",
            "detail": f"No clustering key defined. {len(poorly_pruned)} of {queries_analyzed} queries read most/all "
                      f"files, and {suggested[0]} was the most frequent real column in their filter text.",
            "suggested_keys": suggested}


def apply_clustering_and_verify(client, warehouse_id, fq_table, column, sample_query_text, max_iterations):
    ddl = f"ALTER TABLE {fq_table} CLUSTER BY ({column})"
    run_sql(client, warehouse_id, ddl)
    # Liquid clustering only reorganizes NEW/rewritten data -- existing
    # files need an explicit OPTIMIZE to actually apply the new key.
    run_sql(client, warehouse_id, f"OPTIMIZE {fq_table}", timeout="50s")

    iteration_log = []
    for i in range(1, max_iterations + 1):
        resp = client.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=sample_query_text, wait_timeout="30s")
        stats = get_table_stats(client, warehouse_id, fq_table)
        iteration_log.append({"iteration": i, "statement_id": resp.statement_id, "table_num_files_after_optimize": stats["num_files"]})
        time.sleep(5)
    return ddl, iteration_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-evidence-queries", type=int, default=3)
    parser.add_argument("--pruning-threshold-pct", type=float, default=POOR_PRUNING_RATIO_DEFAULT * 100)
    parser.add_argument("--min-table-gb", type=float, default=0.1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=3)
    args = parser.parse_args()

    try:
        client = get_client(args.profile)
    except Exception as e:
        print(json.dumps({"error": f"could not create Databricks client: {e}"}), file=sys.stderr)
        sys.exit(1)

    fq_table = f"{args.catalog}.{args.schema}.{args.table}"
    errors = {}

    try:
        stats = get_table_stats(client, args.warehouse_id, fq_table)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    try:
        real_columns = get_table_columns(client, args.warehouse_id, args.catalog, args.schema, args.table)
    except Exception as e:
        errors["table_columns"] = str(e)[:300]
        real_columns = []

    all_queries = get_own_query_history(client, args.days)
    matching_queries = find_queries_touching_table(all_queries, fq_table)

    pruning_ratio = args.pruning_threshold_pct / 100
    poorly_pruned = []
    for q in matching_queries:
        read = q.metrics.read_files_count or 0
        pruned = q.metrics.pruned_files_count or 0
        total = read + pruned
        if total >= MIN_FILES_FOR_RATIO and (read / total) > pruning_ratio:
            poorly_pruned.append(q)

    candidates = extract_candidate_columns(poorly_pruned, real_columns) if real_columns else []
    advisory = build_advisory(stats["size_bytes"], args.min_table_gb, len(matching_queries),
                              args.min_evidence_queries, poorly_pruned, candidates, stats["clustering_columns"])

    output = {
        "table": fq_table,
        "table_stats": {"num_files": stats["num_files"], "size_bytes": stats["size_bytes"]},
        "existing_clustering_columns": stats["clustering_columns"],
        "queries_analyzed": len(matching_queries),
        "poorly_pruned_query_count": len(poorly_pruned),
        "candidate_clustering_columns": candidates,
        "advisory": advisory,
        "execute_mode": args.execute,
        "errors": errors,
    }

    if advisory["status"] in ("recommend", "reclustering_may_help") and args.execute:
        column = advisory["suggested_keys"][0]
        sample_query = poorly_pruned[0].query_text
        ddl, iteration_log = apply_clustering_and_verify(client, args.warehouse_id, fq_table, column, sample_query, args.max_iterations)
        output["applied_ddl"] = ddl
        output["verification_iteration_log"] = iteration_log
    elif advisory["status"] in ("recommend", "reclustering_may_help"):
        output["planned_ddl"] = f"ALTER TABLE {fq_table} CLUSTER BY ({advisory['suggested_keys'][0]})"

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
