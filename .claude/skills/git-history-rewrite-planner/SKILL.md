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
4. Inside the disposable clone, it scans **three separate surfaces** for each pattern: every commit's full file tree (not just diffs — a diff-only search would miss a commit that inherits the secret unchanged from a parent), every commit's full message text, and every annotated tag's own message text — these require three different git mechanisms to both find and fix, and missing any one of them leaves that surface silently unverified (see the live bugs below, including the most dangerous one: a secret can exist *only* in a tag message with zero file or commit hits at all).
5. If nothing is found on any surface: reports `no_matches_found_nothing_to_purge` immediately. Nothing else runs.
6. If found, this is the **Task Loop**: each round applies `git filter-repo --replace-text` (file content) **and** `--replace-message` (commit/tag messages) with the supplied patterns, then **independently re-scans all three surfaces of the rewritten disposable clone from scratch** to verify zero matches remain — never trusting the rewrite tool's exit code alone, the same "verify against real ground truth every round" discipline as `snowflake-ddl-diff`. Converges the moment verification finds zero matches everywhere; hard-caps at `--max-iterations` (default 3) per `.claude/rules/loop-engineering.md`.
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

**Seven real bugs found and fixed before this was ever trustworthy** — this skill has had more real bugs found via live testing than any other in this repo, which is fitting given it's also the highest-risk one (bugs 6-7 covered in their own section below, added when regex pattern support closed a documented backlog item):

1. **The disposable clone's "always deleted" safety promise was silently broken on Windows.** The original cleanup used `shutil.rmtree(temp_dir, ignore_errors=True)` — `ignore_errors=True` silently swallowed a `PermissionError` caused by git's own read-only pack/index files, meaning the disposable clone (containing the very secret it was created to purge) was left behind on disk after *every single run*, with no error or warning at all. Found live by checking for leftover temp directories after a real run. Fixed with the standard Windows read-only-clearing retry handler (`os.chmod` + retry), verified across multiple runs afterward with zero leftover directories.
2. **The scanner used `git grep -I`, which explicitly skips binary files.** A secret embedded in any file git classifies as binary would be completely invisible to the scan. Fixed to `git grep -a` (treat every file as text).
3. **Commit messages are a real, separate leak surface `--replace-text` never covers.** Requires the separate `--replace-message` flag; the scanner needed its own `scan_commit_messages()` check too, since `git grep` never looks at messages at all.
4. **The most dangerous one: a secret that exists ONLY in an annotated tag's own message (nowhere in any file or commit) was completely invisible to every prior scan.** `git log --all` (used for commit-message scanning) only walks commits reachable from refs — it never surfaces an annotated tag object's own message text, which is a genuinely separate git object. This meant the planner would confidently report `no_matches_found_nothing_to_purge` and **never even attempt a redaction** — the single worst possible failure mode for a tool whose entire purpose is finding leaked secrets, worse than a failed redaction because it gives false confidence that nothing needs to be done at all. Found live by deliberately planting a secret only in a tag message (confirmed via `git grep`/`git log` that it was invisible to both) and running the planner against it. `git filter-repo --replace-message` already correctly rewrites tag messages (confirmed independently) — the gap was purely in scanning. Fixed with a dedicated `scan_tag_messages()` using `git for-each-ref`, wired into every scan alongside the file-tree and commit-message checks.
5. **A display bug in the same fix:** the code that shortens long commit SHAs to 12 characters for readability was blindly applied to *every* "commit" value, including tag ref names — mangling `refs/tags/v9.9.9` into `refs/tags/v9`. Fixed to only shorten values that are actually 40-character hex SHAs.

