---
name: snowflake-query-optimizer
description: Analyzes slow Snowflake queries for concrete performance issues (disk spilling, poor partition pruning, warehouse queueing) using real execution statistics, and recommends specific fixes. Use when asked why a query is slow, to review Snowflake query performance, or to optimize a specific query by ID.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Why is this query slow?" usually means someone opens Snowsight, finds the query profile, and manually reads through operator statistics looking for spilling, bad pruning, or contention. This skill automates that read: it pulls the query's actual execution diagnostics and turns them into named issues with evidence and a specific recommendation — not a generic "consider tuning your query."

## When to use

Use this skill when asked to: review Snowflake query performance, diagnose why a specific query (by query ID) is slow, or audit the slowest queries in a recent window for common performance problems.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, overridable via `--connection`). Configured locally by the user ahead of time in `~/.snowflake/connections.toml` — this skill never asks for or handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/query_optimizer.py`:
   - With no arguments: analyzes the top 10 slowest **user-warehouse** queries in the last 7 days (`--days N` / `--limit N` to adjust)
   - With `--query-id <id>`: analyzes exactly one query by ID (use this when a specific slow query has already been identified)
3. The script reads `QUERY_HISTORY`'s own execution-statistics columns (no need to parse the per-operator profile tree) and flags, per query:
   - **Spilling to remote storage** (high severity) or **local storage** (medium) — warehouse undersized for the query's working set
   - **Poor partition pruning** — scanned >50% of a table's micro-partitions (only flagged for tables with ≥10 partitions total, to avoid noisy ratios on tiny tables)
   - **Warehouse queueing** — query waited on warehouse capacity before it could even start
   - **Cold-start provisioning** — warehouse had to spin up (low severity, informational)
4. **Serverless system compute pools are excluded from the default (no-`--query-id`) view** — queries on warehouses named `COMPUTE_SERVICE_WH*` (Snowflake-managed task/Snowpipe compute) aren't user-optimizable and would otherwise crowd out queries the user can actually act on.
5. Turn the JSON into a Markdown report (see Output format). If a query has zero issues, say so — do not invent a problem to seem useful; a clean query with no spill/pruning/queueing issues is itself a valid, useful finding.

## Helper scripts

- `{{SKILL_DIR}}/scripts/query_optimizer.py` — queries `QUERY_HISTORY` for diagnostic columns (`partitions_scanned`/`partitions_total`, `bytes_spilled_to_*_storage`, `queued_*_time`) and applies documented, fixed thresholds to flag concrete issues with evidence. Uses `snowflake-connector-python` with a named connection profile; never handles credentials directly.

## Output format

Render as Markdown:

1. **Summary line** — bolded: how many queries analyzed, how many had at least one issue.
2. **Per query with issues (repeat per query):**

   | Query ID | Warehouse | Duration | Issue | Evidence | Recommendation |
   |---|---|---|---|---|---|
   | `01c5...` | `INSIGHTOPS_WH` | 4.4s | Poor partition pruning | scanned 1,844 of 22,410 partitions (8%) | Filter on the table's clustering key, or re-cluster on the filtered column |

3. **Clean queries** — list query IDs + one-line preview for queries with zero issues; don't over-explain, a clean result needs no elaboration.
4. **Bottom line** — one bolded sentence: either the single highest-severity issue to act on first, or "No performance issues found in the analyzed queries."

Always cite the actual numbers (bytes spilled, partition counts, ms queued) behind every flagged issue — never a vague "this query could be faster" without the evidence.

## Loop tier & future promotion

Currently **Tier 1 (on-demand)**, per repo rule (`.claude/rules/loop-engineering.md`) that no skill starts above Tier 1.

Could pair naturally with `snowflake-cost-audit`'s cost-anomaly trigger in a future Tier 3 promotion (e.g., a daily scan of the slowest queries that only reports when a *new* issue type appears that wasn't there yesterday) — but that requires the same promotion checklist as every other skill in this repo (schedule, notification channel, budget, kill switch, run logging) via its own explicit PR. Not done here.
