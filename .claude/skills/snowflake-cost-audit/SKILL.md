---
name: snowflake-cost-audit
description: Audits Snowflake warehouse credit usage, flags idle/oversized warehouses, and surfaces the most expensive queries in a given window. Use when asked about Snowflake costs, warehouse spend, credit usage, or cost anomalies.
risk: read-only
loop-tier: scheduled-notify-only
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

## Verification status per branch (honest status, not hidden)

| Path | Live account | Unit-tested |
|---|---|---|
| Credits/config/top-queries reporting, clean day | ✅ (multiple real runs) | ✅ |
| `cost_anomalies` positive detection | ✅ fired live 2026-07-14: `INSIGHTOPS_WH` +96.6% vs trailing avg — caused by this project's own testing queries that day (caught via `daily-data-ops-report`'s first run) | ✅ `test-fixtures/test_cost_audit.py` (incl. exact-50%-threshold boundary, single-day skip, per-warehouse independence, zero-usage divide-by-zero guard, materiality floor) |
| `--min-anomaly-credits` materiality floor | ✅ closed 2026-07-17 — found live 2026-07-15 (`CLOUD_SERVICES_ONLY` reported "+92%" swinging between 0.00003 and 0.00006 credits, both financially meaningless), deliberately deferred at the time, now fixed using the same pattern already proven in `aws-cost-report`'s `detect_service_anomalies`. Re-run live against the real account afterward: the same near-zero pattern is now correctly suppressed by the default floor (0.05 credits) | ✅ (near-zero swing suppressed by default floor, same data fires once the floor is lowered below it, a real meaningful anomaly still fires with the default floor in place) |
| `possibly_oversized` warehouse flag | ❌ every real warehouse is X-Small | ✅ (2X-LARGE + 3s queries fires; busy LARGE and X-Small correctly don't) |
| `post_to_slack` notification | ❌ no Slack webhook configured yet (Track 7) | ✅ against a **real local HTTP listener** — actual POST sent, payload and Content-Type asserted; silent no-op when unset also verified |
| Scheduled run: healthy / kill-switch / finding log paths | ✅ (digest.log entries) | — |
| Unattended Task Scheduler trigger at 07:00 | ❌ unconfirmed — manual verification on the real desktop still pending | — |

## Loop tier: promoted to Tier 3 (scheduled, notify-only)

This skill has two invocation modes, both backed by the same `cost_audit.py` logic:

1. **On-demand (Tier 1)** — invoked by name or by a natural-language ask, exactly as documented above. Unchanged. Anyone can still run this interactively at any time.
2. **Scheduled (Tier 3, product-loop)** — `{{SKILL_DIR}}/scripts/scheduled_run.py`, run daily via each user's own local scheduler (Windows Task Scheduler / cron), promoted via its own explicit PR per `.claude/rules/loop-engineering.md`. It:
   - Runs a 1-day lookback daily and uses the existing `cost_anomalies` detector as its "should I speak at all" trigger
   - **Stays quiet when healthy** — a clean day writes exactly one line to `digest.log` and nothing else
   - **Notifies only on a finding** — a non-empty `cost_anomalies` or `flagged_warehouses` result appends a `FINDING` block to `digest.log` and posts to Slack if `SLACK_WEBHOOK_URL` is configured (falls back to `digest.log` only, since Slack workspace access isn't set up yet — see the proposal's Section 8.3 prerequisite)
   - **Kill switch:** set `COST_AUDIT_SCHEDULE_DISABLED=1` to pause the schedule without touching the scheduler config
   - **Run logging:** every run — healthy, skipped, or a finding — appends one line to `digest.log` (gitignored, per-machine, not committed) so the schedule is auditable after the fact

**Setup (per user, not committed):** each teammate who wants the scheduled version creates their own local Task Scheduler entry pointing `scheduled_run.py` at their own Snowflake connection profile — same reusability model as the on-demand skill, just automated. Example (Windows, run once):
```
schtasks /create /tn "snowflake-cost-audit-daily" /tr "python D:\path\to\.claude\skills\snowflake-cost-audit\scripts\scheduled_run.py" /sc daily /st 07:00
```

**What's still a documented gap, not silently ignored:** Slack notification degrades to `digest.log`-only until Track 7 (workspace admin approval, channel scoping) is set up. This is the correct interim state per the repo's rules — promoting the loop tier didn't require Slack to exist first, but the notification channel's current limitation is written down here, not hidden.
