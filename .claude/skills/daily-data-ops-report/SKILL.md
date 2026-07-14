---
name: daily-data-ops-report
description: One morning summary across both platforms -- failed Databricks jobs, Snowflake cost + anomalies, cluster config risks, and freshness of explicitly watched tables -- by orchestrating four sibling skills in one command. Use when asked for a morning check, daily ops summary, or "how is the data platform doing today."
risk: read-only
loop-tier: on-demand
---

## Purpose

The morning check today means opening Databricks (any failed jobs?), Snowsight (what did yesterday cost?), the cluster list, and spot-checking key tables for staleness. This skill is that whole routine in one command — the "one morning summary" deliverable named in the original project proposal.

## When to use

Use this skill when asked for: a morning check, daily data-ops summary, "how's the platform today," or a combined health report across Databricks and Snowflake.

## Steps

1. Confirm both connections are configured (Snowflake `connections.toml`; Databricks env vars or `~/.databrickscfg`). This skill never handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/daily_report.py --days 1 [--watch-table SCHEMA.TABLE ...]`.
   - `--watch-table` (repeatable) names the tables whose freshness matters — the report **never guesses** which tables to watch. None configured = the freshness section says so plainly.
3. The script calls four sibling skills' scripts directly (real cross-skill dependencies, not reimplementations — fixes there flow through here):
   - `databricks-job-triage` → failed runs in the window, per-task
   - `snowflake-cost-audit` → credits used + cost anomalies + flagged warehouses
   - `databricks-cluster-audit` → cluster config risks
   - `snowflake-data-quality` → freshness verdicts for each watched table (only *judged* columns count; historical columns are excluded by that skill's own semantics)
4. Each section degrades independently — one platform failing produces that section's real error in `errors`, never a silently missing section, and never takes down the other platform's data.
5. `total_findings` and `all_clear` summarize the whole report — the "should this stay quiet" signal a future scheduled (Tier 3) version would key on.

## Helper scripts

- `{{SKILL_DIR}}/scripts/daily_report.py` — pure orchestration; no data access of its own. Inherits every honesty rule of its four sources (e.g. cost scope note, config-risk-not-dollar-cost, exact-not-approximate counts).

## Output format

Render as Markdown, in this order — worst news first:

1. **Headline** — bolded: either "✅ All clear — no findings across both platforms" or "N findings" with a one-phrase breakdown (e.g. "1 cost anomaly, 3 cluster config risks, 6 stale columns").
2. **Failed jobs** — table (job, run ID, failed task, error) or "No failed runs in the window."
3. **Snowflake cost** — credits used (with the user-managed-warehouses scope note), anomalies table if any.
4. **Cluster config risks** — table, with the pattern callout if one issue repeats across clusters.
5. **Freshness** — per watched table: stale columns with ages, or "fresh." If not configured, say so.
6. **Errors** — any section that failed, with its real error.
7. **Bottom line** — one bolded sentence: the single most important thing to act on this morning, or confirmation nothing needs attention.

## Verified live (not just fixture-tested)

First live run (both platforms, 2 watched tables) returned a genuinely new finding: **`snowflake-cost-audit`'s anomaly detector fired live for the first time ever** — `INSIGHTOPS_WH` +96.6% vs. trailing average, caused by this project's own testing queries earlier that day. The morning report's first real act was catching its own development activity as a cost anomaly. Also correctly reported: zero failed jobs (true), the known 3-cluster config pattern, and 560-day staleness on the watched demo tables (true — static demo data). `errors` empty; all four sections completed.

## Verification status (honest)

| Path | Status |
|---|---|
| All four sections, live, both platforms | ✅ |
| Live cost-anomaly finding flowing through | ✅ (first-ever live firing of that detector) |
| Per-section degradation | ✅ inherited pattern, live-verified on `unified-cost-optimizer`; not separately re-verified here |
| `all_clear: true` (a genuinely quiet day) | ❌ never observed — every real run so far has had findings; the quiet path is simple (`findings == 0 and not errors`) but unproven |

## Loop tier & future promotion

**On-demand (Tier 1).** This is the single strongest Tier-3 candidate in the catalog — it was *designed* as a scheduled morning report (07:00 daily, post to a channel, stay quiet when `all_clear`). Promotion requires the standard explicit PR: schedule, notification channel, per-run budget, kill switch, run logging — same checklist as `snowflake-cost-audit`'s promotion. Not done here; per repo rules it should earn promotion through a period of reliable on-demand use first.
