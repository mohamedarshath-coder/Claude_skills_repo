#!/usr/bin/env python3
"""
Unit tests for uc_audit's pure-logic functions -- is_individual_user,
privilege_names, and every check_* finding function. All principal
names here are synthetic placeholders, never real workspace identities
(this skill's live verification deliberately reports only aggregate
counts/categories for that reason -- see SKILL.md).

The real SDK calls (catalogs.list/get, grants.get, schemas.list) are
proven live against a real Unity Catalog metastore instead.

Run: python test_uc_audit.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from uc_audit import (  # noqa: E402
    is_individual_user,
    privilege_names,
    check_broad_group_direct_grants,
    check_individual_ownership,
    check_individual_broad_privileges,
)

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- is_individual_user ---
check("an email-shaped principal is an individual user", is_individual_user("test.user@example.com") is True)
check("a group name is not an individual user", is_individual_user("account users") is False)
check("a service principal id is not an individual user", is_individual_user("svc-principal-123") is False)


# --- privilege_names ---
def priv(name):
    return SimpleNamespace(__str__=lambda self: f"Privilege.{name}")


class FakePriv:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"Privilege.{self.name}"


def make_assignment(principal, priv_names):
    return SimpleNamespace(principal=principal, privileges=[FakePriv(p) for p in priv_names])


assignment = make_assignment("account users", ["BROWSE", "USE_CATALOG", "MODIFY"])
check("privilege_names strips the enum prefix", privilege_names(assignment) == {"BROWSE", "USE_CATALOG", "MODIFY"})


# --- check_broad_group_direct_grants ---
baseline = {"BROWSE", "USE_CATALOG", "USE_SCHEMA"}
broad_groups = {"account users", "users"}

assignments_beyond = [make_assignment("account users", ["BROWSE", "USE_CATALOG", "MODIFY", "SELECT"])]
findings = check_broad_group_direct_grants(assignments_beyond, "test_catalog", "CATALOG", baseline, broad_groups)
check("a broad group holding MODIFY/SELECT beyond baseline is flagged", len(findings) == 1)
check("the finding names the correct privileges beyond baseline", set(findings[0]["privileges_beyond_baseline"]) == {"MODIFY", "SELECT"})

assignments_baseline_only = [make_assignment("account users", ["BROWSE", "USE_CATALOG"])]
check(
    "a broad group holding ONLY baseline privileges is not flagged",
    check_broad_group_direct_grants(assignments_baseline_only, "test_catalog", "CATALOG", baseline, broad_groups) == [],
)

assignments_non_broad_group = [make_assignment("data-engineers", ["MODIFY", "SELECT", "CREATE_TABLE"])]
check(
    "a non-broad (real team) group holding lots of privileges is NOT flagged by this check -- it's not 'everyone'",
    check_broad_group_direct_grants(assignments_non_broad_group, "test_catalog", "CATALOG", baseline, broad_groups) == [],
)


# --- check_individual_ownership ---
check(
    "an individual-user-owned catalog is flagged",
    len(check_individual_ownership("owner.person@example.com", "test_catalog", "CATALOG")) == 1,
)
check(
    "a group-owned catalog is NOT flagged",
    check_individual_ownership("data-platform-admins", "test_catalog", "CATALOG") == [],
)
check("a None owner does not crash and is not flagged", check_individual_ownership(None, "test_catalog", "CATALOG") == [])


# --- check_individual_broad_privileges ---
individual_broad = [make_assignment("person@example.com", ["MODIFY", "SELECT", "CREATE_TABLE", "USE_CATALOG", "USE_SCHEMA", "EXECUTE"])]
findings = check_individual_broad_privileges(individual_broad, "test_catalog", "CATALOG", threshold=5)
check("an individual holding 6 privileges (>= threshold 5) is flagged", len(findings) == 1)
check("the finding reports the correct privilege_count", findings[0]["privilege_count"] == 6)

individual_narrow = [make_assignment("person@example.com", ["USE_CATALOG", "USE_SCHEMA"])]
check(
    "an individual holding only 2 privileges (below threshold) is NOT flagged",
    check_individual_broad_privileges(individual_narrow, "test_catalog", "CATALOG", threshold=5) == [],
)

individual_all_privileges = [make_assignment("person@example.com", ["ALL_PRIVILEGES"])]
check(
    "ALL_PRIVILEGES always flags regardless of the numeric threshold",
    len(check_individual_broad_privileges(individual_all_privileges, "test_catalog", "CATALOG", threshold=5)) == 1,
)

group_broad = [make_assignment("data-engineers", ["MODIFY", "SELECT", "CREATE_TABLE", "USE_CATALOG", "USE_SCHEMA", "EXECUTE"])]
check(
    "a group (not an individual) holding many privileges is NOT flagged by this check",
    check_individual_broad_privileges(group_broad, "test_catalog", "CATALOG", threshold=5) == [],
)

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All uc_audit unit tests passed.")
sys.exit(0)
