# Loop Engineering Rules

Every skill declares a `loop-tier` in its `SKILL.md` frontmatter. See `Claude-Skills-Proposal-Expanded.md` Section 5 for the full model. Summary:

| Tier | Meaning | Mandatory limits |
|---|---|---|
| `on-demand` | Runs once, when invoked. Default for every new skill. | None beyond standard risk tagging. |
| `retry-until-resolved` | Re-attempts automatically against a fixed spec/condition. | Hard retry cap (default 3), each retry must change something. |
| `scheduled-notify-only` | Runs on a schedule/trigger, reports findings only. | Notification channel wired before going live; per-run budget; documented kill switch. |
| `pattern-gated-auto-remediation` | Retries + can take a pre-approved remediation action. | All of the above, plus an explicit action allowlist, a rate limit, and a full audit log. Requires sign-off beyond normal PR review. |
| `system-loop` | Improves the skill library itself (thresholds, fixtures, usage analytics). | Proposes changes as PRs only — never edits another skill directly. |

## Rules

1. **No skill starts above `on-demand`.** Promotion to a higher tier is always a separate, explicit PR — never bundled with the skill's initial authoring.
2. **Every loop iteration is logged** (timestamp, trigger, action, outcome) for anything above `on-demand` — an unattended loop must be auditable after the fact.
3. **No loop monitors itself.** A scheduled skill going silently quiet must be caught by something external (a heartbeat check), not by the skill's own logic.
4. **`pattern-gated-auto-remediation` skills must document their exact escalation boundary** — which patterns are pre-approved, and the literal fallback ("anything else → escalate, do not attempt").
