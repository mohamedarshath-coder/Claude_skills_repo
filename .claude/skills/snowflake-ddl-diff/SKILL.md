---
name: snowflake-ddl-diff
description: Compares two Snowflake schemas' real table/column structure and, only with explicit confirmation, generates and applies safe additive migration DDL (CREATE TABLE, ADD COLUMN only) to bring the target in line with the source, retrying until converged. Never auto-generates DROP or type-change DDL -- those are always reported for manual review. Use when asked to compare schemas across environments, or to migrate a target schema toward a source schema's structure.
risk: write-confirm
loop-tier: retry-until-resolved
---

## Purpose

"Is our target environment's schema actually in sync with source, and can we bring it up to date safely?" today means manually diffing `DESCRIBE TABLE` output across environments and hand-writing migration DDL, hoping nothing is missed and nothing destructive slips in by accident. This skill compares the two schemas' real, live structure and — only when explicitly told to — generates and applies the safe subset of that migration automatically, retrying until the target converges or a hard cap is hit.

**This is the repo's only write-confirm, retry-until-resolved skill.** It is built around one non-negotiable rule: it will create tables and add columns automatically, because those are additive and reversible (`DROP` undoes a `CREATE`/`ADD COLUMN` cleanly) — but it will **never** auto-generate a `DROP` or a column type change, because those can lose data and aren't safely reversible. Those are always surfaced as "manual review required" and never touched, `--execute` or not.

## When to use

