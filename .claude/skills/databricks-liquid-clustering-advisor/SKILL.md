---
name: databricks-liquid-clustering-advisor
description: Recommends a liquid clustering key for a Delta table by mining the caller's own real SQL Warehouse query history for poor file pruning and the filter columns behind it, cross-checked against the table's actual current clustering key and real size -- never recommends clustering a table too small to benefit. Use when asked whether a Delta table needs liquid clustering, to review clustering strategy, or why a table's queries scan too many files.
risk: write-confirm
loop-tier: retry-until-resolved
---

## Purpose

"Should this Delta table use liquid clustering, and on which column?" today means manually reading query profiles one at a time, eyeballing file-pruning ratios, and guessing at a filter column from memory. This skill answers it from real data: which of the table's actual columns show up in the queries that are actually pruning poorly, whether the table already has a clustering key, and whether the table is even large enough for clustering to matter in the first place. When asked to act, it applies the change and verifies the improvement against a real re-run rather than trusting the DDL succeeded.

## When to use

Use this skill when asked: whether a Delta table needs liquid clustering, to review clustering strategy for a table, why queries against a table read too many files, or to recommend and apply a clustering key from real query patterns.

## Steps

1. Confirm which connection profile / SQL Warehouse to use — this skill never asks for or handles credentials directly; it uses the runtime's own workspace client authentication.
2. Run `{{SKILL_DIR}}/scripts/liquid_clustering_advisor.py --warehouse-id ID --catalog NAME --schema NAME --table NAME` (accepts `--profile`, `--days` [default 7], `--pruning-threshold-pct` [default 50], `--min-evidence-queries` [default 3], `--min-table-gb` [default 0.1]).
3. The script:
   - Pulls the table's real file count and size via `DESCRIBE DETAIL`, and its current clustering columns if any
   - Reads the table's real column list from `information_schema.columns`
   - Finds real queries that touched the table in the window, scoped **strictly to the calling user's own query history** (never another user's queries, even in a shared workspace) with at least 5 files read+pruned total (ignoring tiny-table pruning noise)
   - Flags the ones that read more than the pruning threshold's share of the table's files
   - For each poorly-pruned query, strips the SELECT-list and counts which of the table's *real* columns appear in what's left — ranking candidate clustering columns by how often they show up in poorly-pruned queries' filter text
4. **Never recommends clustering a table below `--min-table-gb`** — this gate fires before any query analysis is considered.
5. Distinguishes 6 honest outcomes, same model as `snowflake-clustering-advisor`: `table_too_small`, `not_enough_query_history`, `pruning_already_good`, `insufficient_column_evidence`, `recommend`, `reclustering_may_help`.
6. **Dry run by default.** Without `--execute`, only reports the advisory and the exact `ALTER TABLE ... CLUSTER BY` statement that would run — touches nothing.
7. **With `--execute` (requires explicit user confirmation of the exact table and column before ever passing this flag):** applies `ALTER TABLE ... CLUSTER BY (column)`, then runs `OPTIMIZE` (liquid clustering does not reorganize existing files without it — a real, easy-to-miss step), then re-runs a real sample query up to `--max-iterations` times and reports the table's real file count after each pass, so the improvement is verified against ground truth, not assumed from the DDL's exit code.

## Helper scripts

- `{{SKILL_DIR}}/scripts/liquid_clustering_advisor.py` — pure workspace-client SQL statement execution; no dependency beyond the SDK. The column-mention extraction is a documented text-matching heuristic, not a SQL parser.

## Output format

Render as Markdown:

1. **Summary line** — bolded: the table, its size, and the advisory status in one phrase (e.g. "dev.claude_skills_test.lc_test_events, 485MB — recommend clustering on REGION").
2. **Current clustering** — whether a key exists.
3. **Query evidence** — total queries analyzed, how many read poorly, at what threshold.
4. **Candidate columns** — only if non-empty:

   | Column | Mentions in poorly-pruned queries | Avg read ratio when present |
   |---|---|---|
   | `region` | 4 | 1.0 |

5. **Advisory** — the status, the plain-language detail, and the suggested key(s) if any.
6. **If executed** — the exact DDL applied and the real file-count/verification iteration log.
7. **Bottom line** — one bolded sentence naming the single action (or explicitly "no action — table too small" / "no action — pruning is already healthy").

## Loop tier

**Retry-until-resolved (Tier 2).** The verification step after applying clustering is a genuine Task Loop: `OPTIMIZE` on a real warehouse can take a moment to finish compacting files, so the script re-runs the same real query and re-checks the real file count up to `--max-iterations` times (default 3) rather than trusting the DDL's exit code as proof the improvement happened. This is a fixed-count retry, not open-ended — each iteration re-checks real state and the loop always terminates.

## Verified live (not just fixture-tested)

Run against a real Delta table on a real SQL Warehouse created specifically for this test (2026-07-21):

- **Setup**: `dev.claude_skills_test.lc_test_events` (event_id, region, customer_id, amount), landed at 16 files / 485MB on a 2X-Small warehouse (file count is a real platform constraint of the warehouse's write parallelism, not something the skill controls). 5 real queries run: 4 `WHERE region = '<value>'`, all independently confirmed via the real `query_history` API (scoped to the caller's own `user_id` only — this workspace is shared with other teams whose query text must never be read by this script) to read all 16 files with 0 pruned.
- **Dry run**: correctly returned `recommend`, identified `region` as the sole candidate (4 mentions, avg read ratio 1.0), and printed the exact `ALTER TABLE ... CLUSTER BY (region)` DDL without touching the table.
- **`--execute`**: applied `ALTER TABLE ... CLUSTER BY (region)` + `OPTIMIZE`, which compacted the table from 16 to 8 files. Verification re-ran the real `region = 'APAC'` query twice; **independently confirmed via the real query_history API** that the first re-run read only 2 of 8 files (a genuine improvement from 16/16 unpruned to 2/8), and the second re-run was served entirely from the result cache (0/0) — both outcomes correctly logged in the iteration log rather than assumed from the ALTER's success.

## Verification status per branch (honest status, not hidden)

| Path | Live | Unit-tested |
|---|---|---|
| `recommend` + `--execute` + verified real pruning improvement | ✅ (real 485MB table, 16→8 files, 16/16→2/8 read ratio) | ✅ |
| `table_too_small` | — not yet observed live | ✅ |
| `not_enough_query_history` | — not yet observed live | ✅ |
| `pruning_already_good` | — not yet observed live | ✅ |
| `insufficient_column_evidence` | — not yet observed live | ✅ |
| `reclustering_may_help` | — not yet observed live | ✅ |
| `strip_select_list` / `extract_candidate_columns` / `find_queries_touching_table` | ✅ (proven correct in the live run above) | ✅ |

**What's still open:** the 5 non-`recommend` advisory branches are unit-tested only — the live test focused on proving the highest-value, highest-risk path (real DDL + real verified improvement) end-to-end, matching the priority already set by `snowflake-clustering-advisor`'s own incremental verification history.
