---
name: git-history-rewrite-planner
description: Plans and verifies a safe git history rewrite to purge a leaked secret -- entirely inside a disposable, deleted-afterward clone, never touching the user's real repository or remote. Reports the exact commands to run for real only after proving they work. Use when a secret has been found committed to git history and needs to be purged, or to plan a history rewrite before running one for real.
risk: write-confirm
loop-tier: retry-until-resolved
---

## Purpose

A secret gets committed, `git rm` and a new commit don't actually remove it from history, and now someone has to plan an actual history rewrite (`git filter-repo`, force-push, everyone re-clones) — a genuinely high-stakes, hard-to-reverse operation that's easy to get wrong on the very repo that matters. This skill plans that rewrite and *proves it actually works* against a disposable, throwaway copy of the real history first — the user's actual repository and remote are never touched by this skill, under any flag, at any point.

This skill exists because of a real incident in this project: a Snowflake password was found committed to `.claude/settings.json` on `main`. The decision made at the time was to rotate the credential and leave history as-is rather than rewrite it unplanned — this skill is what should exist *before* that decision has to be made blind next time.

## When to use

Use this skill when: a secret has been found committed to git history and needs to be purged, or before running any real history rewrite, to prove the plan works first.

## Steps

1. Identify the exact secret string(s) to purge (`--secret-pattern`, repeatable) and the path to the real repository (`--repo-path`, defaults to the current directory).
2. Run `python {{SKILL_DIR}}/scripts/rewrite_planner.py --repo-path PATH --secret-pattern "the-secret" [--secret-pattern "another-one"] [--max-iterations N]` (default 3).
3. **The script never touches the real repository or remote.** It always makes a genuine, independent full clone (`git clone --no-local --no-hardlinks`, never a worktree, which would share the same object database) into a disposable temporary directory, and every subsequent step happens only there.
4. Inside the disposable clone, it scans every commit's full tree (not just diffs — a diff-only search would miss a commit that inherits the secret unchanged from a parent) for each pattern.
5. If nothing is found: reports `no_matches_found_nothing_to_purge` immediately. Nothing else runs.
6. If found, this is the **Task Loop**: each round applies `git filter-repo --replace-text` with the supplied patterns, then **independently re-scans the rewritten disposable clone from scratch** to verify zero matches remain — never trusting the rewrite tool's exit code alone, the same "verify against real ground truth every round" discipline as `snowflake-ddl-diff`. Converges the moment verification finds zero matches; hard-caps at `--max-iterations` (default 3) per `.claude/rules/loop-engineering.md`.
7. **If the cap is hit without converging**, this usually means the secret lives inside a file git treats as binary — `--replace-text` cannot rewrite binary blob content in place. The output's `escalation_note` says so explicitly and recommends the real fix for that case (`git filter-repo --path <file> --invert-paths`, removing the whole file from history instead of trying to redact text inside it).
8. The disposable clone is **always deleted** at the end, success or failure — including correctly handling git's read-only pack/index files on Windows, which a bare `shutil.rmtree` cannot remove (see the live bug below).
9. The script's real output is a **plan**: the exact commands (fresh mirror clone, the filter-repo invocation, the force-push, the mandatory re-clone warning for every other clone/fork) for the user to run against their real repo, verified against the disposable copy first. This skill never runs any of those five steps against the real repo itself — that remains a deliberate, separate, human-executed action.

## Helper scripts

- `{{SKILL_DIR}}/scripts/rewrite_planner.py` — orchestrates `git` and `git filter-repo` (both must be on PATH) entirely within a disposable clone; no credentials or external system access of any kind.

## Output format

Render as Markdown:

1. **Summary line** — bolded: converged or not, how many commits contained the secret before rewrite, in how many iterations.
2. **Commits affected (before rewrite)** — the real commit list.
3. **Convergence log** — per-iteration remaining-match count, so the verification loop is auditable, not just the final answer.
4. **If not converged:** the `escalation_note` verbatim, plus the exact remaining matches (commit + path).
5. **The real-repo plan** — the 5 numbered commands, with the explicit statement that none of them were run for real, only verified against a disposable copy.
6. **Bottom line** — one bolded sentence: "Verified clean — here is the exact plan to run for real" or "Could not fully purge via text replacement — see the escalation note before proceeding."

