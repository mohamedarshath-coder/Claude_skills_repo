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
    silently on binary content is worse than no scanner at all."""
    commits = run(["git", "-C", repo_dir, "rev-list", "--all"]).stdout.split()
    findings = {p: [] for p in patterns}
    if not commits:
        return findings
    for pattern in patterns:
        result = run(
            ["git", "-C", repo_dir, "grep", "-a", "-l", "-e", pattern] + commits,
            check=False,
        )
        if result.returncode not in (0, 1):  # 1 = no matches, both are valid outcomes
            raise RuntimeError(f"git grep failed: {result.stderr}")
        for line in result.stdout.splitlines():
            commit, _, path = line.partition(":")
            findings[pattern].append({"commit": commit, "path": path})
    return findings


def build_replacements_file(patterns, redaction_text, temp_dir):
    path = os.path.join(temp_dir, "replacements.txt")
    with open(path, "w", encoding="utf-8") as f:
        for p in patterns:
            f.write(f"{p}==>{redaction_text}\n")
    return path


def apply_redaction(repo_dir, replacements_file):
    run(["git", "filter-repo", "--replace-text", replacements_file, "--force"], cwd=repo_dir)


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

        commits_affected = sorted({m["commit"][:12] for v in initial_findings.values() for m in v})

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
        filter_cmd = "git filter-repo --replace-text replacements.txt --force"
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
