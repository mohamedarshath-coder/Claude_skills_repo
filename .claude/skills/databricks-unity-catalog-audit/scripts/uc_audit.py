#!/usr/bin/env python3
"""
databricks-unity-catalog-audit helper script.

Flags concrete, evidence-backed access-control observations in a Unity
Catalog metastore -- the same "real findings, not a generic security
nudge" model as snowflake-role-audit, adapted for Databricks' actual
grant/ownership model: a broad built-in group (e.g. "account users")
holding direct object-level privileges beyond baseline browse/use
access, an individual user account (not a group or service principal)
owning a catalog/schema, and an individual user account holding a broad
set of high-impact privileges directly rather than through a group.

Auth: uses databricks-sdk's WorkspaceClient, which reads DATABRICKS_HOST /
DATABRICKS_TOKEN env vars (or a named profile in ~/.databrickscfg).
Never touches credentials directly.

PRIVACY: this skill's whole purpose is an access/ownership audit, which
inherently means reporting real principal identifiers (who owns what,
who holds what grant) -- unlike this repo's other Databricks skills,
which deliberately avoid reading other users' data. That is expected
and necessary here, exactly as snowflake-role-audit reports real
usernames for its own account/security findings. The caller of this
script is expected to be someone with legitimate admin visibility into
these grants already (SHOW GRANTS-equivalent access), not a bystander.

Scope note, same honesty framing as databricks-cluster-audit and
snowflake-role-audit: these are configuration observations, not
confirmed vulnerabilities. A user directly owning a catalog might be
entirely correct for a small team. Every finding says "confirm with
whoever owns access policy," never "this is wrong."

Read-only: this skill only ever reads grants/ownership metadata. It
never modifies a grant, revokes access, or changes ownership.

Usage:
    python uc_audit.py [--profile NAME] [--catalog NAME ...]
        [--baseline-privileges PRIV,PRIV,...]
        [--broad-group-names NAME,NAME,...]
        [--broad-privilege-threshold N]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import json
import os
import sys

from databricks.sdk import WorkspaceClient

# Privileges that a broad, all-users-style group holding them directly
# is unremarkable (basic discoverability), vs. anything beyond this set
# being a real, reportable observation.
DEFAULT_BASELINE_PRIVILEGES = {"BROWSE", "USE_CATALOG", "USE_SCHEMA"}

# Built-in/broad group names this account/workspace treats as "everyone"
# -- the Databricks equivalent of Snowflake's PUBLIC role.
DEFAULT_BROAD_GROUP_NAMES = {"account users", "users"}

# A privilege set this large, held directly by one individual user
# (rather than through a group), is worth a second look regardless of
# which specific privileges make it up.
DEFAULT_BROAD_PRIVILEGE_THRESHOLD = 5

SYSTEM_CATALOG_TYPES = {"CatalogType.SYSTEM_CATALOG"}


def get_client(profile):
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def is_individual_user(principal):
    """Heuristic: an individual human user account looks like an email
    address. Groups and service principals in this workspace don't."""
    return "@" in principal


def list_audit_catalogs(client, explicit_catalogs):
    if explicit_catalogs:
        return explicit_catalogs
    return [c.name for c in client.catalogs.list() if str(c.catalog_type) not in SYSTEM_CATALOG_TYPES]


def get_privilege_assignments(client, securable_type, full_name):
    resp = client.grants.get(securable_type=securable_type, full_name=full_name)
    return resp.privilege_assignments or []


def privilege_names(assignment):
    return {str(p).split(".")[-1] for p in (assignment.privileges or [])}


def check_broad_group_direct_grants(assignments, full_name, securable_type, baseline_privileges, broad_group_names):
    findings = []
    for a in assignments:
        if a.principal.lower() not in broad_group_names:
            continue
        privs = privilege_names(a)
        beyond_baseline = privs - baseline_privileges
        if beyond_baseline:
            findings.append({
                "check": "broad_group_holds_direct_object_grants",
                "securable_type": securable_type,
                "securable_name": full_name,
                "principal": a.principal,
                "privileges_beyond_baseline": sorted(beyond_baseline),
                "detail": f"The broad group '{a.principal}' holds {sorted(beyond_baseline)} directly on {securable_type.lower()} '{full_name}', "
                          f"beyond baseline browse/use access ({sorted(baseline_privileges)}).",
            })
    return findings


def check_individual_ownership(owner, full_name, securable_type):
    if owner and is_individual_user(owner):
        return [{
            "check": "securable_owned_by_individual_user",
            "securable_type": securable_type,
            "securable_name": full_name,
            "principal": owner,
            "detail": f"{securable_type.title()} '{full_name}' is owned directly by an individual user account rather than a group or service principal -- "
                      f"a single person's account leaving would orphan ownership.",
        }]
    return []


