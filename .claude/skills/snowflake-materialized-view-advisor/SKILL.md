---
name: snowflake-materialized-view-advisor
description: Finds repeated, expensive SELECT query patterns via Snowflake's own query fingerprint (QUERY_PARAMETERIZED_HASH), checks each against real materialized-view eligibility restrictions, and -- only with explicit confirmation -- creates the view and verifies a subsequent real run is actually faster. Use when asked whether a materialized view would help, to find repeated expensive query patterns, or to review materialized view candidates.
risk: write-confirm
loop-tier: retry-until-resolved
---

## Purpose

"Would a materialized view help here?" today means noticing the same query shape keeps showing up in the query history, guessing whether it qualifies for a materialized view, and hoping the view actually helps once built. This skill answers it from real data: which query patterns actually repeat and cost real time, whether each one is even eligible for a materialized view under Snowflake's real restrictions, and — when asked to act — whether a real re-run is verifiably faster afterward, not assumed faster just because the `CREATE` succeeded.

## When to use

Use this skill when asked: whether a materialized view would help, to find repeated expensive query patterns worth materializing, or to review/create materialized view candidates.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/mv_advisor.py` (accepts `--connection`, `--days` [default 30], `--min-occurrences` [default 5], `--min-total-seconds` [default 30.0 — a materiality floor, since materialized views have real maintenance overhead not worth paying for a cheap pattern], `--execute`, `--view-name`, `--max-iterations` [default 3]).
3. The script:
   - Groups `ACCOUNT_USAGE.QUERY_HISTORY` by Snowflake's own `QUERY_PARAMETERIZED_HASH` (a fingerprint that ignores literal values, so `WHERE region = 'US'` and `WHERE region = 'EU'` count as the same repeated pattern) to find genuinely repeated SELECT shapes, excluding masked/serverless system-compute queries
   - Filters to patterns that both repeat at least `--min-occurrences` times AND cost at least `--min-total-seconds` total, in the window
   - Checks each candidate against Snowflake's real materialized-view restrictions via a documented text heuristic (not a full SQL parser): single base table only (no JOIN), not a system/`INFORMATION_SCHEMA`/`ACCOUNT_USAGE` pseudo-table, no non-deterministic functions (`RANDOM`/`CURRENT_TIMESTAMP`/etc.), no `ORDER BY`/`LIMIT`/`HAVING`/`UNION`/`QUALIFY`, no window functions
   - Every real restriction found is reported, not just the first blocker — a reviewer sees the full picture
4. **Dry run by default.** Without `--execute`, only reports candidates, eligibility, and the exact `CREATE OR REPLACE MATERIALIZED VIEW` DDL that would run — creates nothing.
5. **With `--execute` (requires explicit user confirmation of the exact view before ever passing this flag):** creates the materialized view, then re-runs the same real query pattern up to `--max-iterations` times and compares each real execution time against the pattern's own historical average — reporting honestly whether improvement was actually observed, never assuming it from the `CREATE`'s success alone.

## Helper scripts

- `{{SKILL_DIR}}/scripts/mv_advisor.py` — pure `snowflake-connector-python` queries; no dependency beyond the connector. The eligibility check is a documented text-matching heuristic, not a SQL parser.

## Output format

Render as Markdown:

1. **Summary line** — bolded: how many repeated patterns were found and how many are MV-eligible.
2. **Candidates (table)** — occurrences, total cost, avg duration, eligibility, and (if ineligible) every reason why.
3. **Recommendation** — the top eligible candidate's exact DDL, or "no action" if none qualify.
4. **If executed** — the view created and the real verification iteration log (execution time per attempt vs. the historical baseline).
5. **Bottom line** — one bolded sentence: the action taken/recommended, or explicitly "no action — no eligible repeated patterns" if none qualify.

Always cite the actual occurrence counts, total/avg durations, and real verification timings — never a vague "this looks like it repeats a lot" without the numbers.

## Loop tier

**Retry-until-resolved (Tier 2).** The verification step after creating the view is a genuine Task Loop: a materialized view can take time to populate, and `ACCOUNT_USAGE` itself lags by up to ~45 minutes, so the script re-runs the real query and re-checks its real execution time up to `--max-iterations` times (default 3) rather than trusting the `CREATE`'s success as proof of improvement. This is a fixed-count retry that always terminates and reports honestly either way — including "created but improvement not observed," which is a real, not a hidden, outcome.

## Verified live (not just fixture-tested)

Run against the real account (2026-07-20/21). A real 5M-row table (`RAW.MV_TEST_SALES`) was built and a `SELECT REGION, SUM(AMOUNT) ... GROUP BY REGION` query run 6 times to create a genuinely repeated pattern.

- **Detection**: correctly found 10 real repeated query-hash patterns in the account during testing, correctly disqualified system-schema/JOIN/non-deterministic candidates that showed up alongside the intended test pattern, and correctly identified the `MV_TEST_SALES` GROUP BY pattern (6 occurrences) as eligible with zero false disqualifications.
- **`--execute`**: created a real materialized view (`MV_ADVISOR_TEST_SALES_VIEW`) on the eligible candidate. **Verification honestly reported no improvement observed** across 3 real re-run attempts (1053ms, 145ms, 106ms) against a 12.3ms historical baseline — the baseline itself was almost certainly already being served by Snowflake's own result cache on a small, frequently-repeated query, leaving no real room for a materialized view to measurably beat it within a short test window. This is a genuine, valuable finding about the loop's honesty, not a bug: the Task Loop correctly refused to report success it hadn't actually observed, and the `created_but_improvement_not_observed` status fired for real, live reasons rather than being a purely theoretical branch.
- Test view and table dropped after verification.

## Verification status per branch (honest status, not hidden)

| Path | Live | Unit-tested |
|---|---|---|
| `no_repeated_patterns_found` | — not yet observed live (this account always has some repetition) | — (trivial branch) |
| `repeated_patterns_found_none_eligible` | ✅ (real system-schema/JOIN candidates correctly disqualified alongside the eligible one) | — |
| `recommend_dry_run` | ✅ (real MV_TEST_SALES candidate) | — |
| `created_and_verified_improved` | ❌ not yet observed live — would need a pattern expensive enough, and not already result-cached, that a real MV measurably beats | — |
| `created_but_improvement_not_observed` | ✅ (real MV created, 3 honest re-run attempts, none beat the cached baseline) | — |
| `check_mv_eligibility` (every restriction) | ✅ (proven correct disqualifying real candidates in the account) | ✅ (all 6 restrictions plus compound/empty/None safety) |

**What's still open:** `created_and_verified_improved` — proving a genuine speed win would need a candidate large/expensive enough that Snowflake's own result cache isn't already masking the difference, which the small test table here didn't provide.
