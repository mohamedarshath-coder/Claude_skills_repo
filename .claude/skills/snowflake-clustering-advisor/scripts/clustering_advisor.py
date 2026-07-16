#!/usr/bin/env python3
"""
snowflake-clustering-advisor helper script.

Recommends a clustering key for a table by mining REAL query history --
which queries actually touched the table, how well each one pruned
micro-partitions, and which of the table's real columns show up in the
WHERE/JOIN/GROUP BY portion of the poorly-pruned ones. Cross-checked
against SYSTEM$CLUSTERING_INFORMATION (the table's actual current
clustering health, clustered or not) and the table's real size, so a
tiny table never gets a clustering recommendation it doesn't need.

Uses snowflake-connector-python with a locally-configured named connection
(~/.snowflake/connections.toml, or SNOWFLAKE_* env vars). Never touches
credentials directly.

Column-mention extraction is a documented heuristic, not a SQL parser:
it strips the SELECT-list segment (the noisiest source of irrelevant
column mentions) and then looks for the table's real column names as
whole words in what's left (FROM/JOIN/WHERE/GROUP BY/ORDER BY). It
cannot fully distinguish a filter from an unrelated mention inside a
subquery, and says so in the output rather than overclaiming precision.

Usage:
    python clustering_advisor.py --schema NAME --table NAME
        [--connection NAME] [--days N] [--pruning-threshold-pct N]
        [--min-evidence-queries N] [--min-table-gb N]

Requires: snowflake-connector-python (already installed in this environment).
"""
import argparse
import json
import os
import re
import sys

import snowflake.connector

# Consistent with snowflake-query-optimizer's own thresholds, so "poor
# pruning" means the same thing across both skills.
POOR_PRUNING_RATIO = 0.5
POOR_PRUNING_MIN_PARTITIONS = 10

TABLE_SIZE_SQL = """
SELECT row_count, bytes
FROM information_schema.tables
WHERE table_schema = %(schema)s AND table_name = %(table)s
"""

TABLE_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = %(schema)s AND table_name = %(table)s
"""

# Substring match on the table name is a real limitation -- it can't tell
# "FROM raw.raw_orders" apart from a comment or string literal that happens
# to mention the name. Documented in SKILL.md, not silently assumed away.
QUERIES_TOUCHING_TABLE_SQL = """
SELECT
    query_id,
    query_text,
    partitions_scanned,
    partitions_total,
    execution_time
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD('day', -%(days)s, CURRENT_TIMESTAMP())
    AND execution_status = 'SUCCESS'
    AND partitions_total >= %(min_partitions)s
    AND query_text ILIKE %(table_pattern)s
ORDER BY start_time DESC
LIMIT 500
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


def fetch_section(errors, section_name, fetch_fn):
    try:
        return fetch_fn()
    except Exception as e:
        errors[section_name] = str(e)[:300]
        return None


def get_clustering_info(cur, fq_table):
    """SYSTEM\\$CLUSTERING_INFORMATION only returns depth/overlap stats for
    a table that has an EXPLICIT clustering key defined (ALTER TABLE ...
    CLUSTER BY) -- found live: it raises "table X is not clustered" on a
    table with no key at all, it does not fall back to reporting natural
    micro-partition organization. That specific error is caught here and
    turned into a clean, honest "no key defined" result rather than
    being treated as a data-access failure."""
    try:
        cur.execute("SELECT SYSTEM$CLUSTERING_INFORMATION(%s)", (fq_table,))
        raw = cur.fetchone()[0]
        info = json.loads(raw)
        return {
            "clustered": True,
            "cluster_by_keys": info.get("cluster_by_keys"),
            "total_partition_count": info.get("total_partition_count"),
            "average_overlaps": info.get("average_overlaps"),
            "average_depth": info.get("average_depth"),
        }
    except Exception as e:
        if "is not clustered" in str(e):
            return {"clustered": False, "cluster_by_keys": None}
        raise


