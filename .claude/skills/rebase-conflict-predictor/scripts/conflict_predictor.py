#!/usr/bin/env python3
"""
rebase-conflict-predictor helper script.

Predicts merge/rebase conflicts between a feature branch and a target
branch WITHOUT touching the working directory, index, or either branch --
uses `git merge-tree --write-tree`, which performs the trial merge
entirely in-memory and writes results only to a throwaway tree object
(never checked out, never referenced by any branch). Genuinely read-only:
no worktree, no checkout, no state left behind.

Requires git >= 2.38 (this repo has been verified against 2.53).

Usage:
    python conflict_predictor.py --branch <feature-branch> --target <target-branch> [--repo-path .]

Requires: git on PATH. No Python package dependencies.
"""
import argparse
import json
import re
import subprocess
import sys

CONFLICT_LINE_RE = re.compile(r"^CONFLICT \(([^)]+)\): (.*)$")
# The common format: "Merge conflict in <path>" (content, add/add, ...).
MERGE_CONFLICT_IN_RE = re.compile(r"^Merge conflict in (.+)$")
# Rename-style messages lead with the original path:
# "<path> renamed to <a> in <branch> and to <b> in <branch>." (rename/rename)
# "<path> renamed to <a> in <branch>, but deleted in <branch>."  (rename/delete)
RENAME_LEAD_PATH_RE = re.compile(r"^(\S+) renamed ")


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def extract_file(message):
    """Per-format path extraction. Returns None when the format isn't
    recognized -- an honest None beats a garbled guess (the original
    generic 'in <path>' heuristic produced garbage on rename/rename
    messages, whose format is entirely different)."""
    m = MERGE_CONFLICT_IN_RE.match(message)
    if m:
        return m.group(1)
    m = RENAME_LEAD_PATH_RE.match(message)
    if m:
        return m.group(1)
    return None


def parse_conflicts(stdout):
    conflicts = []
    for line in stdout.splitlines():
        m = CONFLICT_LINE_RE.match(line.strip())
        if not m:
            continue
        conflict_type, message = m.group(1), m.group(2)
        conflicts.append({
            "type": conflict_type,
            "message": message,
            "file": extract_file(message),
        })
    return conflicts


def branch_exists(repo_path, ref):
    result = run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo_path)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=".")
    parser.add_argument("--branch", required=True, help="The feature branch to check")
    parser.add_argument("--target", default="main", help="The branch it would merge/rebase into (default: main)")
    args = parser.parse_args()

    repo_path = args.repo_path

    for ref in (args.branch, args.target):
        if not branch_exists(repo_path, ref):
            print(json.dumps({"error": f"ref '{ref}' does not exist in this repository"}), file=sys.stderr)
            sys.exit(1)

    result = run(["git", "merge-tree", "--write-tree", args.target, args.branch], cwd=repo_path)

    if result.returncode not in (0, 1):
        print(json.dumps({"error": f"git merge-tree failed unexpectedly: {result.stderr.strip()}"}), file=sys.stderr)
        sys.exit(1)

    conflicts = parse_conflicts(result.stdout) if result.returncode == 1 else []

    output = {
        "branch": args.branch,
        "target": args.target,
        "conflict_predicted": result.returncode == 1,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
