# Claude Skills Automation Library — Master Plan

**Prepared by:** Mohamed Arshath
**Date:** 07 July 2026
**Companion to:** Claude-Skills-Proposal.md (project proposal & governance)

---

## Vision

One shared, version-controlled repository of Claude Code Skills that automates repetitive work across the entire engineering organization — data platform operations, DevOps, data engineering practices, and client delivery. Any engineer clones the repo, installs in one command, and gets `/skill-name` automations that run under their own credentials.

**Guiding rules:** read-only by default · no credentials or client data/PII in the repo · every skill PR-reviewed · anything that writes requires explicit confirmation.

---

## The Four Tracks

| # | Track | What it covers | When |
|---|---|---|---|
| 1 | **Data Platform Ops** | Snowflake, Databricks, Airflow, AWS, Power BI, Tableau | Phase 1–2 (start now) |
| 2 | **DevOps & Platform** | CI/CD, Terraform, Docker/K8s, monitoring, security | Phase 3 |
| 3 | **Data Engineering Practices** | Spark tuning, SQL review, migrations, data quality, synthetic data | Phase 3 |
| 4 | **SDLC & Client Delivery** | PR review, testing, docs, analytics deliverables | Phase 4 |
| 5 | **Platform-Native Skill Ecosystems** | Interop with Snowflake Cortex/CoCo Skills and Databricks Agent Skills | Cuts across all phases |

---

# Track 5 — Platform-Native Skill Ecosystems

Both Snowflake and Databricks have recently published their own **Agent Skills** standards — and the important part is that they use the same `SKILL.md` package format we're already building on for Claude Code. This means skills are not a Claude-only asset; they are a **portable, platform-agnostic automation layer**. Raised by leadership after a client call — highest-priority track to formalize since it directly affects our client story.

## Snowflake Cortex Agents Skills
*[docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-skills](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-skills)*

- Skills run natively **inside Snowflake's own Cortex Agents** (Snowflake's in-platform AI) — no external AI assistant required.
- Same `SKILL.md` structure: name, description, instructions, optional scripts.
- Stored in a Snowflake named stage or a Git repo (pinned to a commit or an auto-updating tag).
- Attached to an agent via SQL, REST API, or Snowsight UI; the agent auto-discovers and invokes relevant skills based on the user's query.
- Automatically available inside **Snowflake CoWork**.

