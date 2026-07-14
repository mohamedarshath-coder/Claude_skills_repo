---
name: git-bisect-assistant
description: Orchestrates git bisect against a test command to find the exact commit that introduced a regression, isolated in a temporary worktree so the user's working directory is never touched. Use when asked to find which commit broke something, or to bisect a regression.
risk: read-only
loop-tier: retry-until-resolved
---

## Purpose

"Something broke between last week and today, which commit did it?" normally means manually running `git bisect` yourself: checking out commits one at a time, re-running a test, telling git good/bad, repeating until it converges — and hoping you don't accidentally leave your own working directory on a random historical commit halfway through. This skill automates the whole loop and never touches your actual checkout.

## When to use

Use this skill when asked to find which commit introduced a regression, to bisect a bug, or "this used to work, what broke it."

## Steps

1. Identify a **known-good ref** (a commit/tag where the behavior was correct) and a **known-bad ref** (default `HEAD`, where it's currently broken), and a **test command** that exits `0` when the behavior is correct and non-zero when it's broken. If the user hasn't specified these, ask — this skill cannot guess what "good" and "bad" mean for their code.
2. Run `python {{SKILL_DIR}}/scripts/bisect_assistant.py --good-ref <ref> --bad-ref <ref> --test-command "<command>"` (accepts `--repo-path` if not run from the repo root, `--max-steps` to adjust the retry cap, default 25).
3. The script:
   - Creates a **temporary, isolated git worktree** via `git worktree add --detach` — the user's actual branch and working directory are never checked out away from, at any point
   - Runs real `git bisect start` / `bad` / `good` inside that isolated worktree
   - Loops: check out the next bisect candidate, run the test command, tell git bisect the verdict, repeat — this is the **Task Loop**: each retry checks out a genuinely different commit (never repeats identical state) and there's a hard cap (`--max-steps`) so a flaky or non-deterministic test command can't loop forever
   - Always cleans up (`git bisect reset`, worktree removal) in a `finally` block, even if the test command itself crashes
4. If it converges, report the culprit commit (hash, author, date, message) and how many steps it took. If it hits `--max-steps` without converging, say so plainly and flag that this usually means the test command itself is flaky/non-deterministic, not that bisect needs more attempts — do not silently retry past the cap.

## Helper scripts

- `{{SKILL_DIR}}/scripts/bisect_assistant.py` — orchestrates the bisect loop entirely inside a disposable worktree; never modifies the user's actual branch/working directory. Uses only `git` on PATH — no additional dependencies, no credentials involved (this skill has no external system access at all).

## Output format

Render as Markdown:

1. **Summary line** — bolded: converged or not, in how many steps.
2. **Culprit commit** (if converged):

   | Field | Value |
   |---|---|
   | Commit | `c90d7b5` |
   | Author | ... |
   | Date | ... |
   | Message | ... |

3. **Step log** — a short table of each step (commit tested, verdict) so the reasoning is auditable, not just the final answer.
4. **If it didn't converge:** state the max-steps cap was hit and recommend checking the test command for flakiness before re-running — never imply a higher `--max-steps` will definitely fix it.

## Verified live (not just fixture-tested)

Three real scenarios, all on throwaway branches (created and deleted in the same session, never merged):

1. **Linear history** — 5 commits, bug introduced in commit 3, two unrelated commits after. Converged in 2 steps to the exact culprit. `test-fixtures/sample-output.json` is the real output from this run.
2. **Merge-commit history** — the bug introduced on a *side branch* and arriving on the mainline via a `--no-ff` merge, with unrelated commits on both sides. Correctly converged to the side-branch commit itself — not the merge commit, not the unrelated mainline commit. Bisect through genuinely branched history is proven.
3. **Non-convergence / cap path** — run with a deliberately too-low `--max-steps 1`: correctly reported `converged: false` with the explicit cap-hit error and exit code 1, and did **not** fabricate a culprit. (Note: the cap message attributes non-convergence to a likely-flaky test command; when the cap is simply set too low for the range, that's the other possible cause — the message names the more dangerous one.)

## Verification status (honest, per scenario)

| Scenario | Live-tested |
|---|---|
| Linear history, deterministic test | ✅ |
| Merge commits in the bisect range | ✅ |
| Cap-hit / non-convergence path | ✅ |
| Genuinely flaky (non-deterministic) test command | ❌ — the cap protects against it, but a real flaky test has never been run; a flaky test can also cause bisect to converge on a *wrong* commit rather than hit the cap, which no cap can detect |
| Test command with external side effects | ❌ — the worktree isolates git state only, not databases/networks the test touches |

## Loop tier & safety limits

This is the repo's first **Task Loop (retry-until-resolved)** skill — every other skill so far is `on-demand` or the one `scheduled-notify-only` promotion. Per `.claude/rules/loop-engineering.md`:
- **Hard retry cap:** `--max-steps` (default 25), well above real bisect convergence (log2 of any realistic commit range), so hitting it is itself a signal something's wrong with the test command.
- **Each retry changes something:** guaranteed by `git bisect` itself — it always checks out a new candidate commit, never repeats one.
- **No mutation of user state:** the isolation-via-worktree design means this skill's retry loop can never leave the user's actual branch/working directory in an unexpected state, even if it crashes mid-run.

No promotion beyond this tier applies — `retry-until-resolved` is a terminal tier for this kind of skill, not a step toward `scheduled-notify-only` (there's no meaningful "schedule this bisect to run automatically").
