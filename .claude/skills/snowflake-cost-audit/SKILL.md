---
name: snowflake-cost-audit
description: Audits Snowflake warehouse credit usage, flags idle/oversized warehouses, and surfaces the most expensive queries in a given window. Use when asked about Snowflake costs, warehouse spend, credit usage, or cost anomalies.
risk: read-only
loop-tier: on-demand
---

## Purpose

Warehouse credit usage is one of the highest-visibility, easiest-to-misjudge costs in a Snowflake account. This skill answers "where is our Snowflake spend going, and is any of it wasted?" in one command instead of a manual trawl through Snowsight's cost dashboards.

## When to use

Use this skill when asked about: Snowflake cost, warehouse spend, credit usage, cost trends/anomalies, which warehouses are idle or oversized, or which queries are the most expensive in a period.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, overridable via `--connection` or the `SNOWFLAKE_CONNECTION_NAME` env var). The profile is configured locally by the user ahead of time in `~/.snowflake/connections.toml` — this skill never asks for or handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/cost_audit.py` with the desired lookback window (default 7 days, accepts `--days N`).
3. The script queries the account's usage views for:
   - Credit consumption per warehouse per day over the window
   - Warehouses with `AUTO_SUSPEND` disabled or set unusually high (idle-cost risk), via `SHOW WAREHOUSES`
   - Warehouses whose size looks oversized relative to their actual average query duration
   - Day-over-day cost anomalies: a warehouse whose latest day is >50% above its trailing average for the window
   - The top 10 most expensive queries by **real per-query credit attribution** (`QUERY_ATTRIBUTION_HISTORY`); if that view isn't readable by the user's role, it falls back to duration ranking and the JSON's `top_queries_ranking` field says so — the report must label the section accordingly ("most expensive" vs. "longest-running"), never mislabel duration as cost
4. Read the script's JSON output and turn it into a formatted Markdown report (see Output format below) — always as tables, never a raw JSON dump or a wall of prose.
5. If nothing looks anomalous, say so plainly in the summary — do not manufacture a finding to seem useful.
6. If the JSON's `errors` object is non-empty, report which sections are missing and why (usually a permissions gap) — a partial audit is still useful, but never present it as complete.

## Scope & data-freshness caveats (must appear in every report)

- **Scope:** credit totals cover **user-managed warehouses only**. Serverless features (tasks, Snowpipe, materialized-view maintenance) bill separately and are not in this number — say "across user-managed warehouses," never "across the account."
- **Latency:** `ACCOUNT_USAGE` views lag real time — up to ~45 minutes for query history and ~3 hours for metering. Include one line noting this so a just-finished job's absence isn't mistaken for a bug.

## Helper scripts

- `{{SKILL_DIR}}/scripts/cost_audit.py` — pre-filters and pre-aggregates the raw usage-view data server-side (via SQL) before it reaches the agent, so the payload stays small regardless of account size. Uses `snowflake-connector-python` with a named connection profile; never reads, writes, or asks for credentials directly.

## Output format

Render the report as Markdown with these sections, in this order. Use tables wherever the data is tabular — never a raw JSON dump or a wall of prose.

1. **Summary line** — bolded, one sentence: total credits consumed across user-managed warehouses in the window, plus a ⚠️ callout if `cost_anomalies` is non-empty, followed by the one-line data-latency note.

2. **Credits by warehouse (table)**

   | Warehouse | Day | Credits Used |
   |---|---|---|
   | `INSIGHTOPS_WH` | 2026-07-09 | 0.71 |

3. **Cost anomalies** — only include this section if `cost_anomalies` is non-empty:

   | Warehouse | Latest Day | Latest Credits | Trailing Avg | Increase |
   |---|---|---|---|---|
   | `ANALYTICS_WH` | 2026-07-12 | 4.20 | 1.10 | +282% |

4. **Idle/oversized warehouses (table)** — only if `flagged_warehouses` is non-empty:

   | Warehouse | Issue | Evidence |
   |---|---|---|
   | `ANALYTICS_WH` | Auto-suspend high/disabled | `auto_suspend` = 3600s |

5. **Top expensive queries (table)** — title the section per `top_queries_ranking`: "Top queries by attributed credits" or "Longest-running queries (credit attribution unavailable)":

   | Query ID | Warehouse | Credits | Duration (s) | What it is |
   |---|---|---|---|---|
   | `01c5...995e` | `INSIGHTOPS_WH` | 0.0024 | 0.4 | *(one-line summary inferred from query text, e.g. "dbt build of demo_fct_orders"; else "text not visible to this role")* |

6. **Partial-data warning** — only if the JSON `errors` object is non-empty: one line per missing section and its cause.

7. **Bottom line** — one bolded sentence: either the specific action to take, or "No action needed — nothing flagged this run."

Always cite the actual numbers pulled by the script (credits, seconds, query IDs, percentages) — never a vague qualitative claim without the evidence behind it.

## Loop tier & future promotion

This skill is currently **Tier 1 (on-demand)** — it only runs when explicitly invoked, per the repo's rule that no skill starts above Tier 1 (`.claude/rules/loop-engineering.md`).

The `cost_anomalies` field the script already computes is what a future **Tier 3 (scheduled, product-loop)** version would use as its "should I speak at all" trigger — e.g. running daily and staying silent unless `cost_anomalies` is non-empty. Promoting this skill to Tier 3 is **not done here** — it would require its own explicit PR adding, per `.claude/rules/loop-engineering.md`:
- A schedule (e.g. daily) and a notification channel (Slack/Teams) wired before it goes live
- A per-run cost/token budget
- A documented pause/kill switch any team member can operate
- Logging of every run (timestamp, findings, outcome) for later audit

Until that PR happens, this skill stays on-demand only.
