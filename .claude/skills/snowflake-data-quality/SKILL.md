---
name: snowflake-data-quality
description: Runs exact data-quality checks on a Snowflake table -- duplicates, nulls, freshness, value ranges, referential integrity, format correctness, and business-rule consistency -- reporting counts and aggregates only, never row values. Use when asked to check a table for duplicates, nulls, staleness, out-of-range values, orphaned foreign keys, malformed data, or business-rule violations.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Is this table clean?" usually means someone hand-writes three or four ad-hoc queries: a `COUNT DISTINCT` for duplicates, `COUNT(*) - COUNT(col)` per column for nulls, a `MAX(date)` for staleness. This skill runs all of them, **exactly** (no approximations), in one command — and reports aggregates only, never actual row values, per the repo's no-client-data/PII ground rule.

## When to use

Use this skill when asked to: check a table for duplicates, audit nulls, check whether data is stale/fresh, or run a general data-quality check on a Snowflake table.

## Steps

1. Confirm the connection profile (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Ask the user which column should be unique, which ranges/formats/relationships/rules matter — the script **never guesses any of this**. Every check beyond nulls is opt-in and explicit. Run:
   `python {{SKILL_DIR}}/scripts/data_quality.py --schema NAME --table NAME [--key-column COL] [--freshness-threshold-days N] [--freshness-columns COL ...] [--range-check COL MIN MAX] [--fk-check CHILD_COL PARENT_SCHEMA.TABLE PARENT_COL] [--format-check COL REGEX] [--rule-check NAME "SQL_BOOLEAN_EXPR"]`

   (`--range-check`, `--fk-check`, `--format-check`, `--rule-check` are all repeatable — pass each once per column/relationship/rule you want checked.)
3. Seven exact checks, all evidence-based, all counts/percentages only — never row values:
   - **Duplicates** (only if `--key-column` given): exact `COUNT DISTINCT` vs. non-null key count, how many keys have duplicates, worst key's occurrence count. **Never the duplicate key values themselves** — a key value can itself be identifying.
   - **Nulls**: exact null count and percentage per column, one aggregate query. Always runs.
   - **Freshness**: days since the latest value in each date/timestamp column. The stale verdict only applies to plausible recency signals — historical columns (birth dates) are measured but not judged. Always runs on date columns present.
   - **Range** (`--range-check`, repeatable): exact count of non-null values outside `[MIN, MAX]`.
   - **Referential integrity** (`--fk-check`, repeatable): exact orphan count via anti-join against a named parent table/column — genuinely checks the data, not just that a foreign key column exists.
   - **Format** (`--format-check`, repeatable): exact count of non-null values not matching a caller-supplied regex (`REGEXP_LIKE`). No built-in presets (no silent assumption about what "a valid email" looks like) — the caller supplies the pattern.
   - **Business rule** (`--rule-check`, repeatable): exact count of rows where a caller-supplied SQL boolean expression is false. **The riskiest check to interpret**: a high violation rate more often means the expression is wrong, not that the data is bad — see the honest example below before trusting a rule-check result at face value.
4. Render as Markdown tables. State plainly which checks passed — a clean table is a valid, useful result. Only report sections for checks that were actually requested (no `--range-check`? no range section in the output).
5. Never present these results as exhaustive data quality — even with every check type available now, business-rule checks are only as good as the rule supplied, and this still isn't a substitute for a domain expert reviewing the schema.

## Helper scripts

- `{{SKILL_DIR}}/scripts/data_quality.py` — exact aggregate queries only (`COUNT`, `COUNT DISTINCT`, `COUNT_IF`, anti-joins); no row-level data ever leaves Snowflake. Uses `snowflake-connector-python` with a named connection profile.

## Output format

1. **Summary line** — bolded: pass/fail per check actually run, e.g. "**Duplicates: PASS · Nulls: PASS · Freshness: 2 stale columns · Range (QUANTITY): PASS · Referential integrity (ORDER_ID): PASS.**"
2. **Duplicates** — the exact numbers, and if failed: how many keys duplicated + worst offender count. Never key values.
3. **Nulls (table)** — only columns with nulls > 0; if none, one line: "No nulls in any column."
4. **Freshness (table)** — column, days since latest value, verdict (`stale` / `ok` / `measured only` with the reason).
5. **Range checks (table)** — only if any were requested: column, range, out-of-range count/%.
6. **Referential integrity (table)** — only if any were requested: child column, parent, orphan count/%.
7. **Format checks (table)** — only if any were requested: column, non-matching count/%. Never show the actual non-matching values.
8. **Business rule checks (table)** — only if any were requested: rule name, violated count/%, and **if the violation rate looks implausibly high (e.g. >50%), say so explicitly and suggest double-checking the expression before treating it as a real data problem** — do not just report a scary percentage without that caveat.
9. **Bottom line** — one bolded sentence: the single most important action across every check that ran, or "Table is clean on all checks performed."

## Verified live (not just fixture-tested)

First live run against `RAW.RAW_CUSTOMERS` (6,000 real rows) produced two genuinely instructive results:

1. **It prevented a false alarm.** `snowflake-schema-explorer`'s approximate profile had suggested `CUSTOMER_ID` might have ~113 duplicates (~5,887 approx distinct of 6,000). The exact check: **6,000 distinct of 6,000 — zero duplicates.** The approximation was HyperLogLog under-estimation (~1.9%), exactly the error mode that skill's mandatory caveat warns about. Acting on the approximate number would have sent someone hunting for duplicates that don't exist.
2. **It exposed a design flaw in this skill itself, which was then fixed:** the first version flagged `DATE_OF_BIRTH` as "stale" at 9,328 days — semantically absurd (a birth date is supposed to be old). The stale *verdict* now applies only to plausible recency signals; historical columns are measured but not judged.

## Verified live: the four new checks (added after the original three)

Added on request to cover the gap explicitly named in the original Purpose ("does not validate ranges, referential integrity, or format correctness") — all four tested live against real tables the same session they were built:

- **Range** (`--range-check QUANTITY 1 100` on `RAW_ORDER_ITEMS`, 20,000 rows): 0 out of range. Clean, correctly reported.
- **Referential integrity** (`--fk-check ORDER_ID RAW.RAW_ORDERS ORDER_ID` and `--fk-check PRODUCT_ID RAW.RAW_PRODUCTS PRODUCT_ID`, same table): 0 orphans on both. Clean, correctly reported.
- **Format** (`--format-check EMAIL "^[^@]+@[^@]+\.[^@]+$"` on `RAW_CUSTOMERS`, 6,000 rows): 0 non-matching. Clean, correctly reported.
- **Business rule** (`--rule-check line_total_formula "ABS(LINE_TOTAL - (QUANTITY*UNIT_PRICE - DISCOUNT_AMOUNT)) < 0.01"` on `RAW_ORDER_ITEMS`): **89.88% violated (17,976 of 20,000)** — this is the honest cautionary example named in Step 5. Investigated before reporting: the match rate was ~10% *uniformly* across every `IS_RETURNED`/`FULFILLMENT_STATUS` combination, meaning the guessed formula doesn't reconcile with the data at all — this is synthetic demo data where `LINE_TOTAL` isn't actually derived from `QUANTITY`/`UNIT_PRICE`/`DISCOUNT_AMOUNT` by any simple rule, not a real 90% data-quality failure. **The check's SQL and counts are correct; the specific rule guessed was wrong.** This is exactly why a high business-rule violation rate must trigger a "double-check the expression" caveat rather than being reported as a finding at face value.

## Verification status (honest, per check)

| Check | Live | Unit-tested |
|---|---|---|
| Duplicates: clean table (exact zero, 6000/6000) | ✅ | — (detection logic lives in SQL, not Python — a stub cursor would only test dict packaging) |
| Duplicates: table WITH real duplicates | ❌ no such table exists in the account; awaits a real case | — (same reason) |
| Nulls: clean table | ✅ | — (same reason) |
| Freshness verdict semantics: stale / ok / historical-exemption / explicit `--freshness-columns` | ✅ all three verdicts occurred live | ✅ `test-fixtures/test_quality.py` (7 tests — the verdict logic is Python, and it's the part that was actually wrong once) |
| Range check: clean table | ✅ | — (SQL-only logic) |
| Range check: table WITH out-of-range values | ❌ no such case found yet | — |
| Referential integrity: clean (no orphans) | ✅ (both real FK relationships tested) | — |
| Referential integrity: table WITH real orphans | ❌ no such case found yet | — |
| Format check: clean (matching pattern) | ✅ | — |
| Format check: table WITH non-matching values | ❌ no such case found yet | — |
| Business rule: correctly identifies a wrong-formula guess (not a data bug) | ✅ — the `line_total_formula` case above | — |
| Business rule: a genuinely correct rule with real violations | ❌ not yet tested — would need a rule confirmed correct by someone who knows the actual business logic |

## Loop tier & future promotion

**On-demand (Tier 1).** A natural Tier 3 candidate later (a scheduled freshness watchdog is already in the backlog as `freshness-watchdog`) — same promotion checklist as every other skill, not done here.
