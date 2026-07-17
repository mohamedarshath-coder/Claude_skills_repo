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
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from rewrite_planner import build_replacements_file, _force_remove_readonly, scan_commit_messages, scan_tag_messages  # noqa: E402

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

# --- scan_commit_messages: regression test for the live-found gap ---
# (git filter-repo's --replace-text never touches commit messages at
# all; a secret pasted into one is a real, separate leak surface, found
# live when a secret survived redaction completely undetected because
# the scanner only ever checked file content via `git grep`.)
with tempfile.TemporaryDirectory() as tmp:
    def git(*args):
        subprocess.run(["git", "-C", tmp] + list(args), check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    with open(os.path.join(tmp, "f.txt"), "w") as f:
        f.write("hello")
    git("add", "f.txt")
    git("commit", "-q", "-m", "a commit whose message mentions SECRET_IN_MESSAGE_123 by mistake")
    with open(os.path.join(tmp, "f.txt"), "w") as f:
        f.write("hello again")
    git("add", "f.txt")
    git("commit", "-q", "-m", "an unrelated commit with no secret in it")

    findings = scan_commit_messages(tmp, ["SECRET_IN_MESSAGE_123"])
    check("secret embedded in a commit message (never in file content) is found", len(findings["SECRET_IN_MESSAGE_123"]) == 1)
    check("the hit is labeled as a commit-message hit, not a file path",
          findings["SECRET_IN_MESSAGE_123"][0]["path"] == "<commit message>")

    clean_findings = scan_commit_messages(tmp, ["never_appears_anywhere"])
    check("a pattern that appears in no message returns zero hits", clean_findings["never_appears_anywhere"] == [])

    # --- scan_tag_messages: regression test for the most dangerous bug found ---
    # (a secret existing ONLY in an annotated tag's own message, nowhere
    # in any file or commit, was completely invisible to every prior
    # scan -- the planner confidently reported "nothing to purge" and
    # never even attempted a redaction.)
    git("tag", "-a", "v1.0", "-m", "Release v1.0 -- rollback token TAG_ONLY_SECRET_456 if needed")

    tag_findings = scan_tag_messages(tmp, ["TAG_ONLY_SECRET_456"])
    check("a secret that exists ONLY in a tag message is found", len(tag_findings["TAG_ONLY_SECRET_456"]) == 1)
    check("the hit is labeled as a tag-message hit", tag_findings["TAG_ONLY_SECRET_456"][0]["path"] == "<tag message>")
    check("the tag ref name is preserved, not mangled", tag_findings["TAG_ONLY_SECRET_456"][0]["commit"] == "refs/tags/v1.0")

    check("scan_commit_messages alone does NOT see the tag-only secret (proves the two scans are genuinely separate)",
          scan_commit_messages(tmp, ["TAG_ONLY_SECRET_456"])["TAG_ONLY_SECRET_456"] == [])

    clean_tag_findings = scan_tag_messages(tmp, ["never_appears_anywhere"])
    check("a pattern in no tag returns zero hits", clean_tag_findings["never_appears_anywhere"] == [])

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All rewrite_planner unit tests passed.")
sys.exit(0)
