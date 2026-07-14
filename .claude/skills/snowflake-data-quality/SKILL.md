---
name: snowflake-data-quality
description: Runs exact data-quality checks on a Snowflake table -- duplicate detection on a key column, per-column null counts, and freshness of date columns -- reporting counts and aggregates only, never row values. Use when asked to check a table for duplicates, nulls, staleness, or general data quality.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Is this table clean?" usually means someone hand-writes three or four ad-hoc queries: a `COUNT DISTINCT` for duplicates, `COUNT(*) - COUNT(col)` per column for nulls, a `MAX(date)` for staleness. This skill runs all of them, **exactly** (no approximations), in one command — and reports aggregates only, never actual row values, per the repo's no-client-data/PII ground rule.

## When to use

Use this skill when asked to: check a table for duplicates, audit nulls, check whether data is stale/fresh, or run a general data-quality check on a Snowflake table.

## Steps

1. Confirm the connection profile (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Ask the user which column should be unique if it isn't obvious — the script **never guesses the key column**. Run:
   `python {{SKILL_DIR}}/scripts/data_quality.py --schema NAME --table NAME [--key-column COL] [--freshness-threshold-days N] [--freshness-columns COL ...]`
3. Three exact checks:
   - **Duplicates** (only if `--key-column` given): exact `COUNT DISTINCT` vs. non-null key count, how many keys have duplicates, and the worst key's occurrence count. **Reports counts only, never the duplicate key values themselves** — a key value can itself be identifying.
   - **Nulls**: exact null count and percentage per column, one aggregate query.
   - **Freshness**: days since the latest value in each date/timestamp column. **The stale verdict is a judgment, applied carefully**: if `--freshness-columns` names the recency signals explicitly, only those are judged; otherwise obviously-historical columns (birth dates) are *measured but not judged* — a birth date being decades old is correct, not stale.
4. Render as Markdown tables. State plainly which checks passed — a clean table is a valid, useful result.
5. Never present these results as exhaustive data quality: this covers uniqueness/completeness/recency. It does not validate ranges, referential integrity, or format correctness.

## Helper scripts

- `{{SKILL_DIR}}/scripts/data_quality.py` — exact aggregate queries only (`COUNT`, `COUNT DISTINCT`, `MAX`); no row-level data ever leaves Snowflake. Uses `snowflake-connector-python` with a named connection profile.

## Output format

1. **Summary line** — bolded: pass/fail per check, e.g. "**Duplicates: PASS · Nulls: PASS · Freshness: 2 stale columns.**"
2. **Duplicates** — the exact numbers (total rows, non-null keys, distinct keys), and if failed: how many keys duplicated + worst offender count. Never key values.
3. **Nulls (table)** — only columns with nulls > 0; if none, one line: "No nulls in any column."
4. **Freshness (table)** — column, days since latest value, verdict (`stale` / `ok` / `measured only` with the reason).
5. **Bottom line** — one bolded sentence: the single most important action, or "Table is clean on all three checks."

## Verified live (not just fixture-tested)

First live run against `RAW.RAW_CUSTOMERS` (6,000 real rows) produced two genuinely instructive results:

1. **It prevented a false alarm.** `snowflake-schema-explorer`'s approximate profile had suggested `CUSTOMER_ID` might have ~113 duplicates (~5,887 approx distinct of 6,000). The exact check: **6,000 distinct of 6,000 — zero duplicates.** The approximation was HyperLogLog under-estimation (~1.9%), exactly the error mode that skill's mandatory caveat warns about. Acting on the approximate number would have sent someone hunting for duplicates that don't exist.
2. **It exposed a design flaw in this skill itself, which was then fixed:** the first version flagged `DATE_OF_BIRTH` as "stale" at 9,328 days — semantically absurd (a birth date is supposed to be old). The stale *verdict* now applies only to plausible recency signals; historical columns are measured but not judged.

## Verification status (honest, per check)

| Check | Live | Unit-tested |
|---|---|---|
| Duplicates: clean table (exact zero, 6000/6000) | ✅ | — (detection logic lives in SQL, not Python — a stub cursor would only test dict packaging) |
| Duplicates: table WITH real duplicates | ❌ no such table exists in the account; awaits a real case | — (same reason) |
| Nulls: clean table | ✅ | — (same reason) |
| Freshness verdict semantics: stale / ok / historical-exemption / explicit `--freshness-columns` | ✅ all three verdicts occurred live | ✅ `test-fixtures/test_quality.py` (7 tests — the verdict logic is Python, and it's the part that was actually wrong once) |

## Loop tier & future promotion

**On-demand (Tier 1).** A natural Tier 3 candidate later (a scheduled freshness watchdog is already in the backlog as `freshness-watchdog`) — same promotion checklist as every other skill, not done here.
