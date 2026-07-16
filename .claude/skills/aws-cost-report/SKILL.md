---
name: aws-cost-report
description: Reports AWS cost by service over a lookback window, a month-over-month comparison, and day-over-day cost anomalies with a dollar-value floor so near-zero costs never register as false alarms. Use when asked about AWS spend, cost trends, cost anomalies, or a month-over-month AWS cost comparison.
risk: read-only
loop-tier: on-demand
---

## Purpose

"What's our AWS spend doing, and did anything jump recently?" today means opening the Billing console, reading the cost breakdown by eye, and manually comparing this month to last month. This skill answers it in one command from Cost Explorer's own API — real dollar figures, a fair month-over-month comparison, and anomaly detection that won't cry wolf over a service that went from a fraction of a cent to another fraction of a cent.

## When to use

Use this skill when asked about: AWS cost, AWS spend, cost trends or anomalies, which AWS service is driving spend, or a month-over-month AWS cost comparison.

## Steps

1. Confirm AWS credentials are available: a named profile (`--profile NAME`, read from the standard `~/.aws/credentials` file) or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` environment variables. Configured locally by the user ahead of time — this skill never asks for or handles credentials directly, and never accepts a key on the command line.
2. **Cost Explorer must be enabled on the account first** — a one-time, per-account setting (Billing and Cost Management → Cost Explorer → Enable). AWS can take up to 24 hours to backfill historical data after first enabling it; a report run during that window will show an honest, empty-but-not-crashed result (see Verified live below).
3. Run `python {{SKILL_DIR}}/scripts/aws_cost_report.py [--profile NAME] [--days N] [--min-anomaly-dollars N]` (`--days` default 7, `--min-anomaly-dollars` default 1.0).
4. The script calls Cost Explorer's `GetCostAndUsage` directly:
   - Daily unblended cost broken down by service over the window
   - A month-over-month comparison, deliberately day-aligned: this month's cost-to-date vs. the *same number of days* at the start of the prior month, never a full prior month vs. a partial current month, since that comparison would be misleading
   - Cost anomalies: a service whose latest day is >50% above its trailing average — **and** whose latest-day cost is at least `--min-anomaly-dollars`. The dollar floor exists specifically so a service going from $0.0001 to $0.0002 doesn't register as a "100% increase" that means nothing — the exact gap found (and, at the time, deferred) in `snowflake-cost-audit`, closed here from the start instead of rediscovered later.
   - A distinct case for a service with **no prior-period cost at all** (a brand-new charge) — flagged with a `note` rather than a fabricated percentage, since there's no trailing average to divide by.
5. **Cost Explorer's API only has an endpoint in `us-east-1`**, regardless of which region the account's resources actually run in — the script pins the client there deliberately; this isn't a leftover default.
6. If Cost Explorer isn't enabled yet, or data is still backfilling, or the credentials aren't authorized for Cost Explorer, that section's real error goes into `errors` — never a crash, never a silently empty report presented as complete.
7. Turn the JSON into a Markdown report (see Output format). If no anomalies exist, say so plainly.

## Helper scripts

- `{{SKILL_DIR}}/scripts/aws_cost_report.py` — calls AWS Cost Explorer via `boto3` using the caller's own credential chain (named profile or environment variables); never reads, writes, or asks for credentials directly. No dependency beyond `boto3`.

## Output format

Render as Markdown:

1. **Summary line** — bolded: total cost over the window, plus a ⚠️ callout if `cost_anomalies` is non-empty, followed by the region note (Cost Explorer is a us-east-1-only API regardless of resource region).
2. **Cost by service (table)**

   | Service | Day | Unblended Cost |
   |---|---|---|
   | `Amazon EC2` | 2026-07-15 | 12.40 |

3. **Cost anomalies** — only if non-empty:

   | Service | Latest Day | Latest Cost | Trailing Avg | Increase |
   |---|---|---|---|---|
   | `AWS Lambda` | 2026-07-15 | 8.20 | 2.10 | +290% |

4. **Month-over-month** — this month's cost-to-date vs. the same number of days at the start of last month, with the percentage change; state plainly that this is a day-aligned partial comparison, not full-month-vs-full-month.
5. **Partial-data warning** — only if `errors` is non-empty: one line per missing section and its real cause (commonly: Cost Explorer not yet enabled, or still backfilling).
6. **Bottom line** — one bolded sentence: the single highest-cost anomaly to look into, or "No action needed — nothing flagged this run."

Always cite the actual dollar figures and service names pulled by the script — never a vague qualitative claim without the number behind it.

## Verified live (not just fixture-tested)

Built and tested against a real AWS account (2026-07-16). Two real findings from that testing, both worth knowing about:

1. **The credentials supplied were AWS account root-user access keys** (`arn:aws:iam::<account>:root`), not an IAM user. AWS explicitly recommends against creating access keys for root at all — root has unrestricted access to the entire account with no way to scope it down after the fact. This is the AWS-side equivalent of the standing-`ACCOUNTADMIN`-grant finding `snowflake-role-audit` flags on Snowflake. Recommendation given at the time: delete the root access key after testing and create a dedicated IAM user with only Cost Explorer read access for any ongoing use.
2. **Cost Explorer was not yet enabled on the account.** Visiting the Billing console auto-enabled it, but AWS documents up to a 24-hour delay before historical data backfills. The very first live run against this account hit that real, undocumented-until-now state — `GetCostAndUsage` returned `DataUnavailableException` for every section. The script handled it exactly as designed: each section's real error landed in `errors`, the report still returned valid JSON with honest zeros and empty lists, and nothing was fabricated to look like a complete report. `test-fixtures/sample-output.json` is that actual real run.

## Verification status per branch (honest status, not hidden)

| Path | Live account | Unit-tested |
|---|---|---|
| Graceful degradation when Cost Explorer isn't enabled / still backfilling | ✅ (real `DataUnavailableException` on the first-ever run) | — |
| Daily cost by service, a real non-empty result | ❌ pending — account was still backfilling at build time | — |
| Month-over-month comparison, a real non-empty result | ❌ pending — same reason | — |
| `detect_service_anomalies`: a real percentage-and-dollar-floor anomaly | ❌ no real cost data existed yet to test against | ✅ `test-fixtures/test_aws_cost_report.py` (real anomaly, near-zero swing correctly suppressed by the dollar floor, same data correctly flagged once the floor is lowered below it, brand-new-service no-prior-average case, cross-service independence, clean/stable case, exact-50%-boundary non-fire) |
| Root-user credential detection | Not automated — this is a one-time, human-reported finding from the live test above, not a check the script performs. A future version could warn if the caller's ARN ends in `:root`. |

**What's still open:** the happy path (real, non-zero cost data flowing through `daily_cost_by_service`, `month_over_month`, and a genuine anomaly) has not yet been observed live — the account's Cost Explorer was still backfilling at build time. This should be re-run once data appears (could be within 24h of first enabling) to close that gap honestly, the same way `snowflake-cost-audit`'s anomaly detector went unverified live until a real spike eventually occurred.

## Loop tier & future promotion

Currently **on-demand (Tier 1)**, per repo rule that no skill starts above Tier 1.

A reasonable future Tier 3 (scheduled, notify-only) candidate later — daily Cost Explorer check, notify only on a new anomaly — following the same explicit-PR promotion checklist as `snowflake-cost-audit`'s promotion (schedule, notification channel, budget, kill switch, run logging). Not done here.
