#!/usr/bin/env python3
"""
unit-tests CI job.

Discovers and runs every test_*.py under .claude/skills/*/test-fixtures/.
Each test file is a standalone script: exit 0 = pass, nonzero = fail.
These complement fixture_test.py (which checks JSON fixture structure) by
actually EXECUTING skill logic against constructed inputs -- the mechanism
for covering branches that real account data hasn't naturally triggered yet.

Usage: python tools/ci/unit_tests.py
Exit code 0 = all test files pass, 1 = at least one failure (fails the PR).
"""
import glob
import subprocess
import sys


def main():
    test_files = sorted(glob.glob(".claude/skills/*/test-fixtures/test_*.py"))
    if not test_files:
        print("No unit-test files found under .claude/skills/*/test-fixtures/ -- nothing to run.")
        return 0

    failed = False
    for path in test_files:
        result = subprocess.run([sys.executable, path], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"OK: {path}")
        else:
            failed = True
            print(f"FAIL: {path}")
            print(result.stdout)
            print(result.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
