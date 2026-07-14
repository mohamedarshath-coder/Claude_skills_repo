# Agent Skills Automation Library

Shared, version-controlled repository of Agent Skills (`SKILL.md` + helper scripts) that automate repetitive data-ops work across our stack. See `Claude-Skills-Proposal-Expanded.md` and `Claude-Skills-Master-Plan.md` for the full plan.

## Ground rules (non-negotiable — see `.claude/rules/`)

- **No credentials or client data/PII ever committed to this repo.** Every skill runs under the invoking user's own existing access (their own Snowflake role, Databricks profile, Airflow token, etc.) via named connection profiles configured locally, never hardcoded.
- **Read-only by default.** Any write-capable skill requires explicit user confirmation before acting and is clearly labeled in its `SKILL.md` risk tag.
- **AI-agnostic authoring.** Every `SKILL.md` body is vendor-neutral — no Claude-specific tool names, no hardcoded paths (use `{{SKILL_DIR}}`), no MCP/vendor-specific integration mechanisms. See `.claude/rules/portability.md`.
- **Loop tier declared per skill.** Every skill declares a `loop-tier` in its frontmatter (`on-demand`, `retry-until-resolved`, `scheduled-notify-only`, `pattern-gated-auto-remediation`, `system-loop`). No skill starts above `on-demand`; promotion to a higher tier is a separate, explicit PR. See `.claude/rules/loop-engineering.md`.
- **PR review required** for every skill before merge.

## Repo structure

```
your-project/
├── CLAUDE.md                 # this file — team instructions, committed
├── CLAUDE.local.md            # personal overrides, gitignored (not yet created)
├── .claude/
│   ├── settings.json          # permissions + config, committed
│   ├── settings.local.json    # personal permissions, gitignored (not yet created)
│   ├── commands/               # custom slash commands
│   ├── rules/                  # modular instruction files (this repo's conventions)
│   ├── skills/                 # one folder per skill — SKILL.md + scripts/
│   └── agents/                  # subagent personas (for multi-skill orchestration, later)
└── tools/
    └── export-skill            # (planned) converts a canonical SKILL.md to Cursor/CoCo/GPT formats
```

## Skill authoring

Before writing a new skill, read `.claude/rules/skill-template.md` — it defines the required `SKILL.md` frontmatter (name, description, risk tag, loop tier) and the mandatory sections (log pre-filtering for any log-touching skill, portability rules, version-stamp check).
