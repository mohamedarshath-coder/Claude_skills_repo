---
name: dependency-graph-impact-analyzer
description: Given a changed file in this repo, determines which skills are actually affected -- shared rule/CI changes affect every skill, a skill's own file affects just that skill (plus any skill that references it directly). Use when asked what a file change would break, or the blast radius of an edit before merging.
risk: read-only
loop-tier: on-demand
---

## Purpose

In a monorepo with multiple skills sharing common conventions (`.claude/rules/`, `tools/ci/`), "what does changing this one file actually affect?" isn't obvious by inspection. A shared rule file quietly underpins every skill's authoring standard; a skill's own script usually affects only that skill — but not always, if another skill happens to call into it. This skill answers that question with actual evidence, not a guess.

## When to use

Use this skill when asked: what would changing this file break, the blast radius of an edit before merging a PR, or which skills depend on a given shared file.

## Steps

1. Identify the changed file's path, relative to the repo root.
2. Run `python {{SKILL_DIR}}/scripts/impact_analyzer.py --changed-file <path>` (accepts `--repo-path` if not run from the repo root).
3. The script applies two-tier reasoning:
   - **Global scope** — if the changed file is under `.claude/rules/`, `tools/ci/`, `.github/workflows/`, or is `CLAUDE.md`/`.claude/settings.json`, **every skill** is impacted. This is a repo-convention judgment call, not derived from text references: these files are the shared authoring rules and CI pipeline every skill relies on, even though no individual skill's own files literally reference them by path.
   - **Single-skill / cross-skill scope** — if the changed file lives inside one skill's own folder (`.claude/skills/<name>/...`), that skill is impacted by definition. The script then scans every *other* skill's files for a literal string reference to the changed file's path or basename — catching a genuine cross-skill dependency (one skill's script calling into another's) rather than assuming skills are always isolated.
   - **No impact** — a file that's neither global nor inside any skill folder (e.g. a top-level planning doc) is reported as affecting zero skills. Say so plainly rather than guessing at relevance.
4. Report the scope and the specific reason for each impacted skill — never just a bare list of names with no justification.

## Helper scripts

- `{{SKILL_DIR}}/scripts/impact_analyzer.py` — pure Python standard library, no dependencies, no external system access at all (reads only local repo files). Genuinely fast and safe to run on any change before committing.

## Output format

Render as Markdown:

1. **Summary line** — bolded: scope (`global` / `single-skill` / `cross-skill` / `none`) and how many skills impacted.
2. **Impacted skills (table)** — only if `impacted_skill_count > 0`:

   | Skill | Reason |
   |---|---|
   | `snowflake-cost-audit` | Belongs to this skill |

3. **Bottom line** — one bolded sentence: e.g. "This is a global convention change — re-validate all N skills before merging," or "Only `<skill>` is affected — safe to review in isolation," or "No skill depends on this file."

## Verified live (not just fixture-tested)

Run against this actual repo, not a synthetic example, for three distinct real cases:
- `.claude/rules/loop-engineering.md` (a shared rule file) → correctly returned **global scope, every skill folder present on disk at that moment** (7 at the time of the test, since this skill's own folder existed before its `SKILL.md` was even written — genuine real-time accuracy, not a cached assumption)
- `.claude/skills/snowflake-cost-audit/scripts/cost_audit.py` (a skill's own file) → correctly returned **single-skill scope**, only `snowflake-cost-audit`
- `Claude-Skills-Proposal-Expanded.md` (a top-level planning doc) → correctly returned **zero impact**

## Known untested paths (honest status, not hidden)

The **cross-skill** branch (a skill's file textually referenced by name/path inside a *different* skill's files) has never actually fired — no real cross-skill reference currently exists in this repo, since every skill's helper script only calls its own sibling files (e.g. `scheduled_run.py` calling `cost_audit.py`, both inside `snowflake-cost-audit/`). The code path is implemented and logically sound but unverified against a genuine positive case. The text-reference scan is also a simple substring match, not a real import/call-graph parser — it would miss an indirect reference (e.g. a path built up from string concatenation or an env var) and could theoretically false-positive on a coincidental substring match.

## Loop tier

**On-demand (Tier 1).** A reasonable pre-commit-hook use case exists (run automatically before a commit touching shared files), but that would be a genuinely different invocation mechanism (git hook, not a scheduled loop), not a Tier 3 promotion in the loop-engineering sense — not pursued here.