## Verified live (not just fixture-tested)

Built and tested against real, throwaway git repositories (created and destroyed purely for this test, never this project's repo).

**Two real bugs found and fixed before this was ever trustworthy:**

1. **The disposable clone's "always deleted" safety promise was silently broken on Windows.** The original cleanup used `shutil.rmtree(temp_dir, ignore_errors=True)` — `ignore_errors=True` silently swallowed a `PermissionError` caused by git's own read-only pack/index files, meaning the disposable clone (containing the very secret it was created to purge) was left behind on disk after *every single run*, with no error or warning at all. Found live by checking for leftover temp directories after a real run. Fixed with the standard Windows read-only-clearing retry handler (`os.chmod` + retry), verified across multiple runs afterward with zero leftover directories.
2. **The scanner used `git grep -I`, which explicitly skips binary files.** This meant a secret embedded in any file git classifies as binary (an image, archive, or other non-text blob) would be completely invisible to the scan — the script would confidently report `no_matches_found_nothing_to_purge` while the secret was still sitting in history untouched. This is a dangerous false negative for a security tool. Found live by deliberately planting a secret inside a binary test file and confirming the original scan missed it entirely. Fixed to `git grep -a` (treat every file as text); re-verified the same binary-embedded secret is now correctly detected.

**Both real outcome branches confirmed live, using the fix above:**
- **Text secret in ordinary files, 4 real commits (2 files):** converged in 1 iteration, verified zero remaining matches, and the *original* test repo was independently confirmed completely unchanged afterward (same commit SHAs, secret still present in the untouched original) — proving the "real repo never touched" guarantee, not just asserting it.
- **Secret embedded in a genuinely binary file:** correctly *detected* (post-fix) but `--replace-text` genuinely cannot rewrite binary blob content — the loop correctly ran all 3 allowed iterations, never falsely claimed convergence, and surfaced the binary-specific escalation note recommending whole-file removal instead. A real, honest limitation of text-replacement rewriting, not a bug.

## Verification status per branch (honest status, not hidden)

| Path | Live |
|---|---|
| `no_matches_found_nothing_to_purge` | ✅ (real repo, a pattern that never existed) |
| Full convergence via text redaction (multi-commit, multi-file) | ✅ |
| Real repo/remote never touched, independently verified after the fact | ✅ |
| Disposable clone cleanup, including Windows read-only pack files | ✅ (real bug found and fixed) |
| Binary-file false-negative in scanning | ✅ (real bug found and fixed) |
| `max_iterations_reached` with binary-content escalation note | ✅ (genuine, real limitation of `--replace-text`, correctly surfaced rather than hidden) |
| A secret that requires a regex (not literal) pattern to match | not yet tested — `--replace-text` supports regex rules with a `regex:` prefix; this script only ever generates literal rules |

## Loop tier & safety limits

The repo's third **Task Loop (retry-until-resolved)** skill, and its second **write-confirm** one — with the strictest safety boundary of any skill here: **the only thing this script is ever capable of writing to is a disposable clone it creates and deletes itself.** Per `.claude/rules/loop-engineering.md`:
- **Hard retry cap:** `--max-iterations` (default 3) — convergence should take exactly 1 round for ordinary text secrets, so hitting the cap is a real signal (usually: binary content) rather than "needs more attempts."
- **Each retry changes something real:** every round re-scans the actual rewritten disposable clone from scratch; a round that finds the same remaining matches as the last is still genuine new evidence, not a repeated no-op.
- **The real repo is structurally unreachable from this script** — there is no flag, mode, or combination of arguments that makes it write to the path passed in `--repo-path` itself, only to a clone of it. Applying the plan for real is always a separate, manual, human-run step.