Use this skill when asked to: compare schema structure across two environments (e.g. dev vs. qa, qa vs. prod), migrate a target schema toward a source schema, or find what's out of sync between two schemas.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. **Always run without `--execute` first.** `python {{SKILL_DIR}}/scripts/ddl_diff.py --source-schema NAME --target-schema NAME` reports the full diff and the exact DDL that *would* run — it changes nothing. Read the plan.
3. **Present the exact planned DDL to the user and get explicit confirmation before ever passing `--execute`.** This mirrors how every other write-capable action in this repo works — a user approving a dry-run once does not mean blanket approval for future runs; confirm each time.
4. Only after confirmation, run again with `--execute` (accepts `--max-iterations`, default 5). The script then:
   - Applies only safe, additive DDL: `CREATE TABLE` for a table missing in the target (with the source's real column definitions), `ALTER TABLE ... ADD COLUMN` for a column missing in the target
   - Re-diffs and repeats, stopping the moment no safe-fixable difference remains (**converged**) — this is the Task Loop: each retry is driven by a genuinely fresh diff against real Snowflake metadata, not a fixed script
   - Stops immediately, without retrying further, if an iteration applies zero successful statements (a real, safe circuit-breaker against looping on a permissions error or similar)
   - Hard-caps at `--max-iterations` (default 5, well above the 1-2 iterations real convergence should take) per `.claude/rules/loop-engineering.md`
5. **Never auto-generates or applies:** a `DROP TABLE`/`DROP COLUMN` for anything present in the target but not the source, or an `ALTER COLUMN` type change for a column whose type differs between source and target. Both are reported under `manual_review_required` with the real evidence, every single run, and are never acted on regardless of `--execute` or how many iterations run.
6. Report the full audit log (every DDL statement attempted, its real timestamp, and its real outcome) — an unattended write-capable loop must be auditable after the fact, per this repo's loop-engineering rules.

## Helper scripts

- `{{SKILL_DIR}}/scripts/ddl_diff.py` — pure `snowflake-connector-python` queries against `INFORMATION_SCHEMA` plus DDL generation/execution; no dependency beyond the connector.

## Output format

Render as Markdown:

1. **Summary line** — bolded: source/target schema, converged or not, in how many iterations, and whether this was a dry run or a real execution.
2. **Safe actions applied / planned (table)** — the DDL statements, with real outcome if executed.
3. **Manual review required (table)** — every extra-object and type-mismatch item, with its real evidence; state plainly that these are never auto-touched.
4. **Audit log** — only if `--execute` was used: every statement attempted, its timestamp, and its real success/error outcome.
5. **Bottom line** — one bolded sentence: "Converged — target now matches source for all safely-fixable differences" plus a reminder of what still needs manual attention, or "Hit the retry cap without converging — investigate before re-running."

## Verified live (not just fixture-tested)

Built and tested against two real, deliberately-constructed Snowflake schemas (`DDL_TEST_SOURCE`, `DDL_TEST_TARGET`) covering all 4 real scenarios at once: a table missing entirely from the target, a table present in both but missing a column in the target, a column present in both with a genuine type mismatch, and a table present in the target but not the source.

**A real, serious bug was found and fixed on the first `--execute` run**: the generated DDL was not schema-qualified (`CREATE TABLE "PRODUCTS"` instead of `CREATE TABLE "DDL_TEST_TARGET"."PRODUCTS"`). Snowflake silently resolved the unqualified name against the connection's current default schema instead of the intended target — the statement succeeded with no error at all, but created the table in the *wrong schema entirely*. Caught immediately by verifying the target schema afterward and finding the table wasn't there; the mistakenly-created table in the wrong schema was cleaned up, and every generated statement is now fully schema-qualified. Re-verified after the fix: the table landed in the correct schema, confirmed independently via a direct `INFORMATION_SCHEMA` query.

**All 4 diff scenarios verified correct on the first fixed run**, plus 3 distinct convergence-loop states, all live:
- Dry run correctly reported the plan and touched nothing (`audit_log` empty)
- `--execute` correctly converged in 2 iterations: iteration 1 applied both safe statements successfully, iteration 2 found zero remaining safe-fixable differences and stopped
- Re-running against already-converged schemas correctly reported `converged: true` in 1 iteration with zero DDL applied
- Running with a deliberately tight `--max-iterations 1` against an out-of-sync pair correctly applied what it could within the cap and reported `max_iterations_reached`, not a false "converged"
- Throughout every run, the 2 manual-review items (the type mismatch, the extra table) were correctly never touched — not by dry run, not by `--execute`, not across any number of iterations

## Verification status per branch (honest status, not hidden)

| Path | Live |
|---|---|
| Dry run (no `--execute`), plan reported, nothing touched | ✅ |
| `create_table` DDL, schema-qualified correctly (post-fix) | ✅ |
| `add_column` DDL, schema-qualified correctly (post-fix) | ✅ |
| `type_mismatch` correctly flagged, never auto-fixed | ✅ |
| `extra_table_in_target` correctly flagged, never auto-dropped | ✅ |
| Full convergence via `--execute` (2 iterations) | ✅ |
| Already-converged re-run (1 iteration, zero DDL) | ✅ |
| `max_iterations_reached` (cap hit before natural convergence) | ✅ |
| `extra_column_in_target` | ✅ (unit-tested; not yet hit in the live scenario, which used an extra *table* for that case) |
| A real permission error mid-loop (circuit-breaker: stop if zero statements succeed in a round) | not yet observed live — this account's role has full DDL rights on its own test schemas |

## Loop tier & safety limits

This is the repo's second **Task Loop (retry-until-resolved)** skill, and its first **write-confirm** one. Per `.claude/rules/loop-engineering.md`:
- **Hard retry cap:** `--max-iterations` (default 5) — convergence should take 1-2 rounds in practice, so hitting the cap is itself a signal something is wrong.
- **Each retry changes something real:** every iteration re-fetches live `INFORMATION_SCHEMA` state and only continues if the prior round actually applied at least one successful statement; a round that fixes nothing stops the loop immediately rather than spinning.
- **Full audit log:** every DDL statement ever executed is recorded with a real timestamp and real outcome, success or the exact error — required for any loop above on-demand, doubly so for one that writes.
- **Confirm every time:** dry run is the only default; `--execute` must be passed explicitly, and per this repo's standing rule, the agent must present the exact planned DDL and get the user's explicit go-ahead before ever passing it — a prior approval does not carry forward to a later run.
- **Destructive actions are structurally impossible, not just discouraged:** `DROP` and type-change DDL are never generated by this script at all, under any flag combination — the safety boundary is enforced in code, not just in the prompt.
