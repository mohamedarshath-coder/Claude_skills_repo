# Portability Rules (AI-Agnostic Authoring)

`SKILL.md` is an open Agent Skills standard, not Claude-proprietary. Every skill in this repo must be authored so the same file (or a thin generated export of it) runs correctly on Claude Code, Cursor, Snowflake Cortex Agents/CoCo, Databricks Genie Code, GitHub Copilot, and OpenAI Codex/GPT tooling.

## Rules

1. **Vendor-neutral core.** The body of `SKILL.md` describes the workflow (endpoints, thresholds, checks, output format) — never a specific assistant's internals. No references to Claude-only tools, slash commands, or behaviors.
2. **Helper scripts are plain Python/PowerShell/shell** — deterministic, agent-independent, runnable by any assistant that can execute a script, or by a human directly.
3. **Path abstraction, not hardcoded paths.** Reference helper scripts as `{{SKILL_DIR}}/scripts/my-script.py`, never `./scripts/my-script.py`. `tools/export-skill/export_skill.py` substitutes the correct path per target runtime (`--skill-dir-value` overrides the default repo-relative path for runtimes hosting the skill elsewhere, e.g. a Snowflake stage).

   Exports: `python tools/export-skill/export_skill.py --all --target cursor|agents-md|native` writes generated files under `exports/` (gitignored — regenerate on demand, never hand-edit or commit).
4. **Native data access, not hardcoded integration mechanisms.** Never write "use the `X-mcp-server`" or reference a specific plugin/vendor command. Describe *what* to query ("query the warehouse credit usage view") and let the runtime's own connector/governance layer handle *how*.
5. **No hardcoded credentials or connection strings — ever.** Skills reference a named connection profile (e.g. Snowflake `connections.toml` read via `snowflake-connector-python`'s `connection_name=`, or a Databricks CLI/`databricks-sdk` profile) that each user configures locally. The skill never knows or cares which specific auth method backs that profile.

## CI enforcement

`tools/ci/portability_lint.py` (the `portability-lint` job in `.github/workflows/skills-ci.yml`) scans every `SKILL.md` body — frontmatter excluded — and fails the PR on: assistant/vendor references (Claude Code, Cursor, Copilot, Codex, MCP servers), slash-command invocation references (`` `/skill-name` `` style), bare helper-script paths missing `{{SKILL_DIR}}`, and hardcoded drive-letter paths (placeholder-marked setup examples like `D:\path\to\...` are allowed). Credential scanning is deliberately left to `secrets_scan.py` — one source of truth, repo-wide.

Proven against real violations, not just clean files: on its first run it caught two genuine issues in `snowflake-cost-audit`'s SKILL.md (a slash-command invocation reference and a bare `scripts/scheduled_run.py` path), both fixed in the same PR that added the linter.
