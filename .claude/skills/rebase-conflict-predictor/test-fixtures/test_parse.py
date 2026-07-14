#!/usr/bin/env python3
"""
Unit tests for conflict_predictor's parse_conflicts/extract_file.

These message formats were captured from REAL `git merge-tree --write-tree`
output (git 2.53) during the live gap-closing pass -- the same pass that
found the original generic 'in <path>' heuristic producing garbage on
rename/rename messages (it extracted 'conflict-gap-b and to .../renamed-by-a.txt
in conflict-gap-a.' as the "file"). These tests pin the fixed per-format
behavior so a parser regression can't sneak back in.

Run: python test_parse.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from conflict_predictor import parse_conflicts  # noqa: E402

# Verbatim real output shapes from git 2.53 merge-tree.
REAL_OUTPUT = """73ef0795fd3a4dc4487da4f7ca1fae66e3bacc87
100644 83db48f84ec878fbfb30b46d16630e944e34f205 1\ttools/ci/_conflict_demo/second.txt

Auto-merging tools/ci/_conflict_demo/newfile.txt
CONFLICT (add/add): Merge conflict in tools/ci/_conflict_demo/newfile.txt
CONFLICT (rename/rename): tools/ci/_conflict_demo/original.txt renamed to tools/ci/_conflict_demo/renamed-by-b.txt in branch-b and to tools/ci/_conflict_demo/renamed-by-a.txt in branch-a.
Auto-merging tools/ci/_conflict_demo/second.txt
CONFLICT (content): Merge conflict in tools/ci/_conflict_demo/second.txt
CONFLICT (rename/delete): tools/ci/_conflict_demo/original.txt renamed to tools/ci/_conflict_demo/renamed-by-a.txt in branch-a, but deleted in branch-c.
"""


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    conflicts = parse_conflicts(REAL_OUTPUT)
    by_type = {c["type"]: c for c in conflicts}

    check("all four conflict lines parsed", len(conflicts) == 4)
    check("content: file extracted",
          by_type["content"]["file"] == "tools/ci/_conflict_demo/second.txt")
    check("add/add: file extracted",
          by_type["add/add"]["file"] == "tools/ci/_conflict_demo/newfile.txt")
    check("rename/rename: file is the ORIGINAL path (the regression that was live-caught)",
          by_type["rename/rename"]["file"] == "tools/ci/_conflict_demo/original.txt")
    check("rename/delete: file is the original path",
          by_type["rename/delete"]["file"] == "tools/ci/_conflict_demo/original.txt")
    check("non-CONFLICT lines (tree hash, Auto-merging, stage entries) are ignored",
          all(c["type"] in ("content", "add/add", "rename/rename", "rename/delete") for c in conflicts))
    check("unrecognized message format yields file=None, not a garbled guess",
          parse_conflicts("CONFLICT (weird/new): some future git message format here")[0]["file"] is None)
    check("clean output (no CONFLICT lines) yields empty list",
          parse_conflicts("abc123\nAuto-merging x\n") == [])

    print(f"\n{len(failures)} failure(s) of 8 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
