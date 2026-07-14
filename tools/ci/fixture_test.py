#!/usr/bin/env python3
"""
fixture-test CI job.

Replays each skill's test-fixtures/*.json and asserts on STRUCTURE, not
exact wording -- per the repo's own rule (a fixture check on literal
text would fail on harmless phrasing changes and the team would start
ignoring it). Concretely, for every fixture file:
  1. It must be valid JSON.
  2. Every object that looks like a finding (has an "issue" or "recommendation"
     key) must carry both "evidence" and "recommendation" -- a finding
     without cited evidence is exactly the "vague claim" failure mode
     every SKILL.md in this repo explicitly forbids.
  3. Every finding must carry a "severity" field.

This intentionally does NOT check for exact field names beyond that core
shape, since different skills have legitimately different schemas
(cost audit vs. job triage vs. cluster audit).

Usage: python tools/ci/fixture_test.py
Exit code 0 = all fixtures pass, 1 = at least one failure (fails the PR).
"""
import glob
import json
import sys


def find_findings(obj, path="root"):
    """Recursively walk a JSON structure, yielding (path, dict) for every
    dict that looks like a 'finding' -- has an 'issue' or 'recommendation' key."""
    if isinstance(obj, dict):
        if "issue" in obj or "recommendation" in obj:
            yield path, obj
        for k, v in obj.items():
            yield from find_findings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from find_findings(item, f"{path}[{i}]")


def check_fixture(path):
    errors = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]

    findings = list(find_findings(data))
    if not findings:
        # A fixture with zero findings anywhere (an all-clean example) is
        # valid -- not every fixture needs to exercise the finding schema.
        return errors

    for finding_path, finding in findings:
        missing = [k for k in ("evidence", "recommendation", "severity") if k not in finding]
        if missing:
            errors.append(f"finding at {finding_path} missing required key(s): {missing}")
        elif not finding.get("evidence"):
            errors.append(f"finding at {finding_path} has an empty 'evidence' field")

    return errors


def main():
    fixture_files = sorted(glob.glob(".claude/skills/*/test-fixtures/*.json"))
    if not fixture_files:
        print("No fixtures found under .claude/skills/*/test-fixtures/ -- nothing to check.")
        return 0

    failed = False
    for path in fixture_files:
        errors = check_fixture(path)
        if errors:
            failed = True
            print(f"FAIL: {path}")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"OK: {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
