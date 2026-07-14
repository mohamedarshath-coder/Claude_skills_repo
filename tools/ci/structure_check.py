#!/usr/bin/env python3
"""
structure-check CI job.

Every skill folder under .claude/skills/<name>/ must have a SKILL.md with
valid frontmatter: name, description, risk, loop-tier -- per
.claude/rules/skill-template.md. This is a mechanical check (frontmatter
parses, required keys present, values are from the allowed set) -- it does
not judge content quality, only structure.

Usage: python tools/ci/structure_check.py
Exit code 0 = all skills pass, 1 = at least one failure (fails the PR).
"""
import glob
import sys

REQUIRED_KEYS = {"name", "description", "risk", "loop-tier"}
ALLOWED_RISK = {"read-only", "generates-code", "write-confirm"}
ALLOWED_LOOP_TIER = {
    "on-demand",
    "retry-until-resolved",
    "scheduled-notify-only",
    "pattern-gated-auto-remediation",
    "system-loop",
}


def parse_frontmatter(text):
    """Minimal YAML-frontmatter parser -- good enough for flat key: value
    pairs between the two '---' markers, without a YAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fm
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return None  # never found closing ---


def check_skill(skill_md_path):
    errors = []
    with open(skill_md_path, "r", encoding="utf-8") as f:
        text = f.read()

    fm = parse_frontmatter(text)
    if fm is None:
        errors.append("missing or malformed frontmatter (must start and end with '---')")
        return errors

    missing = REQUIRED_KEYS - fm.keys()
    if missing:
        errors.append(f"missing required frontmatter keys: {sorted(missing)}")

    if "risk" in fm and fm["risk"] not in ALLOWED_RISK:
        errors.append(f"risk '{fm['risk']}' not in allowed set {sorted(ALLOWED_RISK)}")

    if "loop-tier" in fm and fm["loop-tier"] not in ALLOWED_LOOP_TIER:
        errors.append(f"loop-tier '{fm['loop-tier']}' not in allowed set {sorted(ALLOWED_LOOP_TIER)}")

    return errors


def main():
    skill_files = sorted(glob.glob(".claude/skills/*/SKILL.md"))
    if not skill_files:
        print("No skills found under .claude/skills/ -- nothing to check.")
        return 0

    failed = False
    for path in skill_files:
        errors = check_skill(path)
        if errors:
            failed = True
            print(f"FAIL: {path}")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"OK: {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
