---
name: snowflake-role-audit
description: Flags concrete, evidence-backed access-control observations in a Snowflake account -- privileged system roles granted directly to a user, a user's default role itself being privileged, PUBLIC holding direct object grants, and users with active access who haven't logged in recently. Use when asked to review Snowflake access/security posture, audit roles and grants, or check who has admin-level access.
risk: read-only
loop-tier: on-demand
---

## Purpose

"Who has admin access, and is any of it more than they need?" today means manually clicking through Snowsight's Users/Roles pages, or hand-writing `SHOW GRANTS` queries per user. This skill runs the equivalent checks in one command against `ACCOUNT_USAGE`'s own grant/user views and reports concrete findings with evidence -- not a generic "review your access controls" nudge. This is the first skill in the catalog covering **access/security**, distinct from every other skill's cost/quality/performance focus.

**Scope note, same honesty framing as `databricks-cluster-audit`:** these are configuration observations, not confirmed vulnerabilities. A user directly holding `ACCOUNTADMIN` might be entirely correct for a small team where everyone needs it. Every finding says "confirm with whoever owns access policy," never "this is wrong."

## When to use

Use this skill when asked to: audit Snowflake roles and grants, check who has admin-level access, review access-control/security posture, or find stale users with active access.

## Steps

1. Confirm which Snowflake connection profile to use (default: `default`, via `~/.snowflake/connections.toml`) — this skill never asks for or handles credentials directly.
2. Run `python {{SKILL_DIR}}/scripts/role_audit.py [--connection NAME] [--privileged-roles ROLE ...] [--stale-login-days N]`.
   - `--privileged-roles` (default: `ACCOUNTADMIN SECURITYADMIN ORGADMIN`) — which system roles count as "privileged" for the first two checks. Overridable, since a caller may want to also treat a custom high-privilege role as privileged.
   - `--stale-login-days` (default: `30`) — how long without a successful login counts as stale for an active (non-disabled) user.
3. Four checks, all evidence-based:
   - **Privileged role granted directly to a user** (high severity): queries `ACCOUNT_USAGE.GRANTS_TO_USERS` for any of the privileged roles held by name, not through a custom role.
   - **User's default role is privileged** (high severity): a distinct risk from merely holding the role -- every session for that user starts with full privileges already active.
   - **PUBLIC role holding direct grants** (medium for OWNERSHIP, low for everything else): `PUBLIC` is automatically granted to every user, so any direct object grant to it applies account-wide. OWNERSHIP grants are reported individually (highest impact); everything else is aggregated into one finding with a privilege-type sample, to avoid flooding the report with dozens of near-identical rows.
   - **Stale user with active access** (medium severity): a non-disabled user who hasn't logged in within `--stale-login-days`, or has never logged in at all.
4. Render as Markdown (see Output format). If a check finds nothing, say so plainly -- a clean result on any individual check is valid and useful.
5. Never present this as a complete security audit -- it covers role/grant/login-recency observations only. It does not check MFA enrollment, network policies, password rotation, or object-level grants beyond PUBLIC.

## Helper scripts

- `{{SKILL_DIR}}/scripts/role_audit.py` — queries `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS`, `.USERS`, and `.GRANTS_TO_ROLES` directly; no row-level customer data involved, only account metadata (role names, usernames, grant timestamps). Uses `snowflake-connector-python` with a named connection profile; never handles credentials directly.

## Output format

Render as Markdown:

1. **Summary line** — bolded: total findings, one-phrase breakdown by check (e.g. "2 privileged role grants, 1 privileged default role, 2 PUBLIC ownership grants, 1 PUBLIC direct-grant aggregate, 0 stale users").
2. **Findings (table)** — only if `finding_count > 0`:

   | Issue | Severity | Target | Evidence |
   |---|---|---|---|
   | Privileged role granted to user | high | `JANE` | role 'ACCOUNTADMIN' directly granted |

3. **Bottom line** — one bolded sentence: the single highest-severity finding to confirm first, or "No access-control findings this run."

Always cite the actual role names, usernames, and timestamps behind every finding — never a vague "some users have too much access" without the evidence.

## Verified live (not just fixture-tested)

Run against the real account before this SKILL.md was even finalized -- the first live run surfaced three genuine, non-synthetic findings in a single pass:

1. **`privileged_role_granted_to_user`** fired twice: the account's one real user (`ARSHATH`) directly holds both `ACCOUNTADMIN` and `ORGADMIN`.
2. **`default_role_is_privileged`** fired: that same user's `default_role` is `ACCOUNTADMIN` -- meaning every session starts fully privileged, a distinct and arguably higher-impact finding than #1 alone.
3. **`public_role_has_ownership_grant`** fired twice (on `USER$ARSHATH.PUBLIC` and `INSIGHTOPS_DB.PUBLIC`), and **`public_role_has_direct_grants`** fired once, aggregating 73 non-ownership privileges (e.g. `CREATE VIEW`, `CREATE STAGE`) granted directly to `PUBLIC`.

`stale_user_active_access` did **not** fire live -- the account's one real user logged in the same day this was tested. Covered instead by `test-fixtures/test_role_audit.py`'s stub-cursor unit tests (never-logged-in case, stale-by-N-days case, and the clean/zero-findings case).

## Verification status (honest, per check)

| Check | Live | Unit-tested |
|---|---|---|
| `privileged_role_granted_to_user`: real hit | ✅ (2 real grants) | ✅ (hit + zero-hit clean case) |
| `default_role_is_privileged`: real hit | ✅ | ✅ |
| `public_role_has_ownership_grant` | ✅ (2 real schemas) | ✅ (incl. mixed ownership + non-ownership in one result set) |
| `public_role_has_direct_grants` (non-ownership aggregate) | ✅ (73 real grants) | ✅ (incl. clean/zero-grant case) |
| `stale_user_active_access`: a genuinely stale/never-logged-in user | ❌ this account's one real user is active daily; no such case exists yet | ✅ (never-logged-in and stale-by-N-days cases) |
| Multi-user account (more than one real user triggering findings independently) | ❌ this Snowflake account has exactly one real user, unlike the Databricks workspace tested elsewhere in this repo | — |
| `--privileged-roles` override (a custom role name beyond the default 3) | ✅ (2026-07-15) — `--privileged-roles ACCOUNTADMIN SECURITYADMIN ORGADMIN TRANSFORMER` correctly added a real hit for the account's actual custom role `TRANSFORMER` (granted by `ACCOUNTADMIN`), raising `finding_count` from 6 to 7. Confirms the override isn't just structurally wired but genuinely changes which grants get flagged against real data | ✅ |

## Loop tier & future promotion

**On-demand (Tier 1).** A plausible Tier 3 candidate later (a monthly access-review digest, notify-only on a new privileged grant or a newly-stale user since the last run), but that requires the same explicit promotion PR as `snowflake-cost-audit`'s promotion -- schedule, notification channel, budget, kill switch, run logging. Not done here.