## Snowflake coco-skills (Cortex Code)
*[github.com/Snowflake-Labs/coco-skills](https://github.com/Snowflake-Labs/coco-skills)*

- A curated, open community repo of skills for **Cortex Code (CoCo)**, Snowflake's AI-powered CLI dev tool.
- Existing skill categories: documentation/quickstart search, semantic-view & ontology modeling, Snowpipe/PrivateLink/SAP integration, RBAC, MLOps pipelines, data clean rooms.
- Invoked in CoCo with `$<skill-name>` + a prompt.
- Contribution model is open (Apache 2.0 or Snowflake license) — Snowflake employees, partners, and community can submit skills via a standardized folder structure.

## Databricks Agent Skills
*[docs.databricks.com/aws/en/agent-skills](https://docs.databricks.com/aws/en/agent-skills/)*

- Implements the **open Agent Skills standard** — the same Markdown+metadata format usable by Claude, GitHub Copilot, and Databricks' own built-in coding agent (**Genie Code**).
- Installed via the Databricks CLI's `databricks aitools` command group (auto-detects the coding agent in use) or a third-party Skills CLI package manager.
- Covers compute, orchestration, storage, and Databricks Apps development patterns.

## Strategic implications for our project

| Implication | What it means for us |
|---|---|
| **Reuse, don't rebuild** | Before writing a Snowflake or Databricks skill from scratch, check coco-skills / Databricks Agent Skills for an existing one we can adopt or fork — accelerates Track 1 |
| **Client delivery without Claude** | For clients who haven't adopted an AI coding assistant, the *same skill content* can run natively via Snowflake Cortex Agents/CoCo or Databricks Genie Code — removes "you need Claude" as an adoption blocker |
| **One skill, multiple runtimes** | Because all three ecosystems use the `SKILL.md` format, a well-written skill can largely be dual/triple-purposed (Claude Code + Cortex Agents + Databricks Genie) with minor adaptation |
| **Contribution & visibility** | Generic, non-client-specific skills we build could be contributed back to `coco-skills` or the Databricks Skills ecosystem — positions us as an active partner in the open Agent Skills space |
| **Governance stays the same** | Cortex Agents skills stored in a Snowflake stage/Git and Databricks skills installed via CLI still follow our rule: no credentials/PII in the skill package itself |

**Action items:**
1. Audit `coco-skills` and Databricks Agent Skills repos against our Track 1 roadmap (Section: Data Platform Ops) to identify overlap before building duplicates
2. Pilot one skill (e.g. `snowflake-cost-audit`) as a dual-format skill: works in Claude Code *and* as a Cortex Agents skill in a Snowflake stage
3. Add "publish to native ecosystem" as an optional final step in our skill-authoring template, where applicable
4. Flag this track explicitly in client conversations — "works with or without an AI coding assistant" is a differentiator

---

# Track 1 — Data Platform Ops

## Snowflake
*Access: Snowflake CLI / Python connector, per-user roles; `ACCOUNT_USAGE` read for audits*

| Skill | Use case | Risk |
|---|---|---|
| `snowflake-cost-audit` | Warehouse credit usage, idle/oversized warehouses, top expensive queries, MoM trends | Read-only |
| `snowflake-query` | Safe ad-hoc SQL: auto-LIMIT, formatted results, plain-English explanation | Read-only |
| `snowflake-schema-explorer` | Describe tables, profile columns, generate data dictionaries | Read-only |
| `snowflake-query-optimizer` | Pull query profile of a slow query → clustering/rewrite recommendations | Read-only |
| `snowflake-data-quality` | Null/duplicate/freshness checks on a table with anomaly report | Read-only |
| `snowflake-access-review` | Roles, grants, who-can-see-what audit for compliance | Read-only |
| `snowflake-ddl-diff` | Compare dev/qa/prod schemas → generate migration DDL | Generates code |

## Databricks
*Access: `databricks` CLI / REST API, per-user tokens or SSO profiles*

| Skill | Use case | Risk |
|---|---|---|
| `databricks-job-triage` | Failed job → run logs → root cause → suggested fix | Read-only |
| `databricks-cluster-audit` | Idle/long-running/oversized clusters, cost hotspots | Read-only |
| `databricks-notebook-review` | Lint notebooks: collect() abuse, no partitioning, hardcoded paths | Read-only |
| `databricks-dlt-monitor` | Delta Live Tables pipeline health + expectation results | Read-only |
| `databricks-unity-catalog-audit` | UC permissions, ownership, lineage review | Read-only |
| `databricks-job-deploy` | Create/update jobs from validated spec | Write — confirm |

## Airflow
*Access: REST API, per-user or read-only service credentials*

| Skill | Use case | Risk |
|---|---|---|
| `airflow-dag-health` | Environment-wide failed/stuck DAG summary with root causes — the "morning check" | Read-only |
| `airflow-failed-task-triage` | One failed task → logs → error identified → fix suggested | Read-only |
| `airflow-dag-review` | Lint DAG code: retries, idempotency, catchup, top-level code | Read-only |
| `airflow-sla-report` | Weekly SLA misses and slowest tasks | Read-only |
| `airflow-dag-generator` | Scaffold new DAGs from team template with alerts wired in | Generates code |
| `airflow-backfill` | Validated, dependency-aware backfill planning and execution | Write — confirm |

## AWS
*Access: `aws` CLI with per-user SSO profiles, read-only policies*

| Skill | Use case | Risk |
|---|---|---|
| `aws-cost-report` | Cost Explorer by service/tag, MoM deltas, anomaly flags | Read-only |
| `aws-s3-audit` | Public access, encryption, lifecycle, stale data inventory | Read-only |
| `aws-glue-triage` | Failed Glue job diagnosis from CloudWatch logs | Read-only |
| `aws-lambda-health` | Error rates, throttles, cold starts across functions | Read-only |
| `aws-iam-review` | Over-permissive policies, unused roles/keys | Read-only |
| `aws-resource-cleanup-report` | Orphaned EBS volumes, old snapshots, unattached IPs — report only | Read-only |

## Power BI
*Access: REST API via per-user Azure AD; admin-read API for tenant audits*

| Skill | Use case | Risk |
|---|---|---|
| `powerbi-refresh-triage` | Failed dataset refreshes → cause (credentials/gateway/timeout) | Read-only |
| `powerbi-workspace-audit` | Unused reports, stale datasets, orphaned content | Read-only |
| `powerbi-lineage-trace` | Which reports break if a source table changes | Read-only |
| `powerbi-dax-review` | DAX performance anti-patterns → optimizations | Read-only |
| `powerbi-access-review` | Workspace sharing + RLS audit | Read-only |
| `powerbi-gateway-health` | Gateway status and data source connectivity | Read-only |

## Tableau
*Access: REST + Metadata API, per-user personal access tokens*

| Skill | Use case | Risk |
|---|---|---|
| `tableau-extract-triage` | Failed extract refreshes: identify, diagnose, summarize | Read-only |
| `tableau-content-audit` | Stale workbooks, unused data sources, unviewed dashboards | Read-only |
| `tableau-workbook-review` | Performance review: filters, row-level calcs, extract design | Read-only |
| `tableau-permissions-audit` | Project/workbook permission review | Read-only |
| `tableau-lineage-trace` | Which dashboards depend on a given database table | Read-only |

---

# Track 2 — DevOps & Platform

## CI/CD (GitHub Actions / GitLab CI / Jenkins / Azure DevOps)

| Skill | Use case | Risk |
|---|---|---|
| `ci-failure-triage` | Failed pipeline → logs → failing step + root cause + fix. Highest-frequency DevOps pain | Read-only |
| `pipeline-generator` | Scaffold CI/CD pipelines from team standards (lint→test→scan→deploy) | Generates code |
| `release-notes` | Generate release notes from PRs/commits between two tags | Read-only |
| `deploy-checklist` | Pre-deploy verification: pending migrations, config diffs, rollback plan | Read-only |

## Infrastructure as Code (Terraform / CloudFormation)

| Skill | Use case | Risk |
|---|---|---|
| `terraform-plan-review` | Explain a plan in plain English; flag destructive changes loudly | Read-only |
| `terraform-security-scan` | Open security groups, unencrypted resources, missing tags | Read-only |
| `iac-drift-check` | Detect drift between deployed infra and code | Read-only |
| `terraform-module-generator` | Scaffold modules per team conventions | Generates code |

## Containers & Kubernetes (Docker / K8s / EKS)

| Skill | Use case | Risk |
|---|---|---|
| `k8s-pod-triage` | CrashLoopBackOff / OOMKilled / Pending → describe + logs → diagnosis | Read-only |
| `dockerfile-review` | Image size, layer caching, root user, secrets in layers | Read-only |
| `k8s-resource-audit` | Over/under-provisioned requests & limits, cost implications | Read-only |

## Monitoring & Incident Response (CloudWatch / Grafana / Datadog)

| Skill | Use case | Risk |
|---|---|---|
| `alert-triage` | Alert fires → pull metrics/logs → correlate → probable cause summary | Read-only |
| `log-analyzer` | "What changed in error patterns in the last 24h?" | Read-only |
| `incident-postmortem` | Draft postmortem from incident timeline, logs, ticket trail | Generates doc |

## Security & Compliance

| Skill | Use case | Risk |
|---|---|---|
| `dependency-audit` | CVE scan of requirements.txt / package.json with upgrade paths | Read-only |
| `secrets-scan` | Pre-commit check for accidentally staged credentials | Read-only |
| `ssl-cert-check` | Expiring certificate report | Read-only |

---

# Track 3 — Data Engineering Practices

| Skill | Use case | Risk |
|---|---|---|
| `spark-job-tuner` | Read Spark event logs/UI → diagnose skew, spills, shuffle issues → tuning recs | Read-only |
| `sql-review` | Dialect-aware SQL review: performance, correctness, style | Read-only |
| `pipeline-scaffolder` | New ingestion pipeline from template: source→staging→transform + tests + alerts | Generates code |
| `schema-drift-detector` | Source vs. target schema comparison → ALTER statements | Read-only |
| `data-contract-validator` | Validate datasets against agreed contracts before promotion | Read-only |
| `dq-rule-generator` | Profile a table → propose data-quality rules/tests | Read-only |
| `sample-data-generator` | Realistic synthetic test data for dev — removes any need for client data in dev | Generates data |
| `migration-assistant` | Translate SQL/jobs between platforms (SQL Server→Snowflake, Hive→Databricks) — high value for consulting projects | Generates code |

### dbt (if adopted — strongest single fit in this track)

| Skill | Use case | Risk |
|---|---|---|
| `dbt-test-triage` | Run tests, explain failures, trace to source data | Read-only |
| `dbt-model-generator` | Scaffold staging/mart models with naming conventions + tests | Generates code |
| `dbt-docs-writer` | Auto-generate model/column descriptions in schema.yml | Generates docs |
| `dbt-pr-review` | Missing tests, breaking changes, materialization choices | Read-only |

---

# Track 4 — SDLC & Client Delivery

## Software Development Lifecycle

| Skill | Use case | Risk |
|---|---|---|
| `pr-review-standards` | Code review tuned to our conventions, not generic linting | Read-only |
| `test-generator` | Unit tests for uncovered code paths | Generates code |
| `doc-sync` | Keep READMEs / Confluence in sync with code changes | Generates docs |
| `onboarding-buddy` | Interactive new-joiner walkthrough: setup, tooling, conventions | Read-only |
| `estimate-helper` | Break a Jira epic into tasks with effort estimates | Generates plan |

## Analytics & Client Delivery
*Strict rule: metadata and aggregates only — no client data or PII in any output*

| Skill | Use case | Risk |
|---|---|---|
| `eda-starter` | Standardized EDA on a new dataset: profiling, distributions, quality summary | Read-only |
| `insight-summarizer` | Query results → client-ready narrative summary (aggregates only) | Generates doc |
| `deck-outline-generator` | Structure findings into a presentation outline | Generates doc |
| `sow-requirement-tracer` | Map deliverables against project requirement docs | Read-only |

---

# Cross-Cutting Skills (span multiple tracks — highest value)

| Skill | Use case |
|---|---|
| `pipeline-incident-triage` | **The flagship.** Airflow DAG failed → task log → it's a Databricks job → its run error → it's a Snowflake grant issue → full chain reported with fix. Replaces ~30 min of tab-hopping with one command |
| `freshness-trace` | "Client dashboard is stale" → BI refresh status → Snowflake source → Airflow DAG → pinpoint the break |
| `daily-data-ops-report` | One morning summary: DAG health + failed jobs + cost anomalies + freshness (can run on a schedule) |
| `jira-ticket-from-incident` | After triage, auto-draft the Jira ticket with logs and root cause |
| `data-lineage-trace` | "Where does this column come from?" — target table back through transforms to source |
| `runbook-executor` | Convert existing Confluence runbooks into executable skills |

---

# Delivery Roadmap

| Phase | Weeks | Focus | Deliverables |
|---|---|---|---|
| **1 — Foundation** | 1–2 | Repo, conventions, template, install scripts, security guardrails | Working repo skeleton anyone can install from |
| **2 — Data Platform pilots** | 2–4 | `airflow-dag-health`, `snowflake-cost-audit`, `databricks-job-triage` | 3 skills in daily use by pilot group |
| **3 — Rollout + DevOps/DE tracks** | 4–8 | Team onboarding; first DevOps skills (`ci-failure-triage`, `terraform-plan-review`) and DE skills (`sql-review`, `spark-job-tuner`); expand Track 1 by team vote | 10–15 skills live; contribution workflow running |
| **4 — Cross-cutting + Delivery track** | 8+ | `pipeline-incident-triage`, `daily-data-ops-report`, Jira integration, client-delivery skills | Flagship cross-tool automations; org-wide adoption |

**Prioritization principle within every track:** triage & reporting skills first (daily pain, read-only, safe), generators second, write-capable skills last and always confirmation-gated.

---

# Track 6 — Scheduled / Autonomous Skills

Raised by leadership alongside the native-ecosystem track: skills shouldn't only run when someone types a command — the highest-value monitoring skills should run **on a schedule**, watch for problems continuously, and only surface output when something needs attention.

## What changes vs. on-demand skills
On-demand skills (everything in Tracks 1–4 as originally scoped) wait for a person to invoke them. A **scheduled skill** runs itself on a cron-style interval and pushes a result out — no one has to remember to ask.

| Mode | Trigger | Example |
|---|---|---|
| On-demand | Engineer types `/airflow-dag-health` | Runs once, reports back immediately |
| Scheduled | Runs automatically every N minutes/hours, or daily at a fixed time | Same skill, but it runs itself at 7 AM and posts the result — or stays silent if everything's healthy |

## Best candidates to run on a schedule
These are skills already in the catalog that get materially more valuable once they're autonomous rather than on-demand:

| Skill | Schedule | Why it fits |
|---|---|---|
| `daily-data-ops-report` | Once daily (e.g. 7 AM) | Designed from the start to be a morning summary |
| `airflow-dag-health` | Every 15–30 min during business hours | Catches failures minutes after they happen, not whenever someone checks |
| `snowflake-cost-audit` | Weekly | Cost anomalies are a trend signal, not a real-time one |
| `pipeline-incident-triage` | Triggered by an alert/webhook rather than a fixed interval | Event-driven, not time-driven — fires the moment a DAG/job fails |
| `ssl-cert-check`, `dependency-audit` | Weekly | Classic "don't forget to check this" hygiene tasks |

## What scheduling adds on top of the skill itself
1. **A trigger mechanism** — either time-based (cron/interval) or event-based (webhook/alert fires it)
2. **A delivery channel for output** — since no one is sitting there waiting for a response, results need to land somewhere: Slack/Teams message, email, or a Jira ticket auto-created for anything that needs action (ties directly into the `jira-ticket-from-incident` skill already in the catalog)
3. **A "stay quiet when healthy" rule** — a scheduled skill that pings the team every 15 minutes even when nothing's wrong trains people to ignore it. Default behavior: only notify on a finding, not on every run
4. **Retry/convergence logic** — for anything monitoring an in-progress failure, the loop should keep checking until the issue resolves or escalates, not just fire once and stop

## Where this fits the roadmap
This isn't a new phase so much as a **delivery mode** applied to select Phase 2–4 skills once they're proven on-demand first. Recommended sequencing:
1. Build the skill as on-demand first (Phases 2–4, as already planned) — validate it gives correct, trustworthy output
2. Once proven, promote high-value candidates (table above) to scheduled/event-driven execution
3. Wire up the notification channel (Slack/Teams/Jira) as a one-time piece of shared infrastructure, reused by every scheduled skill after that

---

# Track 7 — Notification Infrastructure (Slack / Microsoft Teams)

This is shared plumbing, not a one-off skill — every scheduled skill in Track 6 needs somewhere to post its output, since no one is sitting there watching it run. Building this once means every current and future scheduled/event-driven skill reuses it instead of each author bolting on their own integration.

## Why it's needed now
A scheduled skill that has no delivery channel just runs and discards its output — worthless. This is the missing piece that makes Track 6 actually usable, so it should be built alongside the first scheduled skill, not deferred.

| Skill | Purpose | Risk |
|---|---|---|
| `notify-slack` | Post a formatted finding/report to a Slack channel or DM via webhook or bot token | Write — scoped to messaging only |
| `notify-teams` | Same, via a Microsoft Teams incoming webhook or Graph API | Write — scoped to messaging only |
| `alert-router` | Shared routing logic: severity → channel (e.g. critical → #data-incidents + @on-call, informational → #data-ops-daily) | Read-only decision, write to deliver |

## Design principles
1. **One shared module, many callers** — every scheduled skill (`airflow-dag-health`, `daily-data-ops-report`, `pipeline-incident-triage`, etc.) calls the same `notify-slack`/`notify-teams` skill rather than reimplementing formatting/auth each time
2. **Severity-based routing** — not every finding goes to the same place; a stale-cert warning and a production pipeline failure shouldn't land in the same noisy channel
3. **Silence is the default for "all clear"** — consistent with Track 6's rule: post only when there's a finding, unless it's the daily digest which posts on schedule regardless
4. **Threaded/updateable messages where supported** — e.g. update one Slack thread as a triage skill's investigation progresses, instead of spamming separate messages
5. **No sensitive data in the message body** — findings reference log/query IDs and summaries; anyone needing full detail follows a link back to the source system (also keeps this aligned with the no-PII rule)

## Requirements
| Item | Details | Owner |
|---|---|---|
| Slack | Incoming webhook URL(s) per target channel, or a scoped bot token (`chat:write` only — no broader workspace access) | Slack workspace admin |
| Microsoft Teams | Incoming webhook connector per channel, or Graph API app registration if richer formatting/threading is needed | Teams/M365 admin |
| Channel/routing map | Agreed list of channels and severity mapping (e.g. `#data-incidents`, `#data-ops-daily`) | Team lead decision, not a technical dependency |

## Where it fits the roadmap
Build this **as part of Phase 3/4**, timed to land right before the first scheduled skill goes live (see Track 6) — there's no value in it existing before something needs to post through it, and no value in a scheduled skill existing without it.

---

# Track 8 — Advanced / Predictive Skills (Phase 5+)

These go beyond single-tool triage into multi-system reasoning, forecasting, and carefully bounded autonomous remediation. They are **explicitly gated behind the foundation** — several depend on the verification-loop discipline and the Track 7 notification infrastructure already working in production before they're trustworthy enough to build. Presented here as "where this goes next," not part of the initial build.

## Multi-system correlation
*The real payoff of having every tool's skills in one repo — reasoning across systems, not within one*

| Skill | What makes it complex | Risk |
|---|---|---|
| `cost-anomaly-correlator` | A cost spike alone tells you little — correlates the spike's timing against Databricks job runs, new Airflow DAGs, and recent Terraform/deploy changes to find the actual cause | Read-only |
| `sla-impact-analyzer` | A broken table refresh doesn't just fail once — traces forward through the full lineage graph to every downstream dashboard/report, ranked by which client deliverable is actually affected and by when it's due | Read-only |
| `architecture-diagram-generator` | Reads live Terraform state + AWS/Databricks/Snowflake resources and produces an up-to-date architecture diagram, since hand-maintained diagrams always drift from as-built infra | Read-only |
| `data-lineage-graph-builder` | Extends the single-hop `data-lineage-trace` skill into a full graph crawl: ingestion → transform → warehouse → BI across every system | Read-only |

## Predictive / forecasting
*Judgment beyond "is something wrong right now" — reasoning about trend and growth*

| Skill | What makes it complex | Risk |
|---|---|---|
| `cost-forecaster` | Projects next month's Snowflake/Databricks/AWS spend from historical trend and planned pipeline additions | Read-only |
| `warehouse-right-sizing-advisor` | Goes beyond flagging an oversized warehouse — analyzes months of query patterns to recommend actual right-size/auto-suspend settings with a projected savings figure | Read-only |
| `capacity-planner` | Predicts when current cluster/compute capacity will be outgrown based on data volume trend | Read-only |

## Bounded autonomous remediation
*Highest complexity — real risk, real payoff. Requires the write-skill verification discipline (hypothesis → verify against evidence → only proceed if confirmed) at its most consequential*

| Skill | What makes it complex | Risk |
|---|---|---|
| `self-healing-pipeline` | Detects a failure, diagnoses it, and auto-remediates **only** if it matches a known, pre-approved, low-risk pattern (e.g. transient timeout → retry); anything outside that list escalates to a human instead of guessing | Write — pattern-gated, confirm on anything novel |
| `incident-war-room` | On a major failure, fans out several diagnostic skills at once — Airflow, Databricks, Snowflake, AWS simultaneously — and synthesizes one unified root-cause report instead of running them one at a time. A multi-agent orchestration pattern, not a single skill | Read-only |

## Compliance / governance
*High value for client-facing consulting engagements specifically*

| Skill | What makes it complex | Risk |
|---|---|---|
| `compliance-mapper` | Maps live data flows (which tables hold what, who accesses them) against a regulatory framework (GDPR/HIPAA/SOC2) and flags gaps — requires classifying data sensitivity, not just describing schema | Read-only |
| `data-opportunity-finder` | Scans a client's warehouse for underused, high-quality datasets and proposes new analytics use cases — a value-creation skill rather than an ops one, differentiated for consulting engagements | Read-only |

## Gating criteria before starting Track 8
1. Tracks 1–4 pilot skills are in daily production use and trusted
2. Track 6 (scheduling) and Track 7 (notifications) are live — several Track 8 skills depend on both
3. The write-skill verification discipline (hypothesis → verify → confirm) is documented and applied to at least one production write-capable skill first
4. Explicit sign-off before any auto-remediation skill (`self-healing-pipeline`) goes beyond a "propose the fix, human approves" mode

---

# Skill Count Summary

| Track | Skills planned |
|---|---|
| Data Platform Ops | ~34 (Snowflake 7, Databricks 6, Airflow 6, AWS 6, Power BI 6, Tableau 5) |
| DevOps & Platform | ~17 |
| Data Engineering Practices | ~12 (incl. dbt) |
| SDLC & Client Delivery | ~9 |
| Notification Infrastructure | 3 (shared by all scheduled skills) |
| Cross-cutting | ~6 |
| Advanced / Predictive (Phase 5+) | ~10 |
| **Total catalog** | **~91 candidate skills** |

This is a menu, not a commitment — Phases 1–2 commit to ~5 skills; everything after is prioritized by team demand and measured value.

---

# What Each Track Needs (Requirements Summary)

| Track | Access needed | Tooling per user |
|---|---|---|
| Data Platform | Per-user credentials for Snowflake / Databricks / Airflow API / AWS SSO / Power BI (Azure AD) / Tableau PAT | snowsql, databricks CLI, aws CLI, Python 3.10+ |
| DevOps | Read access to CI system API, Terraform state (read), kubectl contexts, monitoring APIs | git, terraform, kubectl, docker |
| DE Practices | Mostly file/code-based — minimal new access; Spark history server read access | Python, spark deps as needed |
| SDLC & Delivery | Git platform API, Jira/Confluence (connector already available) | git |
| Notification Infrastructure | Slack incoming webhook/bot token (`chat:write` scope only), Microsoft Teams incoming webhook or Graph API app registration | None extra — invoked by other skills |

Common to all: Claude Code license, Git, and the skills repo. **No shared service accounts, no stored secrets — every skill runs as the invoking user.**
