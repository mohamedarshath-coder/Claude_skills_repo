#!/usr/bin/env python3
"""
snowflake-role-audit helper script.

Flags concrete, evidence-backed access-control observations in a Snowflake
account -- privileged system roles granted directly to a user, a user's
default role itself being privileged, PUBLIC holding direct object grants,
and users with active access who haven't logged in recently. Never asserts
a finding is definitely wrong -- these are configuration observations that
may reflect a deliberate choice (e.g. a small team where everyone needs
ACCOUNTADMIN), same honesty framing as this repo's other audit skills.

Uses snowflake-connector-python with a locally-configured named connection
(~/.snowflake/connections.toml, or SNOWFLAKE_* env vars as a fallback).
Never touches or stores credentials directly.

Usage:
    python role_audit.py [--connection NAME] [--privileged-roles ROLE ...] [--stale-login-days N]
"""
import argparse
import json
import os
import sys

import snowflake.connector


PRIVILEGED_ROLES_DEFAULT = ["ACCOUNTADMIN", "SECURITYADMIN", "ORGADMIN"]


def get_connection(connection_name):
    """Connect using a named profile from ~/.snowflake/connections.toml
    (or SNOWFLAKE_* env vars) -- credentials are never hardcoded here.
    An env var password overrides the profile's stored password, matching
    every other Snowflake skill in this repo. The password itself is never
    written to connections.toml or committed anywhere."""
    overrides = {}
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        overrides["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    try:
        return snowflake.connector.connect(connection_name=connection_name, **overrides)
    except TypeError:
        # Older connector versions without connection_name support.
        return snowflake.connector.connect(
            connections_file_path=os.path.expanduser("~/.snowflake/connections.toml"),
            connection_name=connection_name,
            **overrides,
        )


def check_privileged_role_grants(cur, privileged_roles):
    """Any user directly holding a privileged system role is a finding --
    not necessarily wrong, but worth confirming with whoever owns access
    policy, since best practice is to grant these sparingly."""
    placeholders = ", ".join("%s" for _ in privileged_roles)
    cur.execute(
        f"""
        SELECT grantee_name, role, granted_by, created_on
        FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS
        WHERE deleted_on IS NULL AND role IN ({placeholders})
        ORDER BY grantee_name, role
        """,
        privileged_roles,
    )
    findings = []
    for grantee_name, role, granted_by, created_on in cur.fetchall():
        findings.append({
            "issue": "privileged_role_granted_to_user",
            "severity": "high",
            "target": grantee_name,
            "evidence": f"role '{role}' directly granted (granted_by={granted_by or 'SYSTEM'}, on {created_on})",
            "recommendation": "Confirm with whoever owns access policy whether this user needs standing access to this role, or whether it should be granted only when needed (e.g. via a lower-privileged default role with explicit role-switching).",
        })
    return findings


def check_privileged_default_role(cur, privileged_roles):
    """A user's default role being privileged means every session starts
    with full privileges active, even when the user doesn't need them for
    that session -- a distinct risk from merely holding the role."""
    placeholders = ", ".join("%s" for _ in privileged_roles)
    cur.execute(
        f"""
        SELECT name, default_role, last_success_login
        FROM SNOWFLAKE.ACCOUNT_USAGE.USERS
        WHERE deleted_on IS NULL AND disabled = FALSE AND default_role IN ({placeholders})
        """,
        privileged_roles,
    )
    findings = []
    for name, default_role, last_login in cur.fetchall():
        findings.append({
            "issue": "default_role_is_privileged",
            "severity": "high",
            "target": name,
            "evidence": f"default_role = '{default_role}' (last login: {last_login or 'never'})",
            "recommendation": "Every session for this user starts with this privileged role already active. Consider setting a lower-privileged default role and requiring an explicit USE ROLE to elevate, unless this is a deliberate choice for a small/trusted team.",
        })
    return findings


def check_public_role_grants(cur):
    """PUBLIC is granted to every user automatically -- any direct object
    grant to it applies account-wide. Reported as a configuration
    observation (some of this may be default Snowflake behavior on a new
    database), never asserted as wrong outright."""
    cur.execute(
        """
        SELECT privilege, granted_on, table_catalog, table_schema
        FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_ROLES
        WHERE name = 'PUBLIC' AND deleted_on IS NULL
        """
    )
    rows = cur.fetchall()
    findings = []
    ownership_rows = [r for r in rows if r[0] == "OWNERSHIP"]
    other_rows = [r for r in rows if r[0] != "OWNERSHIP"]

    for privilege, granted_on, catalog, schema in ownership_rows:
        target = f"{catalog}.{schema}" if schema else catalog
        findings.append({
            "issue": "public_role_has_ownership_grant",
            "severity": "medium",
            "target": target,
            "evidence": f"PUBLIC role holds OWNERSHIP on {granted_on} '{target}'",
            "recommendation": "OWNERSHIP granted to PUBLIC means every user in the account can manage this object. Confirm with the account owner whether this is intentional (e.g. a shared sandbox schema) or a default that should be reassigned to a specific role.",
        })

    if other_rows:
        privilege_counts = {}
        catalogs = set()
        for privilege, granted_on, catalog, schema in other_rows:
            privilege_counts[privilege] = privilege_counts.get(privilege, 0) + 1
            if catalog:
                catalogs.add(catalog)
        findings.append({
            "issue": "public_role_has_direct_grants",
            "severity": "low",
            "target": ", ".join(sorted(catalogs)) or "account-level",
            "evidence": f"{len(other_rows)} non-ownership privileges granted directly to PUBLIC (e.g. {', '.join(sorted(privilege_counts)[:5])})",
            "recommendation": "These grants apply to every user in the account via the PUBLIC role. Review whether this is the intended scope, or whether it should be a named role instead.",
        })
    return findings


def check_stale_users(cur, stale_days):
    """A user with active (non-disabled) access who hasn't logged in
    recently still holds whatever roles were granted to them -- worth a
    look, though absence of login doesn't by itself mean access should be
    revoked (could be a service/automation account using key-pair auth)."""
    cur.execute(
        """
        SELECT name, last_success_login,
               DATEDIFF('day', last_success_login, CURRENT_TIMESTAMP()) AS days_since_login
        FROM SNOWFLAKE.ACCOUNT_USAGE.USERS
        WHERE deleted_on IS NULL AND disabled = FALSE
          AND (last_success_login IS NULL OR DATEDIFF('day', last_success_login, CURRENT_TIMESTAMP()) > %s)
        """,
        (stale_days,),
    )
    findings = []
    for name, last_login, days_since in cur.fetchall():
        evidence = "never logged in" if last_login is None else f"last login {days_since} days ago ({last_login})"
        findings.append({
            "issue": "stale_user_active_access",
            "severity": "medium",
            "target": name,
            "evidence": evidence,
            "recommendation": f"No successful login in over {stale_days} days, but the account is not disabled. Confirm whether this is a human user who's left, or a service/automation account authenticating via key-pair (which wouldn't necessarily show a password login here) before disabling.",
        })
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--privileged-roles", nargs="+", default=PRIVILEGED_ROLES_DEFAULT)
    parser.add_argument("--stale-login-days", type=int, default=30)
    args = parser.parse_args()

    try:
        conn = get_connection(args.connection)
    except Exception as e:
        print(json.dumps({"error": f"connection failed: {e}"}), file=sys.stderr)
        sys.exit(1)

    cur = conn.cursor()
    findings = []
    errors = {}

    try:
        findings.extend(check_privileged_role_grants(cur, args.privileged_roles))
    except Exception as e:
        errors["privileged_role_grants"] = str(e)

    try:
        findings.extend(check_privileged_default_role(cur, args.privileged_roles))
    except Exception as e:
        errors["privileged_default_role"] = str(e)

    try:
        findings.extend(check_public_role_grants(cur))
    except Exception as e:
        errors["public_role_grants"] = str(e)

    try:
        findings.extend(check_stale_users(cur, args.stale_login_days))
    except Exception as e:
        errors["stale_users"] = str(e)

    output = {
        "connection": args.connection,
        "privileged_roles_checked": args.privileged_roles,
        "stale_login_threshold_days": args.stale_login_days,
        "finding_count": len(findings),
        "findings": findings,
    }
    if errors:
        output["errors"] = errors

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
