#!/usr/bin/env python3
"""
Unit tests for rewrite_planner's pure-logic functions -- the replacements
file format (git filter-repo's own required syntax) and the read-only
cleanup handler, since most of this script's real behavior (clone, scan,
redact, verify) was proven via live tests against real disposable git
repos, not constructable as pure-function unit tests.

Run: python test_rewrite_planner.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from rewrite_planner import build_replacements_file, _force_remove_readonly  # noqa: E402

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# --- build_replacements_file ---
with tempfile.TemporaryDirectory() as tmp:
    path = build_replacements_file(["secret_one", "secret_two"], "***GONE***", tmp)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    check("replacements file uses git filter-repo's own '==>' syntax", "secret_one==>***GONE***" in content)
    check("multiple patterns each get their own line", content.count("==>") == 2)
    check("second pattern present too", "secret_two==>***GONE***" in content)

# --- _force_remove_readonly: the live-found Windows cleanup bug ---
with tempfile.TemporaryDirectory() as tmp:
    target = os.path.join(tmp, "readonly_file.txt")
    with open(target, "w") as f:
        f.write("content")
    os.chmod(target, stat.S_IREAD)  # simulate git's read-only pack/idx files

    removed = {"called": False}

    def fake_remove(path):
        removed["called"] = True
        os.remove(path)

    _force_remove_readonly(fake_remove, target, None)
    check("read-only handler clears the read-only bit before retrying removal", removed["called"])
    check("file is actually gone after the handler runs", not os.path.exists(target))

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All rewrite_planner unit tests passed.")
sys.exit(0)
