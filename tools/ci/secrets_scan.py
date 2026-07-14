#!/usr/bin/env python3
"""
secrets-scan CI job.

Lightweight, dependency-free pattern scan for obvious committed secrets --
NOT a replacement for Gitleaks/TruffleHog (the proposal's Section 7.5c
names those as the eventual tool), just a first line of defense that needs
no extra installs to run in CI today.

Scans tracked files for:
  - Databricks personal access tokens (dapi + 32+ hex chars)
  - AWS access key IDs (AKIA + 16 alnum chars)
  - Generic "password/secret/token = <literal non-empty string>" assignments
    that aren't clearly reading from an environment variable or a placeholder

Usage: python tools/ci/secrets_scan.py
Exit code 0 = clean, 1 = at least one match (fails the PR).
"""
import re
import subprocess
import sys

PATTERNS = [
    ("Databricks personal access token", re.compile(r"dapi[a-f0-9]{20,}")),
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
]

# A literal-looking assignment: password = "something", api_token: 'abc123', etc.
# Uses \w* around the keyword (not \b...\b) so compound identifiers like
# `api_token` or `my_secret_key` are caught too -- a plain \b boundary
# misses these because underscores are word characters, so "api_token"
# has no boundary before "token". Excludes anything that clearly
# references an env var, a placeholder, or is empty.
LITERAL_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \w*(password|secret|token|api[_-]?key)\w*
    \s*[:=]\s*
    ['"]([^'"]{6,})['"]
    """
)
SAFE_VALUE_HINTS = ("env", "environ", "placeholder", "your-", "xxx", "changeme", "<", "{{")

# Files that legitimately discuss secret patterns / are this scanner itself.
EXCLUDE_SUBSTRINGS = ("tools/ci/secrets_scan.py", ".claude/rules/portability.md")


def tracked_files():
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [f for f in result.stdout.splitlines() if not any(x in f for x in EXCLUDE_SUBSTRINGS)]


def scan_file(path):
    findings = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except (IsADirectoryError, PermissionError):
        return findings

    for label, pattern in PATTERNS:
        for m in pattern.finditer(text):
            findings.append(f"{label}: matched '{m.group()[:12]}...'")

    for m in LITERAL_ASSIGNMENT_RE.finditer(text):
        value = m.group(2)
        if any(hint in value.lower() for hint in SAFE_VALUE_HINTS):
            continue
        findings.append(f"literal-looking {m.group(1)} assignment: '{m.group(0)[:60]}'")

    return findings


def main():
    failed = False
    for path in tracked_files():
        findings = scan_file(path)
        if findings:
            failed = True
            print(f"FAIL: {path}")
            for f in findings:
                print(f"  - {f}")

    if not failed:
        print(f"OK: scanned {len(tracked_files())} tracked files, no secret-shaped patterns found.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