def strip_select_list(query_text):
    """Removes the SELECT-list segment (between SELECT and the first
    top-level FROM) -- the single noisiest source of column mentions
    that are display columns, not filters. Best-effort regex, not a
    real SQL parser: falls back to the original text if no FROM is found,
    rather than guessing wrong and silently dropping real content."""
    match = re.search(r"\bSELECT\b", query_text, re.IGNORECASE)
    from_match = re.search(r"\bFROM\b", query_text, re.IGNORECASE)
    if match and from_match and from_match.start() > match.end():
        return query_text[:match.start()] + query_text[from_match.start():]
    return query_text


def extract_candidate_columns(poorly_pruned_rows, real_columns):
    """Counts how often each of the table's REAL columns appears (as a
    whole word, case-insensitive) in the non-SELECT-list portion of each
    poorly-pruned query. Only counts columns that actually exist on this
    table, so a same-named column from a joined table can still produce
    a false-positive mention -- documented, not hidden."""
    counts = {}
    pruning_ratios_when_present = {}
    col_patterns = {col: re.compile(r"\b" + re.escape(col) + r"\b", re.IGNORECASE) for col in real_columns}

    for row in poorly_pruned_rows:
        remainder = strip_select_list(row["query_text"] or "")
        scanned = row.get("partitions_scanned") or 0
        total = row.get("partitions_total") or 1
        ratio = scanned / total if total else 0
        for col, pattern in col_patterns.items():
            if pattern.search(remainder):
                counts[col] = counts.get(col, 0) + 1
                pruning_ratios_when_present.setdefault(col, []).append(ratio)

    candidates = []
    for col, count in counts.items():
        ratios = pruning_ratios_when_present[col]
        candidates.append({
            "column": col,
            "mention_count": count,
            "avg_pruning_ratio_when_present": round(sum(ratios) / len(ratios), 3),
        })
    return sorted(candidates, key=lambda c: c["mention_count"], reverse=True)