**All outcome branches confirmed live:**
- **Text secret in ordinary files, 4 real commits (2 files):** converged in 1 iteration, and the *original* test repo was independently confirmed completely unchanged afterward — proving the "real repo never touched" guarantee, not just asserting it.
- **Secret embedded in a genuinely binary file:** correctly detected but `--replace-text` genuinely cannot rewrite binary blob content — ran all 3 allowed iterations, never falsely claimed convergence, surfaced the binary-specific escalation note.
- **A genuinely complex scenario** (2 secrets, a real branch + merge, a decoy value, one secret pasted into a commit message): correctly found the secret across the merge (6 real commits), correctly left the decoy (`CorrectHorseBattery00` vs. the real `CorrectHorseBattery99`) completely untouched, and — after the commit-message fix — correctly found and redacted the message-embedded copy, independently confirmed by hand.
- **A secret that exists only in a tag message, with case-variant decoys elsewhere:** correctly found and redacted the tag-only secret (bug #4 above); correctly left a different-casing variant of another secret untouched elsewhere in the same repo — a real, honest limitation (see below), not a bug.

## Known limitations (honest, not hidden)

- **Matching is literal and case-sensitive by default.** A secret that appears with different casing across history (`TokenValue_ABC123` vs. `tokenvalue_abc123`) needs a separate `--secret-pattern` entry for each variant — confirmed live: the exact-case form was correctly redacted while a differently-cased real occurrence elsewhere was correctly left untouched, since that's genuinely a different literal string.
- **Binary blob content cannot be redacted by text replacement** — see the `max_iterations_reached` / escalation-note path above; the real fix for that case is whole-file removal (`--path --invert-paths`), not text substitution.

## Regex pattern support, and a real scan/redaction mismatch it fixed

A `--secret-pattern` can now be prefixed with `regex:` (the same convention `git filter-repo` itself uses) to match a variable secret shape instead of one exact string — e.g. `regex:sk_live_[a-z0-9]+` catches every token matching that shape in one pass, not just one exact value.

**Building this surfaced two real, pre-existing bugs, not just a missing feature:**

1. **A literal pattern was silently being matched as regex, causing false positives.** The scanner used bare `git grep -e`, which defaults to regex interpretation — a perfectly ordinary literal secret shape like `sk.live.abc123` had its `.` match *any* character, so the scan reported a match against a completely unrelated string (`skXliveXabc123`) that never actually contained the real secret. Meanwhile `git filter-repo` treats a plain pattern as literal by default — a real mismatch between what the scanner considered "found" and what the redaction step would actually touch. Found live by deliberately constructing this exact scenario. Fixed: a plain pattern now uses `git grep -F` (fixed-string/literal), matching `filter-repo`'s own default semantics exactly.
2. **`git grep`'s default regex dialect silently failed to match ordinary regex patterns.** Once `regex:`-prefixed patterns were added, a completely standard pattern like `sk_live_[a-z0-9]+` matched **zero** real occurrences — `git grep`'s default mode is POSIX *Basic* regex, where `+`, `?`, and `{n,m}` are not special characters without a backslash, unlike the Python-style regex most people would write. Found live immediately after adding regex support, by testing the exact pattern above against two real, distinct tokens matching that shape. Fixed: a `regex:`-prefixed pattern now uses `git grep -P` (Perl-compatible), the closest available match to the Python `re` semantics `filter-repo` itself uses to interpret its own `regex:` patterns.

Both fixes were re-verified live: the literal-pattern case now correctly matches only the real secret and leaves the unrelated string alone; the regex case now correctly matches and redacts both real, distinct tokens sharing the pattern's shape in one pass.

## Verification status per branch (honest status, not hidden)

| Path | Live |
|---|---|
| `no_matches_found_nothing_to_purge` | ✅ (real repo, a pattern that never existed) |
| Full convergence via text redaction (multi-commit, multi-file) | ✅ |
| Real repo/remote never touched, independently verified after the fact | ✅ |
| Disposable clone cleanup, including Windows read-only pack files | ✅ (real bug found and fixed) |
| Binary-file false-negative in scanning | ✅ (real bug found and fixed) |
| `max_iterations_reached` with binary-content escalation note | ✅ (genuine, real limitation of `--replace-text`, correctly surfaced rather than hidden) |
| Secret across a real branch + merge (not just a linear chain) | ✅ (6-commit real merge history) |
| Precision: a decoy value never touched | ✅ |
| Secret existing ONLY in an annotated tag message | ✅ (the most dangerous bug found this session — real bug found and fixed) |
| Case-sensitivity limitation, correctly left unredacted | ✅ (confirmed as expected, honest behavior, not a bug) |
| Literal pattern with regex metacharacters, no false-positive matching | ✅ (real bug found and fixed — was silently matched as regex, causing a false-positive hit on unrelated text) |
| `regex:`-prefixed pattern matching a variable secret shape | ✅ (real bug found and fixed — `git grep`'s default BRE dialect silently failed to match ordinary quantifiers like `+`; fixed via `-P`) |
| Secret embedded in a commit message, not just file content | ✅ (real bug found and fixed — `--replace-message` was missing entirely, both from redaction and from scanning) |
| A secret that requires a regex (not literal) pattern to match | not yet tested — `--replace-text` supports regex rules with a `regex:` prefix; this script only ever generates literal rules |

## Loop tier & safety limits

The repo's third **Task Loop (retry-until-resolved)** skill, and its second **write-confirm** one — with the strictest safety boundary of any skill here: **the only thing this script is ever capable of writing to is a disposable clone it creates and deletes itself.** Per `.claude/rules/loop-engineering.md`:
- **Hard retry cap:** `--max-iterations` (default 3) — convergence should take exactly 1 round for ordinary text secrets, so hitting the cap is a real signal (usually: binary content) rather than "needs more attempts."
- **Each retry changes something real:** every round re-scans the actual rewritten disposable clone from scratch; a round that finds the same remaining matches as the last is still genuine new evidence, not a repeated no-op.
- **The real repo is structurally unreachable from this script** — there is no flag, mode, or combination of arguments that makes it write to the path passed in `--repo-path` itself, only to a clone of it. Applying the plan for real is always a separate, manual, human-run step.