def check_individual_broad_privileges(assignments, full_name, securable_type, threshold):
    findings = []
    for a in assignments:
        if not is_individual_user(a.principal):
            continue
        privs = privilege_names(a)
        if "ALL_PRIVILEGES" in privs or len(privs) >= threshold:
            findings.append({
                "check": "individual_user_holds_broad_direct_privileges",
                "securable_type": securable_type,
                "securable_name": full_name,
                "principal": a.principal,
                "privilege_count": len(privs),
                "privileges": sorted(privs),
                "detail": f"An individual user account holds {len(privs)} privileges directly on {securable_type.lower()} '{full_name}' "
                          f"({sorted(privs)}) rather than through a group -- harder to review/revoke as a set.",
            })
    return findings


def audit_catalog(client, catalog_name, baseline_privileges, broad_group_names, broad_privilege_threshold, include_schemas):
    findings = []
    cat = client.catalogs.get(catalog_name)
    cat_assignments = get_privilege_assignments(client, "CATALOG", catalog_name)

    findings += check_broad_group_direct_grants(cat_assignments, catalog_name, "CATALOG", baseline_privileges, broad_group_names)
    findings += check_individual_ownership(cat.owner, catalog_name, "CATALOG")
    findings += check_individual_broad_privileges(cat_assignments, catalog_name, "CATALOG", broad_privilege_threshold)

    schemas_audited = 0
    if include_schemas:
        for schema in client.schemas.list(catalog_name=catalog_name):
            if schema.name == "information_schema":
                continue
            full_name = f"{catalog_name}.{schema.name}"
            schema_assignments = get_privilege_assignments(client, "SCHEMA", full_name)
            findings += check_broad_group_direct_grants(schema_assignments, full_name, "SCHEMA", baseline_privileges, broad_group_names)
            findings += check_individual_ownership(schema.owner, full_name, "SCHEMA")
            findings += check_individual_broad_privileges(schema_assignments, full_name, "SCHEMA", broad_privilege_threshold)
            schemas_audited += 1

    return findings, schemas_audited


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--catalog", action="append", default=None,
                        help="Restrict the audit to specific catalog(s). Repeatable. Default: every non-system catalog.")
    parser.add_argument("--baseline-privileges", default=",".join(sorted(DEFAULT_BASELINE_PRIVILEGES)))
    parser.add_argument("--broad-group-names", default=",".join(sorted(DEFAULT_BROAD_GROUP_NAMES)))
    parser.add_argument("--broad-privilege-threshold", type=int, default=DEFAULT_BROAD_PRIVILEGE_THRESHOLD)
    parser.add_argument("--no-schemas", action="store_true", help="Audit catalog-level grants/ownership only, skip per-schema checks.")
    args = parser.parse_args()

    baseline_privileges = {p.strip().upper() for p in args.baseline_privileges.split(",") if p.strip()}
    broad_group_names = {g.strip().lower() for g in args.broad_group_names.split(",") if g.strip()}

    try:
        client = get_client(args.profile)
    except Exception as e:
        print(json.dumps({"error": f"could not create Databricks client: {e}"}), file=sys.stderr)
        sys.exit(1)

    try:
        catalogs = list_audit_catalogs(client, args.catalog)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    all_findings = []
    catalogs_audited = []
    schemas_audited_total = 0
    errors = {}

    for catalog_name in catalogs:
        try:
            findings, schemas_audited = audit_catalog(
                client, catalog_name, baseline_privileges, broad_group_names,
                args.broad_privilege_threshold, include_schemas=not args.no_schemas,
            )
            all_findings.extend(findings)
            catalogs_audited.append(catalog_name)
            schemas_audited_total += schemas_audited
        except Exception as e:
            errors[catalog_name] = str(e)[:300]

    by_check = {}
    for f in all_findings:
        by_check.setdefault(f["check"], 0)
        by_check[f["check"]] += 1

    output = {
        "catalogs_audited": catalogs_audited,
        "schemas_audited": schemas_audited_total,
        "baseline_privileges": sorted(baseline_privileges),
        "broad_group_names": sorted(broad_group_names),
        "findings_count": len(all_findings),
        "findings_by_check": by_check,
        "findings": all_findings,
        "errors": errors,
        "overall_status": "findings" if all_findings else "clean",
        "detail": (
            f"{len(all_findings)} finding(s) across {len(catalogs_audited)} catalog(s)/{schemas_audited_total} schema(s) -- "
            f"see findings_by_check for a breakdown."
        ) if all_findings else (
            f"No access-control observations across {len(catalogs_audited)} catalog(s)/{schemas_audited_total} schema(s) -- "
            f"no broad-group direct grants beyond baseline, no individual-owned securables, no individual holding broad direct privileges."
        ),
    }

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
