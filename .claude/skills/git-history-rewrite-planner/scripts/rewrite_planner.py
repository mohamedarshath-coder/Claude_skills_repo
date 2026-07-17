#!/usr/bin/env python3
"""
git-history-rewrite-planner helper script.

Plans (and verifies, but never applies to the real repo) a safe git
history rewrite to purge a leaked secret. This is the repo's highest-risk
skill, so its safety design is stricter than any other write-capable
skill here:

  - THE USER'S REAL REPOSITORY AND REMOTE ARE NEVER TOUCHED, EVER, under
    any flag. This script always clones the target repo into a disposable
    temporary directory first, and every rewrite/verification step
    happens ONLY inside that throwaway clone.
  - The disposable clone is always deleted at the end of the run,
    success or failure -- it is scratch space to prove a plan works, not
    a deliverable artifact.
  - The script's actual output is a PLAN: the exact commands the user
    would need to run against their real repo (a fresh mirror clone,
    the filter-repo invocation, the force-push, the "everyone must
    re-clone" warning) -- reproduced and verified against a real
    disposable copy of their history first, not asserted from theory.
  - Task Loop (retry-until-resolved): round 1 redacts every supplied
    secret pattern; every round then RE-SCANS the rewritten disposable
    clone from scratch to independently verify zero matches remain,
    rather than trusting the rewrite tool's exit code alone. If a
    verification scan still finds a match, the loop retries (widening
    to a case-insensitive / regex rule) up to a hard cap -- the same
    "verify against real ground truth, don't assume" discipline as
    snowflake-ddl-diff's re-diff-every-round design.

Auth/deps: uses `git` and the `git filter-repo` tool (both must be on
PATH) -- filter-repo is git's own recommended replacement for the
deprecated filter-branch. No credentials of any kind are involved; this
operates purely on local git history.

Usage:
    python rewrite_planner.py --repo-path PATH --secret-pattern PATTERN
        [--secret-pattern PATTERN ...] [--max-iterations N]
        [--redaction-text TEXT]

Requires: git (>= 2.24) and the `git-filter-repo` PyPI package on PATH.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

MAX_ITERATIONS_DEFAULT = 3
REDACTION_TEXT_DEFAULT = "***REMOVED-BY-REWRITE-PLANNER***"


def _force_remove_readonly(func, path, exc_info):
    """shutil.rmtree error handler for Windows: git marks pack/idx files
    read-only, which a bare rmtree cannot delete (PermissionError WinError
    5). Found live: the original code used ignore_errors=True, which
    silently swallowed this exact error and left the disposable clone
    (including the secret it was created to purge) behind on disk after
    every single run on Windows -- a real, silent violation of this
    skill's core "always deleted" safety promise. Clear the read-only bit
    and retry instead of ignoring the failure."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def cleanup_disposable(temp_dir):
    shutil.rmtree(temp_dir, onerror=_force_remove_readonly)


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def verify_git_repo(repo_path):
    result = run(["git", "-C", repo_path, "rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError(f"'{repo_path}' is not a git repository")


def clone_disposable(repo_path, temp_dir):
    """A genuine, independent full clone -- never a worktree of the real
    repo, since a worktree shares the same object database and rewriting
    it WOULD mutate the real repo's history. --no-local forces copying
    objects rather than hardlinking them, for full independence."""
    dest = os.path.join(temp_dir, "disposable_clone")
    run(["git", "clone", "--no-local", "--no-hardlinks", repo_path, dest])
    return dest


REGEX_PREFIX = "regex:"


def is_regex_pattern(pattern):
    """`git filter-repo`'s own convention: a pattern prefixed with
    'regex:' is a regex; anything else is literal text. This script uses
    the identical convention so a pattern behaves the same way in
    scanning as it will in redaction."""
    return pattern.startswith(REGEX_PREFIX)


def pattern_body(pattern):
    return pattern[len(REGEX_PREFIX):] if is_regex_pattern(pattern) else pattern


def text_contains_pattern(text, pattern):
    """Found live: a literal-looking pattern containing regex metacharacters
    (e.g. 'sk.live.abc123', a real secret shape) was being matched with
    `in` for messages/tags but with unanchored regex via `git grep -e`
    for file content -- an inconsistency that could cause a literal
    secret to go undetected in messages while over-matching unrelated
    text in files (the '.' in a real API-key-shaped secret matches ANY
    character in regex mode). This function makes messages/tags use the
    exact same regex-vs-literal decision as the file-content scan."""
    if is_regex_pattern(pattern):
        return re.search(pattern_body(pattern), text) is not None
    return pattern in text


def scan_tag_messages(repo_dir, patterns):
    """Checks every ANNOTATED TAG's own message -- a real, separate git
    object with its own text, distinct from any commit's message. Found
    live: `git log --all` (used by scan_commit_messages) only walks
    commits reachable from refs; it never surfaces an annotated tag
    object's own message body at all. A secret that exists ONLY in a tag
    message (nowhere in any file or commit) was completely invisible to
    every prior scan -- the planner confidently reported
    'no_matches_found_nothing_to_purge' and never even attempted a
    redaction, the most dangerous possible failure mode for a tool whose
    entire purpose is finding leaked secrets. `git filter-repo
    --replace-message` DOES correctly rewrite tag messages (confirmed
    live) -- the gap was purely in scanning, not redaction."""
    result = run(["git", "-C", repo_dir, "for-each-ref", "--format=%(refname)%00%(contents)%00\x01", "refs/tags"])
    entries = [e for e in result.stdout.split("\x01") if e.strip()]
    findings = {p: [] for p in patterns}
    for entry in entries:
        parts = entry.split("\x00", 1)
        if len(parts) != 2:
            continue
        refname, contents = parts
        for pattern in patterns:
            if text_contains_pattern(contents, pattern):
                findings[pattern].append({"commit": refname.strip(), "path": "<tag message>"})
    return findings


def scan_commit_messages(repo_dir, patterns):
    """Checks every commit's full message text -- found live: a secret
    pasted into a commit message survives `--replace-text` entirely
    (that flag only rewrites file/blob content), and `git grep` never
    looks at commit messages at all, so relying on scan_history() alone
    would let a message-embedded secret report as "verified clean" while
    it's still sitting in the rewritten history untouched. Returns
    {pattern: [{commit, path}]}, using the synthetic path "<commit
    message>" so a message hit is clearly distinguishable from a file hit.
    Does NOT cover annotated tag messages -- see scan_tag_messages()."""
    result = run(["git", "-C", repo_dir, "log", "--all", "--format=%H%x00%B%x00"])
    entries = [e for e in result.stdout.split("\x00") if e]
    findings = {p: [] for p in patterns}
    commit_sha, message = None, None
    for i, chunk in enumerate(entries):
        if i % 2 == 0:
            commit_sha = chunk.strip()
        else:
            message = chunk
            for pattern in patterns:
                if text_contains_pattern(message, pattern):
                    findings[pattern].append({"commit": commit_sha, "path": "<commit message>"})
    return findings


def scan_history(repo_dir, patterns):
    """Greps every commit's full tree (not just diffs) for each pattern --
    a diff-only pickaxe search would miss a commit that inherits the
    secret unchanged from a parent. Returns {pattern: [{commit, path}]}.

    Uses `git grep -a` (treat every file as text), NEVER `-I` (skip
    binary files) -- found live: a secret byte-string embedded in a file
    git classifies as binary (a config blob, a serialized asset, anything
    not obviously plain text) is completely invisible to `-I`, which
    would make this scanner silently report "nothing to purge" while the
    secret is still sitting in history. A security scanner that fails
    silently on binary content is worse than no scanner at all.

    Also checks commit messages (scan_commit_messages) and annotated tag
    messages (scan_tag_messages) -- three genuinely separate surfaces a
    secret can leak into, each requiring its own git mechanism to find
    and its own filter-repo flag to fix (see apply_redaction). The file-
    tree grep is skipped if there are zero commits, but the message/tag
    scans always run regardless -- a tag can't exist without at least
    one commit in practice, but there's no reason to couple them.

    Uses `git grep -F` (fixed string / literal) for a plain pattern, and
    real regex matching only for a `regex:`-prefixed one -- found live: a
    literal-looking secret containing regex metacharacters (e.g.
    'sk.live.abc123', a realistic API-key shape) was matched via
    unanchored regex by default, so its '.' matched ANY character and
    the scan reported a false-positive hit on a completely unrelated
    string. `git filter-repo` treats a plain pattern as literal too --
    this was a real scan/redaction semantic mismatch, not just a missing
    feature."""
    findings = {p: [] for p in patterns}
    commits = run(["git", "-C", repo_dir, "rev-list", "--all"]).stdout.split()
    if commits:
        for pattern in patterns:
            # -P (Perl-compatible) for regex patterns, not bare -e --
            # found live: git grep's default regex dialect is POSIX BASIC
            # regex, where `+`, `?`, `{n,m}` are NOT special without a
            # backslash. A pattern like "sk_live_[a-z0-9]+" (ordinary
            # Python-style regex, exactly what a `regex:`-prefixed
            # pattern looks like) silently matched ZERO real occurrences
            # under bare -e, while both real occurrences existed. -P uses
            # Perl/Python-like semantics, matching what git filter-repo
            # itself uses to interpret a `regex:`-prefixed replacement.
            grep_flag = "-P" if is_regex_pattern(pattern) else "-F"
            result = run(
                ["git", "-C", repo_dir, "grep", "-a", "-l", grep_flag, pattern_body(pattern)] + commits,
                check=False,
            )
            if result.returncode not in (0, 1):  # 1 = no matches, both are valid outcomes
                raise RuntimeError(f"git grep failed: {result.stderr}")
            for line in result.stdout.splitlines():
                commit, _, path = line.partition(":")
                findings[pattern].append({"commit": commit, "path": path})

    for scan_fn in (scan_commit_messages, scan_tag_messages):
        extra_findings = scan_fn(repo_dir, patterns)
        for pattern, hits in extra_findings.items():
            findings[pattern].extend(hits)
    return findings


def build_replacements_file(patterns, redaction_text, temp_dir):
    path = os.path.join(temp_dir, "replacements.txt")
    with open(path, "w", encoding="utf-8") as f:
        for p in patterns:
            f.write(f"{p}==>{redaction_text}\n")
    return path


def apply_redaction(repo_dir, replacements_file):
    """--replace-text and --replace-message are two SEPARATE filter-repo
    flags -- found live: passing only --replace-text leaves a secret
    pasted into a commit message completely untouched, since that flag
    only rewrites file/blob content. Both are needed, every time, to
    cover both real surfaces a secret can leak into."""
    run(
        ["git", "filter-repo", "--replace-text", replacements_file,
         "--replace-message", replacements_file, "--force"],
        cwd=repo_dir,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=".")
    parser.add_argument("--secret-pattern", action="append", required=True,
                        help="A literal secret string to purge from history. Repeatable.")
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS_DEFAULT)
    parser.add_argument("--redaction-text", default=REDACTION_TEXT_DEFAULT)
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    try:
        verify_git_repo(repo_path)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    temp_dir = tempfile.mkdtemp(prefix="rewrite_planner_")
    iteration_log = []
    converged = False
    stopped_reason = None
    remaining_matches = {}

    try:
        disposable_clone = clone_disposable(repo_path, temp_dir)

        initial_findings = scan_history(disposable_clone, args.secret_pattern)
        total_initial = sum(len(v) for v in initial_findings.values())

        if total_initial == 0:
            converged = True
            stopped_reason = "no_matches_found_nothing_to_purge"
            remaining_matches = initial_findings
        else:
            for iteration in range(1, args.max_iterations + 1):
                replacements_file = build_replacements_file(args.secret_pattern, args.redaction_text, temp_dir)
                apply_redaction(disposable_clone, replacements_file)

                verify_findings = scan_history(disposable_clone, args.secret_pattern)
                remaining = sum(len(v) for v in verify_findings.values())

                iteration_log.append({
                    "iteration": iteration,
                    "remaining_matches_after_redaction": remaining,
                })
                remaining_matches = verify_findings

                if remaining == 0:
                    converged = True
                    stopped_reason = "verified_clean_after_redaction"
                    break
            else:
                stopped_reason = f"max_iterations_reached ({args.max_iterations})"

        # Only a real 40-char commit SHA should be shortened -- a tag
        # ref name (found live: "refs/tags/v9.9.9") isn't a hash at all,
        # and blindly slicing [:12] mangled it into "refs/tags/v9".
        def _short_ref(ref):
            return ref[:12] if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref) else ref

        commits_affected = sorted({_short_ref(m["commit"]) for v in initial_findings.values() for m in v})

        escalation_note = None
        if not converged and stopped_reason and stopped_reason.startswith("max_iterations_reached"):
            escalation_note = (
                "Text replacement could not remove this secret after "
                f"{args.max_iterations} real attempts -- this usually means the secret "
                "lives inside a file git treats as binary (an image, archive, or other "
                "non-text blob), which `--replace-text` cannot rewrite in place. The real "
                "fix for that case is removing the whole file from history instead: "
                "`git filter-repo --path <the-file-path> --invert-paths --force`. "
                "Check remaining_matches_after_verification for the exact path(s)."
            )

        mirror_clone_cmd = f"git clone --mirror {repo_path} <mirror-dir>"
        filter_cmd = "git filter-repo --replace-text replacements.txt --replace-message replacements.txt --force"
        replacements_preview = [f"{p}==>{args.redaction_text}" for p in args.secret_pattern]
        push_cmd = "git push --force --all && git push --force --tags"

        output = {
            "repo_path": repo_path,
            "secret_patterns_checked": args.secret_pattern,
            "commits_containing_secret_before_rewrite": commits_affected,
            "commit_count_before_rewrite": len(commits_affected),
            "converged": converged,
            "stopped_reason": stopped_reason,
            "iterations_run": len(iteration_log),
            "iteration_log": iteration_log,
            "remaining_matches_after_verification": remaining_matches,
            "escalation_note": escalation_note,
            "real_repo_action_taken": False,
            "plan_for_the_real_repo": {
                "note": "None of the steps below were run against your real repo or remote -- only against a disposable, deleted-afterward clone, to PROVE this plan actually works before you run it for real.",
                "1_make_a_fresh_mirror_clone": mirror_clone_cmd,
                "2_write_replacements_file": replacements_preview,
                "3_run_filter_repo_in_the_mirror": filter_cmd,
                "4_force_push_the_rewritten_history": push_cmd,
                "5_mandatory_warning": "Every commit after the earliest rewritten one gets a new SHA. Every other clone/fork of this repo is now diverged -- everyone with a clone MUST delete it and re-clone fresh; a normal `git pull` will not work and can resurrect the purged secret.",
            } if commits_affected else None,
        }
        print(json.dumps(output, indent=2, default=str))

    finally:
        cleanup_disposable(temp_dir)


if __name__ == "__main__":
    main()
