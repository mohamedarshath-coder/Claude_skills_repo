---
name: snowflake-schema-explorer
description: Describes tables and profiles columns in a Snowflake database -- table inventory with row counts/sizes, or per-column data types, nullability, null counts, and approximate distinct counts for a specific table. Use when asked to describe a table, explore a schema, generate a data dictionary, or profile columns.
risk: read-only
loop-tier: on-demand
---

## Purpose

"What tables exist here, and what's actually in this one?" is normally a manual trawl through Snowsight's object browser plus a few ad-hoc `DESCRIBE`/`SELECT COUNT` queries. This skill answers both halves in one command: an inventory view across a schema, or a detailed column-level profile of one table.

## When to use

Use this skill when asked to: describe a table, list tables in a schema, generate a data dictionary, or profile a table's columns (nulls, distinct values).

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Two modes, chosen by whether `--table` is given:
   - **Overview** (no `--table`): `python {{SKILL_DIR}}/scripts/schema_explorer.py [--schema NAME]` — lists every table/view (optionally scoped to one schema) with type, row count, and size in bytes, from `INFORMATION_SCHEMA.TABLES`.
   - **Detail** (`--schema NAME --table NAME`): describes that table's columns (name, type, nullable, default) from `INFORMATION_SCHEMA.COLUMNS`. Add `--profile` to also compute, in one aggregate query per table, each column's null count and **approximate** distinct count.
3. Render the result as a Markdown table, not a JSON dump.
4. **If `--profile` was used, always state that `distinct_count` is an approximation** (`APPROX_COUNT_DISTINCT`, HyperLogLog-based) — it can occasionally report a value slightly *higher* than the table's total row count at these volumes, which is expected estimation noise, not a data bug. Never present it as an exact count.
5. Views report `row_count`/`bytes` as `NULL` in overview mode (Snowflake doesn't persist size metadata for views) — say so plainly rather than showing a confusing blank.

## Helper scripts

- `{{SKILL_DIR}}/scripts/schema_explorer.py` — pure metadata queries (`INFORMATION_SCHEMA`) plus one optional aggregate profiling query per table. Uses `snowflake-connector-python` with a named connection profile; never handles credentials directly.

## Output format

**Overview mode** — a table:

| Schema | Table | Type | Rows | Size |
|---|---|---|---|---|
| `RAW` | `RAW_CUSTOMERS` | BASE TABLE | 6,000 | 401 KB |
| `PREP` | `STG_CUSTOMERS` | VIEW | — | — |

**Detail mode** — a table of columns, with profiling columns only if `--profile` was used:

| Column | Type | Nullable | Nulls | Approx. Distinct |
|---|---|---|---|---|
| `CUSTOMER_ID` | NUMBER | YES | 0 | ~5,887 |
| `EMAIL` | TEXT | YES | 0 | ~5,968 |

Followed by a one-line note if `--profile` was used: "Distinct counts are approximate (HyperLogLog) and may occasionally exceed the row count slightly — this is normal estimation noise, not a data error."

## Verified live (not just fixture-tested)

Tested against real tables in an actual Snowflake account. Overview mode against the `RAW` schema correctly listed all 7 real tables with accurate row counts and byte sizes. Detail mode with `--profile` against `RAW_CUSTOMERS` (6,000 real rows, 25 real columns) surfaced a genuine bug on the first attempt: every profiling value came back `null` due to a case-sensitivity mismatch between the SQL alias casing and the result-set key lookup. Fixed and re-verified — real counts now populate correctly (e.g. `CUSTOMER_ID`: 0 nulls, ~5,887 distinct of 6,000 rows). This same live run also surfaced the `APPROX_COUNT_DISTINCT` over-count behavior firsthand (`PHONE` reported ~6,080 distinct against exactly 6,000 rows), which is why the approximation caveat above is mandatory, not optional.

## Verification status (honest)

| Path | Status |
|---|---|
| Overview mode (tables + views listed) | ✅ live (7-table real schema) |
| Detail + `--profile` on a base table | ✅ live (6,000-row table; surfaced and fixed the case-sensitivity bug) |
| Detail + `--profile` on a **VIEW** | ✅ live — profiles correctly through the view (aggregate SELECTs, no table-metadata dependency) |
| Case-fix regression pinned | ✅ `test-fixtures/test_explorer.py` (4 unit tests — the exact live-caught bug: profiled values silently all-`None` when alias case didn't match the lowercased result keys) |
| Approx-distinct under-estimation | ✅ cross-confirmed: this skill reports ~5,887 distinct for a column `snowflake-data-quality` proved is exactly 6,000 — the documented HyperLogLog caveat, demonstrated twice |

## Loop tier

**On-demand (Tier 1).** No obvious scheduled variant — schema/column profiling is inherently a point-in-time, ask-when-needed operation, not something to run on a timer.
