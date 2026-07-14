---
name: databricks-cluster-audit
description: Audits Databricks all-purpose clusters for idle-cost risk (missing/high auto-termination) and oversized fixed-size configurations, using real cluster config data. Use when asked about Databricks cluster costs, idle clusters, or cluster cost hotspots.
risk: read-only
loop-tier: on-demand
---

## Purpose

Idle Databricks clusters are a classic hidden cost: someone spins up a personal or shared cluster, forgets about it, and it either runs indefinitely or auto-suspends so late that the idle time alone racks up real spend. This skill audits every cluster's actual configuration and flags concrete, evidence-backed risks — not a generic "review your clusters" nudge.

## When to use

Use this skill when asked about: Databricks cluster costs, idle clusters, cluster cost hotspots, or a cluster configuration audit.

## Steps

1. Confirm the Databricks connection: `DATABRICKS_HOST`/`DATABRICKS_TOKEN` env vars, or a named profile in `~/.databrickscfg` (pass via `--profile`). Configured locally by the user ahead of time — this skill never asks for or handles the token directly.
2. Run `python {{SKILL_DIR}}/scripts/cluster_audit.py` (accepts `--idle-threshold-minutes N`, default 120, and `--large-worker-threshold N`, default 10, to adjust the fixed thresholds below).
3. The script lists every cluster and flags:
   - **No auto-termination** (high severity) — `autotermination_minutes` is 0 or unset on a UI/API-created cluster, meaning it will run indefinitely once started
   - **High auto-termination** (medium) — set, but above the idle threshold (default 2 hours)
   - **Large fixed-size cluster** (medium) — a fixed worker count above the threshold with no autoscale configured
   - **Currently running** (info) — actively costing compute right now, worth confirming it's expected
4. **Job and Pipeline (DLT) clusters are never flagged for auto-termination.** Their lifecycle is controlled by the job/pipeline framework itself — `autotermination_minutes: 0` is *normal* for these, not a risk. Only `UI`/`API`-created (interactive) clusters are evaluated for that heuristic. Treating a job cluster's `0` as a finding would be a false positive.
5. Turn the JSON into a Markdown report (see Output format). If a cluster has zero issues, don't elaborate — a clean cluster needs no commentary.

## Helper scripts

- `{{SKILL_DIR}}/scripts/cluster_audit.py` — lists clusters via the Databricks Clusters API and applies documented, fixed thresholds per cluster source. Uses `databricks-sdk`'s `WorkspaceClient`, which reads the connection from env vars or a named profile; never handles the token directly.

## Output format

Render as Markdown:

1. **Summary line** — bolded: total clusters, breakdown by source (UI/JOB/PIPELINE), how many have at least one issue.
2. **Flagged clusters (table)**

   | Cluster | Source | Issue | Evidence | Recommendation |
   |---|---|---|---|---|
   | `Jane's Personal Compute Cluster` | UI | No auto-termination | `autotermination_minutes = 0` | Set an autotermination value |

3. **Pattern callout** — if the *same* issue appears on 2+ clusters (e.g. several personal clusters sharing one misconfigured default), say so explicitly — that's usually an org-wide cluster policy worth fixing once, not N individual clusters to fix separately.
4. **Bottom line** — one bolded sentence: the single highest-severity issue to act on first, or "No cluster issues found."

Always cite the actual cluster name, ID, and threshold values behind every flagged issue.

## Loop tier & future promotion

Currently **Tier 1 (on-demand)**, per repo rule (`.claude/rules/loop-engineering.md`) that no skill starts above Tier 1.

This is a reasonable future Tier 3 (scheduled, product-loop) candidate — e.g. weekly, notifying only on a *new* flagged cluster since the last run. Promotion follows the same explicit-PR checklist as `snowflake-cost-audit`'s promotion (schedule, notification channel, per-run budget, kill switch, run logging) and is **not done here**.
