---
name: unified-cost-optimizer
description: Aggregates cost/idle-risk findings from snowflake-cost-audit, databricks-cluster-audit, and snowflake-query-optimizer into one ranked roadmap -- real dollar-quantified findings first, unquantified configuration-risk and query-performance findings after. Use when asked for a cross-platform cost overview, savings roadmap, or "where is our data platform spend/risk concentrated."
risk: read-only
loop-tier: on-demand
---

## Purpose

Leadership asking "where should we focus cost/cleanup effort across our data platform" today means reading three separate reports (Snowflake cost, Databricks clusters, Snowflake query performance) and manually reconciling them. This skill is the first in the catalog that reasons *across* platforms instead of within one — it genuinely calls three sibling skills' scripts and merges their findings into a single roadmap.

## When to use

Use this skill when asked for a cross-platform cost overview, a unified savings roadmap, or "where is our data platform spend or cost-risk concentrated" across Snowflake and Databricks together.

## Steps

1. Confirm both underlying connections are configured: Snowflake (`~/.snowflake/connections.toml`) and Databricks (`DATABRICKS_HOST`/`DATABRICKS_TOKEN` or `~/.databrickscfg`). This skill never asks for or handles either credential directly.
2. Run `python {{SKILL_DIR}}/scripts/unified_cost_optimizer.py` (accepts `--days N`, `--snowflake-connection NAME`, `--databricks-profile NAME`).
3. The script **calls the actual `snowflake-cost-audit`, `databricks-cluster-audit`, and `snowflake-query-optimizer` scripts directly** (a real cross-skill dependency on all three, not a reimplementation) — a fix to any sibling skill's logic automatically flows through here too.
4. **Critical honesty rule, inherited from all three source skills:** this does *not* produce one blended dollar total across platforms.
   - **Tier 1 — quantified.** Only Snowflake's `cost_anomalies` carry a real, evidence-backed credit figure. These are ranked by actual credits, highest first.
   - **Tier 2 — configuration risk, unquantified.** Everything else — Snowflake's idle/oversized warehouse flags, every Databricks cluster finding, *and* every slow-query finding from `snowflake-query-optimizer` — has no dollar amount attached. A slow query has a duration, not a credit figure, so it's Tier 2 like everything else here. List these, but never rank them against Tier 1 or against each other by fabricated cost-equivalence. Ranking configuration observations as if they were comparable dollar figures would repeat exactly the overclaiming `databricks-cluster-audit` was corrected for earlier in this repo's history.
5. If one platform's script fails (bad credentials, connection issue), report that platform's section as unavailable with the real underlying error — never silently drop it or present a partial result as complete.
6. If `tier1_quantified_savings_opportunities` is empty, say so plainly — an empty Tier 1 is a valid, common, honest result (most days won't have a real cost anomaly), not a sign something's broken.

## Helper scripts

- `{{SKILL_DIR}}/scripts/unified_cost_optimizer.py` — orchestrates `snowflake-cost-audit/scripts/cost_audit.py`, `databricks-cluster-audit/scripts/cluster_audit.py`, and `snowflake-query-optimizer/scripts/query_optimizer.py` as subprocesses, merges their JSON output. No independent data access of its own; entirely dependent on (and therefore only as reliable as) its three sibling skills.

## Output format

Render as Markdown:

1. **Summary line** — bolded: total quantified credits at risk (Tier 1), and count of unquantified configuration risks (Tier 2), across both platforms.
2. **Tier 1 — Quantified savings opportunities (table)** — only if non-empty:

   | Platform | Target | Credits | Evidence |
   |---|---|---|---|
   | Snowflake | `ANALYTICS_WH` | 4.2 | +282% vs trailing avg |

3. **Tier 2 — Configuration risks (unquantified) (table)** — grouped by platform, not ranked against Tier 1:

   | Platform | Target | Finding | Evidence |
   |---|---|---|---|
   | Databricks | `Jane's Personal Compute Cluster` | Permits long idle billing | `autotermination_minutes = 4320` |

4. **Source errors** — if either platform's section failed, state which one and the real error, plainly.
5. **Bottom line** — one bolded sentence naming the single highest-priority action, drawn from Tier 1 if non-empty, otherwise the most severe Tier 2 item, otherwise "No cost or configuration risks found on either platform right now."

## Verified live (not just fixture-tested)

Run against both real systems together. Happy path: Snowflake returned zero cost anomalies (correctly empty Tier 1, no manufactured finding) while Databricks correctly surfaced the same 3-cluster shared-policy pattern found by `databricks-cluster-audit` directly — proving the aggregation is faithful to the source skill's own output, not a reinterpretation. Degradation path: deliberately broken Databricks credentials produced a real, specific error (`account_id is required to resolve discovery_url...`) in `source_errors`, while the Snowflake section still completed normally — confirming one platform's failure doesn't take down the other's real data.

## Known untested paths (honest status, not hidden)

**Tier 1 (quantified) — now live-verified**: on 2026-07-14 a real cost anomaly existed in the account (`INSIGHTOPS_WH` +134.1% vs trailing average, caused by this project's own testing queries that day), and this skill correctly surfaced it as a Tier 1 entry with real quantified credits (0.419) and evidence, alongside the 3 unquantified Tier 2 config risks. The initial "never non-empty" gap closed with real data. (Multi-entry Tier 1 ranking — more than one simultaneous anomaly — has still never occurred live; the sort is trivial but unexercised beyond one entry.)

`AWS` remains the not-yet-added third platform from this skill's original backlog scoping — no AWS access exists in this environment; adding it is a natural, not-yet-done extension.

**Coverage gap in the query-performance section, found during demo testing (2026-07-15), closed 2026-07-17:** this skill calls `query_optimizer.py` with no `--query-id`, which used to only analyze the top 10 slowest queries by duration in the window. A query with a real flagged issue (e.g. the account's known `spilling_to_local_storage` case, 26MB spilled, 1.46s execution) could rank well outside the top 10 by duration if other unrelated queries simply ran longer without any issue at all -- confirmed live at the time: in a 14-day window, the top 10 slowest queries ranged 1.9s-13.8s and none included the known-flagged 1.46s query, so this section reported zero query-performance findings despite a real one existing in the account.

Fixed directly in `query_optimizer.py` (see that skill's own SKILL.md): the default scan now covers the 50 slowest queries (`--scan-limit`) before diagnosis runs, and results are ranked issues-first so a flagged query is never truncated away regardless of its duration rank. Re-verified live through this same aggregation path afterward: the query-performance section now correctly surfaces real issues, including the originally-missed 1.46s query and, as a bonus, the first-ever live firing of `poor_partition_pruning`.

## Loop tier & future promotion

Currently **Tier 1 (on-demand)**. A scheduled variant is plausible (e.g. weekly, notify only on a new Tier 1 finding or a new Tier 2 pattern), but would require the same explicit promotion PR as `snowflake-cost-audit`'s promotion — schedule, notification channel, budget, kill switch, run logging. Not done here.
