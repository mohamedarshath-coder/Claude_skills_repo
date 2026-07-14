# Project Proposal: Claude Skills Automation Library

**Prepared by:** Mohamed Arshath
**Date:** 07 July 2026
**Status:** Draft for review

---

## 1. Executive Summary

We propose building a **central repository of Claude Code Skills** — reusable, version-controlled automation packages that let any team member automate routine data-engineering workflows across our stack (Snowflake, Databricks, Airflow, AWS, and others) using natural-language commands.

A "skill" packages a workflow's knowledge and steps (as Markdown instructions + helper scripts) so that tasks which today take 20–40 minutes of manual tab-hopping — triaging a failed DAG, auditing warehouse costs, diagnosing a Glue job failure — become a single command such as `/airflow-dag-health` that anyone on the team can run consistently.

**Key value proposition:**
- One person builds a skill once → the entire team benefits immediately (clone/pull to use)
- Standardizes how routine ops work is done (same checks, same quality, every time)
- No credentials are ever stored in the repo — every skill runs under each user's own existing access (IAM, Snowflake roles, Databricks profiles), so existing access controls are fully preserved

---

## 2. Objectives

1. **Reduce manual effort** on repetitive data-ops tasks (monitoring, triage, audits, reporting) by an estimated 30–50% for participating engineers.
2. **Standardize operational workflows** — codify tribal knowledge and runbooks into executable, PR-reviewable skills.
3. **Accelerate incident resolution** — cross-tool triage (Airflow → Databricks → Snowflake) in one command instead of manual log-chasing across UIs.
4. **Enable team-wide reuse** — a shared library any engineer can install in minutes and contribute to via normal Git workflow.
5. **Extend delivery beyond Claude Code** — Snowflake and Databricks now ship their own native Agent Skills standards (Cortex Agents/CoCo, Databricks Genie Code) using the same `SKILL.md` format. Skills built here can be adapted to run natively on a client's platform even if they haven't adopted an AI coding assistant — see Section 5, Cross-Cutting/Native Ecosystem note and the companion Master Plan (Track 5).

---

## 3. What Is a Claude Skill? (Brief)

A skill is a folder containing:
- **`SKILL.md`** — instructions, conventions, and domain knowledge Claude follows when the skill is invoked (e.g., which API endpoints to call, what thresholds indicate a problem, how to format the report)
- **Optional helper scripts** — deterministic Python/PowerShell scripts for API calls, parsing, etc.

Skills are plain text files: reviewable in pull requests, versioned in Git, and distributed by simply cloning the repo. Team members install them once and invoke them from any project with `/skill-name`.

---

## 4. Phased Plan

### Phase 1 — Foundation (Week 1–2)
- Create the Git repository with the agreed structure, naming conventions, and contribution guide
- Write the skill template and documentation standards (so future skills are consistent)
- Build install scripts (`install.ps1` / `install.sh`) for one-command team setup
- Define the security guardrails (see Section 7) and engineering safeguards (see Section 7.5): log pre-filter requirement, version-drift check, and the PR validation pipeline (`test-fixtures/` + CI checks)

**Deliverable:** Working repo skeleton + docs + install workflow.

### Phase 2 — Pilot Skills (Week 2–4)
Build 2–3 read-only, high-value skills end-to-end as reference implementations:

| Pilot skill | Why first |
|---|---|
| `airflow-dag-health` | Daily pain point; read-only; immediately demonstrable value |
| `snowflake-cost-audit` | High visibility with leadership; read-only; clear cost savings |
| `databricks-job-triage` | Frequent manual task; showcases log-analysis strength |

**Deliverable:** 2–3 working skills, demoed to the team, used daily by pilot users.

### Phase 3 — Team Rollout & Expansion (Week 4–8)
- Onboard the wider team (install session, usage guide)
- Gather feedback from pilot usage; refine templates
- Expand the library per the roadmap in Section 5, prioritized by team votes
- Establish the contribution workflow (any engineer can PR a new skill)

**Deliverable:** 8–12 skills in production use; contribution process running.

### Phase 4 — Advanced / Cross-Tool Skills (Week 8+)
- Cross-platform incident triage (Airflow → Databricks → Snowflake in one command)
- Scheduled reporting (daily data-ops health summary)
- Jira integration (auto-draft incident tickets with root-cause analysis)

---

## 5. Skills Roadmap

### Snowflake
| Skill | Purpose | Risk level |
|---|---|---|
| `snowflake-cost-audit` | Warehouse credit usage, idle/oversized warehouses, top expensive queries | Read-only |
| `snowflake-query` | Safe ad-hoc SQL execution with auto-limits and formatted results | Read-only |
| `snowflake-schema-explorer` | Table descriptions, column profiling, data dictionary generation | Read-only |
| `snowflake-query-optimizer` | Analyze slow queries via query profile; suggest fixes | Read-only |
| `snowflake-data-quality` | Null/duplicate/freshness checks with anomaly report | Read-only |
| `snowflake-access-review` | Audit roles and grants for compliance | Read-only |

