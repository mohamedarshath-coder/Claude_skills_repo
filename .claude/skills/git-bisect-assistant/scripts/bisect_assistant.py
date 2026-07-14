#!/usr/bin/env python3
"""
git-bisect-assistant helper script.

Orchestrates `git bisect` against a user-supplied test command, isolated
in a temporary git worktree so the user's actual working directory and
current branch are NEVER touched or checked out away from.

Loop-tier: retry-until-resolved (Task Loop) -- per .claude/rules/loop-engineering.md:
  - Hard retry cap (--max-steps, default 25 -- generous vs. bisect's real
    log2(n) convergence; a real run hitting this cap means something is
    wrong with the test command, not that bisect needs more attempts).
  - Each retry changes something: guaranteed by construction -- every
    bisect step checks out a genuinely different commit.
  - Always cleans up: the worktree and bisect state are removed in a
    `finally` block, even if the test command itself crashes.

Usage:
    python bisect_assistant.py --good-ref <ref> --bad-ref <ref> --test-command "<shell command>" [--repo-path .] [--max-steps 25]

The test command must exit 0 for "good" (working) and non-zero for "bad"
(broken) -- exactly what `git bisect run` expects.

Requires: git on PATH. No Python package dependencies.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


def run(cmd, cwd, check=True):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=isinstance(cmd, str))
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {cmd}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def get_commit_info(repo_path, ref):
    result = run(["git", "show", "-s", "--format=%H|%an|%ad|%s", ref], cwd=repo_path)
    sha, author, date, subject = result.stdout.strip().split("|", 3)
    return {"commit": sha, "author": author, "date": date, "subject": subject}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=".")
    parser.add_argument("--good-ref", required=True, help="A ref known to pass the test command")
    parser.add_argument("--bad-ref", default="HEAD", help="A ref known to fail the test command (default HEAD)")
    parser.add_argument("--test-command", required=True, help="Shell command; exit 0 = good, nonzero = bad")
    parser.add_argument("--max-steps", type=int, default=25)
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    worktree_dir = tempfile.mkdtemp(prefix="bisect-assistant-")
    steps_log = []
    result_payload = {"converged": False, "culprit": None, "steps": [], "error": None}

    try:
        run(["git", "worktree", "add", "--detach", worktree_dir, args.bad_ref], cwd=repo_path)

        run(["git", "bisect", "start"], cwd=worktree_dir)
        run(["git", "bisect", "bad", args.bad_ref], cwd=worktree_dir)
        bisect_out = run(["git", "bisect", "good", args.good_ref], cwd=worktree_dir).stdout

        for step_num in range(1, args.max_steps + 1):
            current_ref = run(["git", "rev-parse", "HEAD"], cwd=worktree_dir).stdout.strip()
            test_result = run(args.test_command, cwd=worktree_dir, check=False)
            is_good = test_result.returncode == 0

            steps_log.append({
                "step": step_num,
                "commit_tested": current_ref,
                "test_exit_code": test_result.returncode,
                "verdict": "good" if is_good else "bad",
            })

            bisect_out = run(["git", "bisect", "good" if is_good else "bad"], cwd=worktree_dir, check=False).stdout

            if "is the first bad commit" in bisect_out:
                culprit_sha = bisect_out.splitlines()[0].split()[0]
                result_payload["converged"] = True
                result_payload["culprit"] = get_commit_info(worktree_dir, culprit_sha)
                break
        else:
            result_payload["error"] = f"Did not converge within --max-steps={args.max_steps}. This means the test command is likely flaky or non-deterministic, not that bisect needs more attempts -- investigate the test command before re-running."

        result_payload["steps"] = steps_log
        result_payload["steps_used"] = len(steps_log)

    except Exception as e:
        result_payload["error"] = str(e)
    finally:
        # Always clean up, even on error -- the user's repo must be left exactly as found.
        try:
            run(["git", "bisect", "reset"], cwd=worktree_dir, check=False)
        except Exception:
            pass
        run(["git", "worktree", "remove", "--force", worktree_dir], cwd=repo_path, check=False)
        shutil.rmtree(worktree_dir, ignore_errors=True)

    print(json.dumps(result_payload, indent=2, default=str))
    sys.exit(0 if result_payload["converged"] else 1)


if __name__ == "__main__":
    main()
