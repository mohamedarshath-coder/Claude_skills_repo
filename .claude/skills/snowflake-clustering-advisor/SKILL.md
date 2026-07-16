---
name: snowflake-clustering-advisor
description: Recommends a clustering key for a Snowflake table by mining real query history for poor partition pruning and the filter columns behind it, cross-checked against the table's actual current clustering health and real size -- never recommends clustering a table too small to benefit. Use when asked whether a table needs a clustering key, to review clustering strategy, or why a table's queries scan too many partitions.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Should this table have a clustering key, and on which column?" today means manually reading query profiles one at a time, eyeballing partition-pruning ratios, and guessing at a filter column from memory. This skill answers it from real data: which of the table's actual columns show up in the queries that are actually pruning poorly, whether the table already has a clustering key and how healthy it is, and whether the table is even large enough for clustering to matter in the first place.

## When to use

Use this skill when asked: whether a table needs a clustering key, to review clustering strategy for a table, why queries against a table scan too many partitions, or to recommend a clustering key from real query patterns.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/clustering_advisor.py --schema NAME --table NAME` (accepts `--connection`, `--days` [default 14], `--pruning-threshold-pct` [default 50], `--min-evidence-queries` [default 3], `--min-table-gb` [default 1.0]).
3. The script:
   - Pulls the table's real row count and size from `INFORMATION_SCHEMA.TABLES`
   - Calls `SYSTEM$CLUSTERING_INFORMATION` for the table's actual current clustering state — this requires an explicit clustering key to return depth/overlap stats; a table with none returns a clean "not clustered" result, never an error (see the live bug this caught, below)
   - Finds real queries that touched the table in the window (a text-match on the qualified table name — a documented limitation, not a full dependency parser) with at least 10 partitions total (ignoring tiny-table pruning-ratio noise, same threshold as `snowflake-query-optimizer`)
   - Flags the ones that scanned more than the pruning threshold of the table's partitions
   - For each poorly-pruned query, strips the SELECT-list (the noisiest source of irrelevant column mentions) and counts which of the table's *real* columns appear in what's left — ranking candidate clustering columns by how often they show up in poorly-pruned queries' filter/join text
4. **Never recommends clustering a table below `--min-table-gb`** — clustering has real reclustering-credit overhead, and isn't worth it at small scale regardless of query patterns. This gate fires before any query analysis is even considered.
5. Distinguishes 6 honest outcomes rather than forcing every result into "recommend" or "don't":
   - `table_too_small` — below the size floor, no analysis needed
   - `not_enough_query_history` — too few qualifying queries in the window to trust a call either way
   - `pruning_already_good` — enough queries analyzed, none pruned poorly
   - `insufficient_column_evidence` — poor pruning exists, but the text-match heuristic couldn't confidently identify a real column behind it
   - `recommend` — no existing key, poor pruning, a clear candidate column
   - `reclustering_may_help` — a clustering key already exists, but pruning is still poor (data drift, or the key no longer matches real query patterns)
6. Turn the JSON into a Markdown report (see Output format). Always state which of the 6 outcomes applies and why — never a bare "cluster on X" without the evidence behind it.

## Helper scripts

- `{{SKILL_DIR}}/scripts/clustering_advisor.py` — pure `snowflake-connector-python` queries plus one `SYSTEM$CLUSTERING_INFORMATION` call; no dependency beyond the connector. The column-mention extraction is a documented text-matching heuristic, not a SQL parser — see the heuristic_note it emits in every run.

## Output format

Render as Markdown:

1. **Summary line** — bolded: the table, its size, and the recommendation status in one phrase (e.g. "RAW.ORDERS, 40GB — recommend clustering on ORDER_DATE").
2. **Current clustering health** — whether a key exists, and if so, its average depth/overlaps.
3. **Query evidence (table)** — only if queries were analyzed: total queries, how many pruned poorly, at what threshold.
4. **Candidate columns (table)** — only if non-empty:

   | Column | Mentions in poorly-pruned queries | Avg pruning ratio when present |
   |---|---|---|
   | `REGION` | 5 | 0.82 |

5. **Recommendation** — the status, the plain-language detail, and the suggested key(s) if any.
6. **Bottom line** — one bolded sentence naming the single action (or explicitly "no action — table too small" / "no action — pruning is already healthy").

Always cite the actual row count, byte size, query counts, and pruning ratios behind every claim — never a vague "this table might benefit from clustering" without the numbers.

## Verified live (not just fixture-tested)

Run against `RAW.RAW_ORDER_ITEMS` in the real account (2026-07-16). The first live run immediately surfaced a real bug: `SYSTEM$CLUSTERING_INFORMATION` was assumed to work on any table and report its natural organization either way — it actually raises `"table X is not clustered"` on a table with no explicit clustering key, rather than falling back to a natural-state report. Fixed to catch that specific message and return a clean `{"clustered": false, "cluster_by_keys": null}` instead of surfacing it as a data-access error. Re-verified after the fix: `errors` came back empty, `existing_clustering` reported cleanly.

The `table_too_small` path fired live and correctly on that same run — `RAW_ORDER_ITEMS` is ~1MB (0.001 GB), well under the 1GB default floor, and the recommendation correctly stopped there without even attempting query analysis. The `not_enough_query_history` path was also confirmed live by rerunning with the size floor deliberately lowered to bypass it — 0 qualifying queries were found once real queries were checked (every real table in this account maxes out at 1-2 micro-partitions, so none clears the `min_partitions >= 10` bar the pruning-ratio check requires).

**The `recommend` path is now genuinely live-verified too**, closing the last real gap. A deliberately-constructed but genuinely real 60-million-row, 8.8GB table (`RAW.CLUSTER_TEST_EVENTS`, built to have 528 real micro-partitions) was created specifically so one column (`REGION`) is randomly scattered relative to physical storage order, while another (`EVENT_TIME`) is physically sorted. 9 real queries were run: 6 filtering on `REGION`, 3 on `EVENT_TIME`. Snowflake's own engine confirmed the intended contrast independently: every `REGION` query scanned **528 of 528 partitions (100%)**, every `EVENT_TIME` range query scanned only **21 of 528 (4%)**. Running the skill against this table (once `ACCOUNT_USAGE.QUERY_HISTORY`'s ~45-minute ingestion lag cleared) correctly identified all 6 `REGION` queries as poorly-pruned, correctly ignored all 3 `EVENT_TIME` queries, and recommended `REGION` as the clustering key with a 1.0 average pruning ratio — zero false positives on `CUSTOMER_ID`, `EVENT_TIME`, or `AMOUNT_DOLLARS`.

## Verification status per branch (honest status, not hidden)

| Path | Live account | Unit-tested |
|---|---|---|
| `table_too_small` | ✅ (real 0.001 GB table) | ✅ |
| `not_enough_query_history` | ✅ (real 0-qualifying-query result) | ✅ |
| `SYSTEM$CLUSTERING_INFORMATION` on an unclustered table, clean result | ✅ (real bug found and fixed live) | — |
| `recommend` (a clear, confident recommendation) | ✅ (real 60M-row/8.8GB/528-partition table; 6 of 10 real queries correctly identified as poorly-pruned, `REGION` correctly recommended at 1.0 avg pruning ratio, zero false positives) | ✅ (incl. verifying a SELECT-list-only mention like `CUSTOMER_ID` is correctly excluded) |
| `pruning_already_good` | ❌ not yet observed on a large real table (would need a large table where every query happens to prune well) | ✅ |
| `insufficient_column_evidence` | ❌ not yet observed live | ✅ |
| `reclustering_may_help` | ❌ not yet observed live (would need a table that already has a clustering key defined) | ✅ |
| `strip_select_list` / `extract_candidate_columns` (the heuristic itself) | ✅ (proven correct against the real 60M-row test above) | ✅ (SELECT-list stripping, real-column-only matching, empty-input safety) |

**What's still open:** `pruning_already_good`, `insufficient_column_evidence`, and `reclustering_may_help` haven't yet occurred live — the first would need a large real table where query patterns happen to already prune well, the second a large table with poor pruning but no identifiable filter column, the third a large table that already has an explicit clustering key defined. All three remain proven only against constructed unit-test inputs. The hardest and most valuable branch (`recommend`, the actual pattern-mining logic) is now genuinely live-proven end to end.

## Loop tier

**On-demand (Tier 1).** No obvious scheduled variant — clustering strategy is a point-in-time analytical question asked when someone is actually investigating a table's performance, not something to check on a timer.
