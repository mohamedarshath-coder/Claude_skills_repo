#!/usr/bin/env python3
"""
dependency-graph-impact-analyzer helper script.

Given a changed file, determines which skills in this repo are actually
affected -- so a PR reviewer knows the real blast radius of a change,
not just "well, it's in the repo somewhere."

Built specifically for this repo's structure (.claude/skills/<name>/,
.claude/rules/, tools/ci/, .github/workflows/) but the logic is generic
enough to apply to any repo following the same skill-folder convention.

Two-tier reasoning, since a pure text-reference graph misses conventions
that apply repo-wide without ever being named in a string literal:

  1. GLOBAL scope: shared rules (.claude/rules/**), CI scripts (tools/ci/**),
     the CI workflow (.github/workflows/**), or repo-root convention files
     (CLAUDE.md, .claude/settings.json) affect EVERY skill by definition --
     they're the authoring rules / validation pipeline every skill relies
     on, even though no skill's own files textually reference them by path.
  2. TEXT-REFERENCE scope: for a change inside one skill's own folder, scan
     every OTHER skill's files for literal string references to the
     changed file's path or basename -- catches genuine cross-skill
     dependencies (e.g. one skill's script calling another skill's script).

Usage:
    python impact_analyzer.py --changed-file <path-relative-to-repo-root> [--repo-path .]

No dependencies beyond the Python standard library.
"""
import argparse
import glob
import json
import os
import re

GLOBAL_PATH_PREFIXES = (".claude/rules/", "tools/ci/", ".github/workflows/")
GLOBAL_EXACT_FILES = ("CLAUDE.md", ".claude/settings.json")

# Candidate path-like strings inside text files -- filtered afterward by
# checking they actually exist as real repo-relative paths, to avoid
# false positives from unrelated quoted strings.
PATH_CANDIDATE_RE = re.compile(r"[\w][\w./-]*\.(?:py|md|json|ya?ml)")


def normalize(path):
    return path.replace("\\", "/")


def list_skill_names(repo_path):
    skill_dirs = glob.glob(os.path.join(repo_path, ".claude", "skills", "*"))
    return sorted(os.path.basename(d) for d in skill_dirs if os.path.isdir(d))


def is_global_path(rel_path):
    rel_path = normalize(rel_path)
    if rel_path in GLOBAL_EXACT_FILES:
        return True
    return any(rel_path.startswith(prefix) for prefix in GLOBAL_PATH_PREFIXES)


def owning_skill(rel_path, skill_names):
    rel_path = normalize(rel_path)
    prefix = ".claude/skills/"
    if not rel_path.startswith(prefix):
        return None
    remainder = rel_path[len(prefix):]
    skill_name = remainder.split("/", 1)[0]
    return skill_name if skill_name in skill_names else None


def find_text_references(repo_path, changed_rel_path, skill_names, owning_skill_name):
    """Scan every OTHER skill's files for a literal reference to the
    changed file's basename or relative path -- a genuine cross-skill
    dependency, not just co-location in the same repo."""
    changed_basename = os.path.basename(changed_rel_path)
    referencing_skills = set()

    for skill in skill_names:
        if skill == owning_skill_name:
            continue
        skill_dir = os.path.join(repo_path, ".claude", "skills", skill)
        for root, _dirs, files in os.walk(skill_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except (IsADirectoryError, PermissionError):
                    continue
                if changed_basename in text or normalize(changed_rel_path) in text:
                    referencing_skills.add(skill)
                    break
    return sorted(referencing_skills)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-file", required=True, help="Path relative to repo root")
    parser.add_argument("--repo-path", default=".")
    args = parser.parse_args()

    repo_path = args.repo_path
    changed_rel = normalize(args.changed_file)
    skill_names = list_skill_names(repo_path)

    impacted = []
    warning = None

    if is_global_path(changed_rel):
        scope = "global"
        for skill in skill_names:
            impacted.append({
                "skill": skill,
                "reason": f"'{changed_rel}' is a shared convention/CI file -- every skill's authoring rules or validation pipeline depends on it",
            })
    else:
        owner = owning_skill(changed_rel, skill_names)
        if owner:
            scope = "single-skill"
            impacted.append({"skill": owner, "reason": f"'{changed_rel}' belongs to this skill"})
            cross_refs = find_text_references(repo_path, changed_rel, skill_names, owner)
            if cross_refs:
                scope = "cross-skill"
                for s in cross_refs:
                    impacted.append({"skill": s, "reason": f"references '{os.path.basename(changed_rel)}' or its path directly"})
        else:
            scope = "none"
            # A path shaped like .claude/skills/<name>/... whose <name> isn't a
            # real skill folder is far more likely a typo (or a renamed/deleted
            # skill) than a deliberately unrelated file -- a bare "none" would
            # read as reassuring when it should read as suspicious.
            if changed_rel.startswith(".claude/skills/"):
                claimed = changed_rel[len(".claude/skills/"):].split("/", 1)[0]
                warning = (
                    f"path looks like a skill file, but no skill named '{claimed}' exists on disk -- "
                    f"check for a typo or a renamed/deleted skill before trusting this zero-impact result"
                )

    output = {
        "changed_file": changed_rel,
        "scope": scope,
        "impacted_skill_count": len(impacted),
        "impacted_skills": impacted,
    }
    if warning:
        output["warning"] = warning
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
