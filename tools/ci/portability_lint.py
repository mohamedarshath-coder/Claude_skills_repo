#!/usr/bin/env python3
"""
portability-lint CI job -- the check .claude/rules/portability.md promised.

Scans every SKILL.md BODY (frontmatter excluded -- the name/description
fields legitimately describe the skill however they like) for phrasing
that would break the "author once, run on any assistant" guarantee:

  1. Vendor/assistant references -- "Claude Code", "Cursor", "Copilot",
     "Codex", MCP servers. A portable skill describes WHAT to query and
     lets each runtime's own connector layer decide HOW (portability.md
     rule 4). Bare ".claude/" paths and the repo file "CLAUDE.md" are
     allowed: they're this repo's on-disk layout, not an assistant
     behavior.
  2. Slash-command invocation references -- `/skill-name` style. How a
     skill is invoked is runtime-specific; the body must not assume a
     slash-command mechanism exists (portability.md rule 1).
  3. Hardcoded helper-script paths -- `scripts/foo.py` or `./scripts/foo.py`
     instead of `{{SKILL_DIR}}/scripts/foo.py` (portability.md rule 3).
     Full repo-relative paths (".claude/skills/<other>/scripts/...") are
     allowed: naming another skill's file as an example or dependency is
     content, not an invocation path the runtime must resolve.
  4. Drive-letter absolute paths (C:\..., D:\...) -- unless the line
     carries an explicit placeholder marker ("path\\to" or "<...>"),
     since a labeled setup example with a placeholder is instructional,
     not a hardcoded path.

Hardcoded credentials are deliberately NOT re-checked here -- that is
secrets_scan.py's job, repo-wide, and duplicating it would mean two
sources of truth drifting apart.

Usage: python tools/ci/portability_lint.py
Exit code 0 = all SKILL.md bodies pass, 1 = at least one violation (fails the PR).
"""
import glob
import re
import sys

# Assistant/vendor phrases that must not appear in a portable skill body.
# Word-boundary matched, case-insensitive where the name isn't ambiguous
# with a common word ("Cursor" gets a case-sensitive match so prose about
# "the cursor object" in a DB-API context isn't flagged).
VENDOR_RES = [
    (re.compile(r"\bClaude Code\b", re.IGNORECASE), "references 'Claude Code' (assistant-specific)"),
    (re.compile(r"\bmcp\b|\bmcp[-_]server\b", re.IGNORECASE), "references MCP servers (integration-mechanism-specific)"),
    (re.compile(r"\bCursor\b"), "references 'Cursor' (assistant-specific)"),
    (re.compile(r"\bCopilot\b", re.IGNORECASE), "references 'Copilot' (assistant-specific)"),
    (re.compile(r"\bCodex\b", re.IGNORECASE), "references 'Codex' (assistant-specific)"),
]

# `/skill-name` in backticks: a slash-command invocation reference.
# Requires the whole backticked token to be /<kebab-name> with no further
# slashes or dots, so real paths (`~/.snowflake/...`, `/usr/bin/git`) and
# CLI flags (`--profile`) never match.
SLASH_COMMAND_RE = re.compile(r"`/[a-z][a-z0-9]*(?:-[a-z0-9]+)+`")

# `scripts/...` or `./scripts/...` at the START of a backticked token --
# a helper-script reference missing {{SKILL_DIR}}. Full repo paths
# (`.claude/skills/...`) and templated paths (`{{SKILL_DIR}}/scripts/...`)
# don't match because the token doesn't start with (./)scripts/.
BARE_SCRIPT_PATH_RE = re.compile(r"`(?:\./)?scripts/[^`]+`")

DRIVE_LETTER_RE = re.compile(r"\b[A-Za-z]:\\")
PLACEHOLDER_MARKERS = ("path\\to", "path/to", "<")


def split_body(text):
    """Return (body_start_line, body_text): everything after the closing
    frontmatter delimiter. Line numbers are 1-based for report output."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 2, "\n".join(lines[i + 1:])
    return 1, text


def lint_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    start_line, body = split_body(text)

    violations = []
    for offset, line in enumerate(body.splitlines()):
        lineno = start_line + offset
        for regex, why in VENDOR_RES:
            if regex.search(line):
                violations.append((lineno, why, line.strip()[:120]))
        if SLASH_COMMAND_RE.search(line):
            violations.append((lineno, "slash-command invocation reference (runtime-specific invocation mechanism)", line.strip()[:120]))
        for m in BARE_SCRIPT_PATH_RE.finditer(line):
            violations.append((lineno, f"bare script path {m.group(0)} -- use {{{{SKILL_DIR}}}}/scripts/... instead", line.strip()[:120]))
        if DRIVE_LETTER_RE.search(line) and not any(p in line for p in PLACEHOLDER_MARKERS):
            violations.append((lineno, "hardcoded drive-letter path (no placeholder marker)", line.strip()[:120]))
    return violations


def main():
    skill_files = sorted(glob.glob(".claude/skills/*/SKILL.md"))
    if not skill_files:
        print("No SKILL.md files found under .claude/skills/ -- nothing to lint.")
        return 0

    failed = False
    for path in skill_files:
        violations = lint_file(path)
        if violations:
            failed = True
            print(f"FAIL: {path}")
            for lineno, why, snippet in violations:
                print(f"  line {lineno}: {why}")
                print(f"    > {snippet}")
        else:
            print(f"OK: {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
