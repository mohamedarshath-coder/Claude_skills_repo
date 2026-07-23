---
name: databricks-unity-catalog-audit
description: Flags concrete, evidence-backed access-control observations in a Unity Catalog metastore -- a broad built-in group (e.g. "account users") holding direct object-level privileges beyond baseline browse/use access, a catalog/schema owned directly by an individual user rather than a group or service principal, and an individual user holding a broad set of high-impact privileges directly rather than through a group. Use when asked to review Unity Catalog access/ownership posture, audit catalog or schema grants, or check who has broad access.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Is anyone's Unity Catalog access broader than it should be?" today means manually clicking through each catalog/schema's Permissions tab, or hand-writing `SHOW GRANTS` per securable. This skill runs the equivalent checks across every (or a chosen set of) catalogs and schemas in one command and reports concrete findings with evidence -- not a generic "review your access controls" nudge. Same honesty model as `snowflake-role-audit`, Databricks-native.

**Scope note, same honesty framing as `snowflake-role-audit` and `databricks-cluster-audit`:** these are configuration observations, not confirmed vulnerabilities. An individual owning a catalog might be entirely correct for a small team's sandbox. Every finding says "confirm with whoever owns access policy," never "this is wrong."

**Privacy note:** unlike this repo's other Databricks skills (which deliberately scope query-history reads to the caller's own identity to avoid touching other users' business data), an access/ownership audit's whole purpose is reporting real principal identifiers -- who owns what, who holds what grant. That is expected and necessary here, the same way `snowflake-role-audit` reports real usernames. This skill never reads query text, table contents, or anything beyond grant/ownership metadata, and never modifies a grant, revokes access, or changes ownership -- strictly read-only.

## When to use

Use this skill when asked: to review Unity Catalog access/ownership posture, audit catalog or schema grants, check who has broad or unusual access, or find catalogs/schemas with concentrated single-user ownership.

## Steps

1. Confirm which Databricks workspace profile to use — this skill never asks for or handles credentials directly; it uses the runtime's own workspace client authentication.
2. Run `{{SKILL_DIR}}/scripts/uc_audit.py` (accepts `--profile`, `--catalog NAME` [repeatable, default: every non-system catalog], `--baseline-privileges` [default `BROWSE,USE_CATALOG,USE_SCHEMA`], `--broad-group-names` [default `account users,users`], `--broad-privilege-threshold` [default 5], `--no-schemas` to skip per-schema checks and audit catalog-level only).
3. The script:
   - Lists every catalog in scope (or the ones named via `--catalog`), skipping system catalogs
   - For each catalog (and, unless `--no-schemas`, each of its schemas): pulls real privilege assignments via the Grants API and real ownership via the catalog/schema metadata
   - **`broad_group_holds_direct_object_grants`** — a built-in "everyone" group (configurable via `--broad-group-names`) holds any privilege beyond the baseline browse/use set directly on a catalog or schema — the Databricks equivalent of Snowflake's PUBLIC-holds-grants check
   - **`securable_owned_by_individual_user`** — a catalog or schema is owned directly by an individual user account (heuristic: an email-shaped principal) rather than a group or service principal — a single person leaving orphans ownership
   - **`individual_user_holds_broad_direct_privileges`** — an individual user holds `ALL_PRIVILEGES`, or at least `--broad-privilege-threshold` distinct privileges, directly on a securable rather than through a group — harder to review or revoke as a set
4. Turn the JSON into a Markdown report (see Output format). Always state which checks fired and the concrete evidence — never a bare "access looks fine" without the counts.

## Helper scripts

- `{{SKILL_DIR}}/scripts/uc_audit.py` — pure `databricks-sdk` calls (`catalogs.list/get`, `schemas.list`, `grants.get`); no dependency beyond the SDK.

## Output format

Render as Markdown:

1. **Summary line** — bolded: how many catalogs/schemas were audited and how many findings surfaced.
2. **Findings by check (table)** — check name, count.
3. **Findings (table or list)** — securable, principal (real identifier — this is the point of an access audit), and the concrete detail for each.
4. **Bottom line** — one bolded sentence: "review needed" with the top concern, or explicitly "no action — access is scoped through groups/service principals as expected" if clean.

Always cite the actual securable names, principals, and privilege lists behind every finding — never a vague "some access looks broad" without specifics.

## Loop tier

**On-demand (Tier 1).** A point-in-time access review, same as `snowflake-role-audit` — no obvious scheduled variant without real notification infrastructure in place first.

## Verified live (not just fixture-tested)

Run against the real Unity Catalog metastore (2026-07-23), scoped to 3 real catalogs and 75 real schemas:

- **`broad_group_holds_direct_object_grants`** fired twice for real: the built-in `account users` group holds `MODIFY`/`SELECT`/`CREATE_TABLE`/`CREATE_SCHEMA`/etc. directly on one catalog beyond baseline browse/use access, and `SELECT`/`EXECUTE`/`READ_VOLUME` directly on another.
- **`securable_owned_by_individual_user`** fired 63 times across the audited catalogs/schemas — real, individual-user-owned securables rather than group/service-principal ownership. (Real principal identifiers are reported by the script's own output for the person running it, but are not reproduced in this document or in `test-fixtures/sample-output.json`, which uses synthetic placeholder names — this workspace is shared with other real teams, and this repo's own privacy discipline extends to not publishing other people's identities into a committed file, even when the underlying check is legitimate and necessary.)
- **`individual_user_holds_broad_direct_privileges`** did **not** fire live, even retested at a lowered threshold (2) — every individual-level grant observed in this workspace was narrow; broad access is consistently routed through groups here. Covered instead by `test-fixtures/test_uc_audit.py`'s synthetic-data unit tests (the `ALL_PRIVILEGES` case, the threshold-crossing case, and the below-threshold clean case).

## Verification status per branch (honest status, not hidden)

| Path | Live | Unit-tested |
|---|---|---|
| `broad_group_holds_direct_object_grants` | ✅ (2 real findings across 2 real catalogs) | ✅ |
| `securable_owned_by_individual_user` | ✅ (63 real findings across 3 catalogs/75 schemas) | ✅ |
| `individual_user_holds_broad_direct_privileges` | ❌ not yet observed live — this workspace routes broad access through groups, not direct individual grants | ✅ (`ALL_PRIVILEGES`, threshold-crossing, and below-threshold cases) |
| Clean/zero-findings result | — not yet observed live (every real catalog audited so far has at least one finding) | — (trivial to construct, not separately unit-tested) |

**What's still open:** `individual_user_holds_broad_direct_privileges` genuinely hasn't fired in this account — that's a real, positive signal about this workspace's access hygiene, not a gap in the check itself.