### Databricks
| Skill | Purpose | Risk level |
|---|---|---|
| `databricks-job-triage` | Diagnose failed jobs from run logs; suggest fixes | Read-only |
| `databricks-cluster-audit` | Idle/oversized clusters, cost hotspots | Read-only |
| `databricks-notebook-review` | Lint notebooks for anti-patterns | Read-only |
| `databricks-job-deploy` | Create/update jobs from validated specs | Write (confirmation required) |

### Airflow
| Skill | Purpose | Risk level |
|---|---|---|
| `airflow-dag-health` | Environment-wide failed/stuck DAG summary with root causes | Read-only |
| `airflow-failed-task-triage` | Pull task logs, identify error, suggest fix | Read-only |
| `airflow-dag-review` | Lint DAG code: retries, idempotency, catchup, top-level code | Read-only |
| `airflow-backfill` | Plan and execute validated backfills | Write (confirmation required) |
| `airflow-dag-generator` | Scaffold new DAGs from team conventions | Generates code only |

### AWS
| Skill | Purpose | Risk level |
|---|---|---|
| `aws-cost-report` | Cost Explorer summary with month-over-month deltas and anomalies | Read-only |
| `aws-s3-audit` | Public access, encryption, lifecycle policy review | Read-only |
| `aws-glue-triage` | Failed Glue job diagnosis from CloudWatch logs | Read-only |
| `aws-iam-review` | Overly-permissive policies, unused roles/keys | Read-only |
| `aws-resource-cleanup-report` | Orphaned volumes/snapshots/IPs — report only, human acts | Read-only |

### Power BI
| Skill | Purpose | Risk level |
|---|---|---|
| `powerbi-refresh-triage` | Diagnose failed dataset refreshes (credentials, gateway, timeouts) | Read-only |
| `powerbi-workspace-audit` | Unused reports, stale datasets, orphaned content inventory | Read-only |
| `powerbi-lineage-trace` | Impact analysis: which reports depend on a given source table | Read-only |
| `powerbi-dax-review` | Review DAX measures for performance anti-patterns | Read-only |
| `powerbi-access-review` | Workspace/report sharing and RLS audit | Read-only |

### Tableau
| Skill | Purpose | Risk level |
|---|---|---|
| `tableau-extract-triage` | Diagnose failed extract refreshes | Read-only |
| `tableau-content-audit` | Stale workbooks and unused data sources via usage stats | Read-only |
| `tableau-workbook-review` | Workbook performance review (filters, calcs, extract design) | Read-only |
| `tableau-lineage-trace` | Which dashboards depend on a given database table (Metadata API) | Read-only |

### Cross-Cutting (Phase 4)
| Skill | Purpose |
|---|---|
| `pipeline-incident-triage` | End-to-end failure trace across Airflow → Databricks → Snowflake |
| `freshness-trace` | "Dashboard shows stale data" → trace BI refresh → Snowflake source → Airflow DAG to pinpoint the break |
| `daily-data-ops-report` | One morning summary: DAG health + job failures + cost anomalies + freshness |
| `jira-ticket-from-incident` | Auto-draft Jira tickets with logs and root-cause after triage |
| `runbook-executor` | Convert existing ops runbooks into executable skills |

---

## 6. Requirements

### 6.1 Access & Licensing
| Item | Details | Owner/Action |
|---|---|---|
| Claude Code licenses | Seats for pilot users (existing org plan covers this if already provisioned) | Manager / IT |
| Git repository | New repo on our Git platform (GitHub/GitLab/Bitbucket) with team access | Me + repo admin |
| Snowflake access | Each user's existing role; a monitoring role with `ACCOUNT_USAGE` read access needed for cost-audit skills | Snowflake admin |
| Databricks access | Personal access tokens or existing SSO profiles per user; workspace read access to Jobs/Clusters APIs | Databricks admin |
| Airflow access | REST API enabled + read credentials per user (or a read-only service role) | Platform team |
| AWS access | Existing SSO profiles; read access to Cost Explorer, CloudWatch, S3 metadata, IAM (read-only) | AWS admin |
| Power BI access | Power BI REST API access via each user's Azure AD account; admin-read API for tenant-wide audits (optional) | Power BI / Azure admin |
| Tableau access | Tableau Server/Cloud REST + Metadata API; personal access tokens per user | Tableau admin |
| Snowflake Cortex Agents Skills (native) | Ability to create/write to a Snowflake named stage (or a linked Git repo) to host skills for Snowflake's in-platform Cortex Agents; SQL/REST/Snowsight access to attach skills to an agent | Snowflake admin |
| Databricks Agent Skills (native) | `databricks aitools` CLI access (bundled with Databricks CLI) to install/publish skills for Databricks' native Genie Code agent | Databricks admin |

### 6.2 Tooling (per user machine)
- Claude Code CLI (already in use)
- CLI clients: Snowflake CLI/`snowsql`, `databricks` CLI, `aws` CLI v2
- Python 3.10+ for helper scripts
- Git

### 6.3 Effort Estimate
| Phase | Effort |
|---|---|
| Phase 1 (foundation) | ~3–4 days |
| Phase 2 (pilot skills) | ~1.5–2 weeks (part-time alongside project work) |
| Phase 3 (rollout) | ~2 weeks elapsed; ~2–3 days effort + team onboarding session |
| Ongoing | Skills added incrementally by the team via PRs |

