---
name: rebase-conflict-predictor
description: Predicts which files will conflict if a feature branch is merged or rebased onto a target branch, without touching the working directory, index, or either branch. Use when asked whether a branch will conflict, to check merge/rebase readiness, or before starting a rebase on a long-lived branch.
risk: read-only
loop-tier: on-demand
---

## Purpose

Discovering a merge conflict only at PR time, after a feature branch has drifted far from `main`, turns a small fix into an unpleasant rebase. This skill predicts conflicts *before* anyone attempts the merge — genuinely read-only, no trial checkout, no working-directory side effects.

## When to use

Use this skill when asked: whether a branch will conflict with another, to check merge/rebase readiness before starting work, or as a pre-flight check before rebasing a long-lived feature branch.

## Steps

1. Identify the **feature branch** and the **target branch** (default `main`) to check against. Both must exist as local refs (or be fetched first if only remote).
2. Run `python {{SKILL_DIR}}/scripts/conflict_predictor.py --branch <feature-branch> --target <target-branch>` (accepts `--repo-path` if not run from the repo root).
3. The script runs `git merge-tree --write-tree <target> <branch>` — a trial merge performed **entirely in-memory**. It writes a throwaway tree object (never checked out, never referenced by any branch, never touches the working directory or index) and exits `0` for a clean merge or `1` if conflicts exist.
4. Parse the `CONFLICT (...)` lines from the output for the conflict type and affected file(s).
5. Report clearly whether a conflict is predicted, and for which files — if none, say so plainly rather than padding the report.

## Helper scripts

- `{{SKILL_DIR}}/scripts/conflict_predictor.py` — wraps `git merge-tree --write-tree` (requires git ≥ 2.38; this repo verified against 2.53) and parses its output into structured JSON. No worktree, no checkout, no external dependencies beyond `git` on PATH — genuinely the least invasive skill in this repo, since it has no external system access and doesn't even touch the local working directory.

## Output format

Render as Markdown:

1. **Summary line** — bolded: conflict predicted or not, between which two branches.
2. **Conflicting files (table)** — only if `conflict_predicted` is true:

   | File | Conflict type |
   |---|---|
   | `shared.txt` | content |

3. **Bottom line** — one bolded sentence: "Safe to merge/rebase now" or "N file(s) will conflict — resolve these before rebasing," naming the files.

## Verified live (not just fixture-tested)

Tested against a real, deliberately-constructed scenario on throwaway branches (created and deleted in the same session, never merged): two branches diverging from a common base, each editing the same line of the same file differently. The script correctly predicted the conflict and named the exact file. A second branch with a genuinely non-overlapping change (appending a new line) was correctly predicted as a clean merge. In both cases, `git status` and the checked-out branch were confirmed unchanged after the script ran — proving the "no working-directory side effects" claim, not just asserting it.

**Gap-closing pass — binary conflicts, and 4 simultaneous conflict types in one prediction:** a second real scenario, two branches diverging from a shared base with four different files each triggering a different case at once — a text content conflict, an add/add (both branches creating the same new path with different content), a binary-file conflict, and a same-anchor-point append conflict (both branches appending a different line at the same location in an otherwise-untouched file). All 4 were predicted correctly and cross-checked against the raw `git merge-tree` output directly, not just the script's own JSON. The binary case in particular closed the one previously-untested branch: git's `merge-tree` has no distinct "binary" conflict type — it emits a separate `warning: Cannot merge binary files: ...` line (correctly ignored, since it isn't a `CONFLICT (...)` line) followed by an ordinary `CONFLICT (content): ...` line, so the script's `content` label for a binary file is correct, not a misclassification. `git status` and the checked-out branch were confirmed unchanged afterward, even with 4 simultaneous conflicts predicted.

## Verification status per conflict type (honest status, not hidden)

| Conflict type | Live-tested | Notes |
|---|---|---|
| `content` | ✅ | Original verification |
| `add/add` (both branches create the same new path) | ✅ | Gap-closing pass |
| `rename/rename` (both branches rename the same file differently) | ✅ | **Found a real bug**: the original generic `in <path>` extraction produced garbage on this format ("conflict-gap-b and to .../renamed-by-a.txt in..."). Fixed with per-format extraction — rename messages resolve to the *original* path. Pinned by `test-fixtures/test_parse.py` |
| `rename/delete` | ✅ | File resolves to original path |
| 3 simultaneous conflicts in one prediction | ✅ | All parsed distinctly |
| 4 simultaneous conflicts, incl. binary, in one prediction | ✅ | Gap-closing pass: content, add/add, content (binary), content across 4 files, all parsed correctly in one run |
| Binary-file conflicts | ✅ | Git emits no distinct "binary" conflict type in `merge-tree` output — a binary conflict surfaces as an ordinary `CONFLICT (content): ...` line, preceded by a separate `warning: Cannot merge binary files: ...` line (correctly ignored by the parser, since it isn't a `CONFLICT (...)` line). So the script's `content` label for a binary file is accurate, not a misclassification — there was never a distinct format to detect. |
| Unrecognized future formats | unit-tested | `file: null` (honest) rather than a garbled guess |

## Loop tier

**On-demand (Tier 1)**, and likely to stay there — there's no obvious scheduled/product-loop variant for this skill (predicting conflicts on-demand before a specific rebase is the natural usage pattern, not a periodic background check).