def build_recommendation(table_bytes, min_table_gb, queries_analyzed, min_evidence_queries,
                         poorly_pruned_rows, candidates, existing_clustering):
    min_table_bytes = min_table_gb * (1024 ** 3)

    if table_bytes is not None and table_bytes < min_table_bytes:
        return {
            "status": "table_too_small",
            "detail": f"Table is {round(table_bytes / (1024**3), 3)} GB, below the {min_table_gb} GB "
                      f"threshold this check uses -- clustering overhead (reclustering credits) "
                      f"isn't worth it at this size regardless of query patterns.",
            "suggested_keys": None,
        }

    if queries_analyzed < min_evidence_queries:
        return {
            "status": "not_enough_query_history",
            "detail": f"Only {queries_analyzed} qualifying quer{'y' if queries_analyzed == 1 else 'ies'} "
                      f"touched this table in the window (need at least {min_evidence_queries} to make "
                      f"a confident call) -- widen --days or wait for more real usage before trusting "
                      f"a recommendation either way.",
            "suggested_keys": None,
        }

    if not poorly_pruned_rows:
        return {
            "status": "pruning_already_good",
            "detail": f"{queries_analyzed} qualifying queries analyzed, none scanned more than "
                      f"{int(POOR_PRUNING_RATIO * 100)}% of the table's partitions -- pruning already "
                      f"looks healthy, no clustering change indicated.",
            "suggested_keys": None,
        }

    if not candidates:
        return {
            "status": "insufficient_column_evidence",
            "detail": f"{len(poorly_pruned_rows)} of {queries_analyzed} queries pruned poorly, but no "
                      f"real column of this table could be identified in their filter/join text -- "
                      f"this is a text-matching heuristic, not a SQL parser, and it found nothing "
                      f"confident enough to recommend. Don't treat this as 'no problem exists.'",
            "suggested_keys": None,
        }

    top = candidates[:2]
    suggested_keys = [c["column"] for c in top]
    existing_keys = (existing_clustering or {}).get("cluster_by_keys")

    if existing_keys:
        return {
            "status": "reclustering_may_help",
            "detail": f"Table already has a clustering key ({existing_keys}), but {len(poorly_pruned_rows)} "
                      f"of {queries_analyzed} queries still pruned poorly. The most-mentioned filter "
                      f"column(s) in those queries were {suggested_keys} -- worth checking whether the "
                      f"existing key still matches real query patterns, or whether the table needs "
                      f"reclustering (data drift can degrade an originally-good key over time).",
            "suggested_keys": suggested_keys,
        }

    return {
        "status": "recommend",
        "detail": f"No clustering key currently defined. {len(poorly_pruned_rows)} of {queries_analyzed} "
                  f"queries in the window scanned more than {int(POOR_PRUNING_RATIO * 100)}% of the "
                  f"table's partitions, and {suggested_keys[0]} was the most frequent real column "
                  f"in their filter/join text.",
        "suggested_keys": suggested_keys,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--pruning-threshold-pct", type=float, default=POOR_PRUNING_RATIO * 100)
    parser.add_argument("--min-evidence-queries", type=int, default=3)
    parser.add_argument("--min-table-gb", type=float, default=1.0)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
        cur = conn.cursor()
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    schema = args.schema.upper()
    table = args.table.upper()
    fq_table = f"{schema}.{table}"
    pruning_ratio = args.pruning_threshold_pct / 100

    errors = {}

    size_rows = fetch_section(errors, "table_size",
                              lambda: run_query(cur, TABLE_SIZE_SQL, {"schema": schema, "table": table}))
    if size_rows == []:
        print(json.dumps({"error": f"table {fq_table} not found in information_schema"}), file=sys.stderr)
        cur.close()
        conn.close()
        sys.exit(1)
    table_row_count = size_rows[0]["row_count"] if size_rows else None
    table_bytes = size_rows[0]["bytes"] if size_rows else None

    column_rows = fetch_section(errors, "table_columns",
                                lambda: run_query(cur, TABLE_COLUMNS_SQL, {"schema": schema, "table": table}))
    real_columns = [r["column_name"] for r in (column_rows or [])]

    existing_clustering = fetch_section(errors, "clustering_information",
                                        lambda: get_clustering_info(cur, fq_table))

    query_rows = fetch_section(errors, "queries_touching_table", lambda: run_query(
        cur, QUERIES_TOUCHING_TABLE_SQL,
        {"days": args.days, "min_partitions": POOR_PRUNING_MIN_PARTITIONS,
         "table_pattern": f"%{schema}.{table}%"},
    ))
    query_rows = query_rows or []

    cur.close()
    conn.close()

    poorly_pruned_rows = [
        r for r in query_rows
        if r.get("partitions_total") and (r.get("partitions_scanned") or 0) / r["partitions_total"] > pruning_ratio
    ]
    candidates = extract_candidate_columns(poorly_pruned_rows, real_columns) if real_columns else []

    recommendation = build_recommendation(
        table_bytes=table_bytes,
        min_table_gb=args.min_table_gb,
        queries_analyzed=len(query_rows),
        min_evidence_queries=args.min_evidence_queries,
        poorly_pruned_rows=poorly_pruned_rows,
        candidates=candidates,
        existing_clustering=existing_clustering,
    )

    output = {
        "schema": schema,
        "table": table,
        "heuristic_note": "Candidate columns are found via text-matching real column names against the "
                          "non-SELECT-list portion of poorly-pruned queries -- not a SQL parser. It can "
                          "miss a real filter written unusually, and can't fully rule out a same-named "
                          "column from a different, joined table.",
        "table_stats": {"row_count": table_row_count, "bytes": table_bytes},
        "existing_clustering": existing_clustering,
        "queries_analyzed": len(query_rows),
        "pruning_threshold_pct": args.pruning_threshold_pct,
        "poorly_pruned_query_count": len(poorly_pruned_rows),
        "candidate_clustering_columns": candidates,
        "advisory": recommendation,
        "errors": errors,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