---

## 7. Security & Governance

Aligned with our organization's Claude usage policies:

1. **No credentials in the repository** — skills reference the user's own environment (AWS SSO, Snowflake config, Databricks profiles). `.gitignore` blocks `.env` and config files; no API keys, secrets, or connection strings are ever committed.
2. **No client data or PII in skills or outputs** — skills operate on metadata (logs, configs, usage stats) by default; any data-touching skill (e.g., data-quality checks) reports aggregates only.
3. **Read-only by default** — Phases 1–3 are almost entirely read-only. Any write-capable skill (backfill, job deploy) requires explicit user confirmation before acting and is clearly labeled.
4. **Existing access controls preserved** — a skill can only do what the invoking user's credentials allow; no shared/elevated service accounts for interactive use.
5. **PR review for all skills** — every skill is code-reviewed before merge, like any production code.
6. **Private repository** — internal access only.

---

## 7.5 Engineering Safeguards

Raised during technical review — three real risks that only surface after skills are in daily use, so they're addressed now rather than retrofitted later.

### a) Log pre-filtering (mandatory, not optional)
Skills that ingest raw logs (`databricks-job-triage`, `aws-glue-triage`, `airflow-failed-task-triage`) risk blowing past context limits or drowning the model in boilerplate (repeated GC logs, verbose retry noise) before it ever reaches the actual error.

- **Rule:** any skill that touches logs must ship a helper script that pre-filters *before* the payload reaches Claude — strip repetitive noise, isolate the actual exception/failure block via regex or structural parsing.
- **Degradation rule:** even after filtering, cap the payload (e.g., last N lines of the real failure + a count of suppressed noise lines) so a worse-than-expected log truncates gracefully instead of silently.
- This is a mandatory field in the skill template, not left to each author's discretion.

### b) Version drift protection
Because skills are cloned/pulled locally, an engineer can end up running a stale `/snowflake-cost-audit` with an outdated threshold or deprecated schema reference — producing confident-but-wrong output.

- **Approach:** the install process writes a version stamp; each skill checks that stamp against the repo's latest tag/commit on invocation and prints a one-line staleness warning if behind.
- Deliberately lightweight — no custom wrapper binaries or background processes (higher maintenance surface, more places to break across OS/shell differences) — just a check baked into the skill's own instructions.

### c) CI/CD validation for skills (not just for clients)
A `SKILL.md` is prose, not code — a "small tweak" to fix one edge case can silently regress a different path the same skill handles, and nobody reviews prose diffs the way they review logic diffs.

**Pipeline run on every PR to the skills repo:**

| Check | What it catches |
|---|---|
| `structure-check` | Every changed skill folder has a valid `SKILL.md` with required frontmatter (name, description, risk tag) |
| `secrets-scan` | Gitleaks/TruffleHog over the diff — hard fail on anything resembling a key/token/connection string |
| `fixture-test` | Replays the modified skill against `test-fixtures/<skill-name>/` (anonymized historical logs/reports) and asserts on **structure**, not exact wording — did it cite evidence, identify the right root-cause category, stay read-only where required |
| `lint-conventions` | Naming convention (tool-prefix-verb), risk tag present, no hardcoded client names/URLs |

Any failing check blocks merge. Fixture assertions deliberately check structural properties, not literal output text — asserting exact wording would fail on harmless phrasing changes and the team would start ignoring the check.

**Dogfooding note:** since `ci-failure-triage`, `dependency-audit`, and `secrets-scan` are already planned as client-facing skills (Section 5, DevOps track — see Master Plan), the same skills run against our own repo's pipeline. Useful as a demo point: the skill diagnosing its own repo's CI failures.

---

## 8. Success Metrics

| Metric | Target (first quarter) |
|---|---|
| Skills in production use | 10+ |
| Active users | Full pilot team, expanding to wider group |
| Time saved on routine triage/audit tasks | 30–50% reduction (measured via before/after task timing on 3 benchmark workflows) |
| Mean time to diagnose pipeline failures | Reduced from ~30 min to <10 min for covered failure types |
| Team contributions | ≥3 skills authored by engineers other than me |

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Incorrect diagnosis/suggestion by a skill | Skills report findings with evidence (logs, query IDs); human validates before acting; read-only default |
| Credential misuse or leakage | No secrets in repo; per-user auth only; org security settings enforced |
| API access not granted in time | Requirements list in Section 6.1 raised in week 1; pilot skills chosen to need minimal new access |
| Low adoption | Start with the most painful daily tasks; one-command install; demo sessions |
| Skill sprawl / inconsistency | Template + conventions doc + mandatory PR review |

---

## 10. Immediate Next Steps (Pending Approval)

1. Approve pilot scope (Phases 1–2) and confirm pilot user group
2. Request the access items in Section 6.1 (Snowflake monitoring role, Airflow API access)
3. Create the repository and begin Phase 1

---

*Questions or scope adjustments welcome — the roadmap in Section 5 is a menu, not a commitment; we'll prioritize based on team input.*
