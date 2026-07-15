#!/usr/bin/env python3
"""
Unit tests for role_audit.py's check_* functions against a stub cursor --
covers boundary cases the live account doesn't naturally exercise: a
genuinely stale user, PUBLIC with zero grants (clean case), and PUBLIC
with only non-ownership grants (no ownership row at all).

Run: python test_role_audit.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from role_audit import (  # noqa: E402
    check_privileged_role_grants,
    check_privileged_default_role,
    check_public_role_grants,
    check_stale_users,
)


class StubCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # privileged_role_granted_to_user: one real hit
    cur = StubCursor([("JANE", "ACCOUNTADMIN", None, "2026-01-01")])
    findings = check_privileged_role_grants(cur, ["ACCOUNTADMIN", "SECURITYADMIN"])
    check("privileged role grant detected", len(findings) == 1 and findings[0]["issue"] == "privileged_role_granted_to_user")
    check("privileged role grant has evidence+recommendation", findings[0]["evidence"] and findings[0]["recommendation"])

    # privileged_role_granted_to_user: zero hits -> no false positive
    cur = StubCursor([])
    findings = check_privileged_role_grants(cur, ["ACCOUNTADMIN"])
    check("no privileged role grants -> empty list", findings == [])

    # default_role_is_privileged
    cur = StubCursor([("JANE", "ACCOUNTADMIN", "2026-07-01")])
    findings = check_privileged_default_role(cur, ["ACCOUNTADMIN"])
    check("privileged default role detected", len(findings) == 1 and findings[0]["issue"] == "default_role_is_privileged")

    # public_role_grants: clean case, zero grants at all
    cur = StubCursor([])
    findings = check_public_role_grants(cur)
    check("no PUBLIC grants -> empty list (clean)", findings == [])

    # public_role_grants: only non-ownership grants, no ownership row emitted
    cur = StubCursor([
        ("CREATE VIEW", "SCHEMA", "DB1", "PUBLIC"),
        ("CREATE STAGE", "SCHEMA", "DB1", "PUBLIC"),
    ])
    findings = check_public_role_grants(cur)
    check("non-ownership-only grants produce exactly one aggregated finding", len(findings) == 1)
    check("non-ownership finding is 'public_role_has_direct_grants', not ownership", findings[0]["issue"] == "public_role_has_direct_grants")

    # public_role_grants: ownership row present -> its own finding, separate from the aggregate
    cur = StubCursor([
        ("OWNERSHIP", "SCHEMA", "DB1", "PUBLIC"),
        ("CREATE VIEW", "SCHEMA", "DB1", "PUBLIC"),
    ])
    findings = check_public_role_grants(cur)
    check("ownership + non-ownership -> two distinct findings", len(findings) == 2)
    check("ownership finding reported separately from aggregate", any(f["issue"] == "public_role_has_ownership_grant" for f in findings))

    # stale_users: a genuinely stale user (never live-fired in this account)
    cur = StubCursor([("OLDUSER", None, None)])
    findings = check_stale_users(cur, 30)
    check("never-logged-in user flagged", len(findings) == 1 and "never logged in" in findings[0]["evidence"])

    cur = StubCursor([("OLDUSER2", "2026-01-01", 120)])
    findings = check_stale_users(cur, 30)
    check("stale user with a past login date flagged with day count", "120 days ago" in findings[0]["evidence"])

    # stale_users: clean case, nobody stale
    cur = StubCursor([])
    findings = check_stale_users(cur, 30)
    check("no stale users -> empty list", findings == [])

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
