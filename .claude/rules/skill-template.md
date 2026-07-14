# Skill Authoring Template

Every skill lives at `.claude/skills/<skill-name>/SKILL.md`, using the tool-prefix-verb naming convention (e.g. `snowflake-cost-audit`, `airflow-dag-health`).

## Required frontmatter

```yaml
---
name: <skill-name>
description: <one-line summary of what it does and when Claude should use it>
risk: read-only | generates-code | write-confirm
loop-tier: on-demand | retry-until-resolved | scheduled-notify-only | pattern-gated-auto-remediation | system-loop
---
```

## Required sections in the body

1. **Purpose** — what problem this solves, in one paragraph.
2. **When to use** — the triggering situation (e.g. "when asked about warehouse costs, credit usage, or cost anomalies").
3. **Steps** — the actual workflow: which connection profile/API to use, what to query, what thresholds mean a problem, how to format the output. Vendor-neutral (see `portability.md`).
4. **Helper scripts** — reference via `{{SKILL_DIR}}/scripts/<name>`, never a hardcoded path.
5. **Log pre-filtering** (mandatory if the skill touches raw logs) — the helper script must strip noise before the payload reaches the agent, and cap the payload with a degradation rule (last N lines + suppressed-line count).
6. **Output format** — what the report/answer should look like.

## Do not

- Reference Claude-specific tools, MCP servers, or vendor-specific slash commands in the body (see `portability.md`).
- Hardcode credentials, connection strings, or client names.
- Default to any loop tier above `on-demand` — promotion is a separate PR (see `loop-engineering.md`).
