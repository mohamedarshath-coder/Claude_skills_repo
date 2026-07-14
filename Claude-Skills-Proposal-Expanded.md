# Agent Skills Automation Library

*(AI-agnostic — authored once as open-standard Agent Skills, runnable via Claude Code, Cursor, Snowflake CoCo/Cortex Agents, Databricks Genie Code, GitHub Copilot, OpenAI Codex/GPT, and future agents)*

**Prepared by:** Mohamed Arshath
**Date:** 07 July 2026
**Status:** Draft for review

---

## 1. Executive Summary

We are building a central repository of **Agent Skills** — reusable, version-controlled automation packages that let any team member automate routine data-engineering workflows across our stack using natural-language commands, **regardless of which AI coding assistant they (or a client) use**.

A "skill" packages a workflow's knowledge and steps (as Markdown instructions + helper scripts) so that tasks which today take 20–40 minutes of manual tab-hopping — triaging a failed DAG, auditing warehouse costs, diagnosing a job failure — become a single command such as `/airflow-dag-health` that anyone on the team can run consistently.

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
5. **Stay AI-agnostic by design** — skills are authored to the open Agent Skills standard (`SKILL.md`), not to any one vendor's assistant. The same skill content runs in Claude Code, Cursor, Snowflake's Cortex Agents/CoCo, Databricks Genie Code, GitHub Copilot, and OpenAI Codex/GPT tooling — with per-runtime adaptation kept thin and documented (see Section 3.5). This removes both "you need Claude" and "you need any specific AI tool" as adoption blockers, internally and for clients.

### 2.5 Phased Plan

#### Phase 1 — Foundation (Week 1–2)
- Create the Git repository with the agreed structure, naming conventions, and contribution guide
- Write the skill template and documentation standards (so future skills are consistent) — including the loop-tier and portability fields from Sections 5.4 and 3.5
- Build install scripts (`install.ps1` / `install.sh`) for one-command team setup
- Define the security guardrails (Section 7) and engineering safeguards (Section 7.5): log pre-filter requirement, version-drift check, and the PR validation pipeline (`test-fixtures/` + CI checks)

**Deliverable:** Working repo skeleton + docs + install workflow.

#### Phase 2 — Pilot Skills (Week 2–4)
Build 2–3 read-only, Tier 1 (on-demand) skills end-to-end as reference implementations:

| Pilot skill | Why first |
|---|---|
| `airflow-dag-health` | Daily pain point; read-only; immediately demonstrable value; the Section 5.6 worked example |
| `snowflake-cost-audit` | High visibility with leadership; read-only; clear cost savings |
| `databricks-job-triage` | Frequent manual task; showcases log-analysis strength |

**Deliverable:** 2–3 working skills, demoed to the team, used daily by pilot users.

#### Phase 3 — Team Rollout & Expansion (Week 4–8)
- Onboard the wider team (install session, usage guide)
- Gather feedback from pilot usage; refine templates
- Expand the library per the roadmap in Section 4, prioritized by team votes
- Establish the contribution workflow (any engineer can PR a new skill)
- Promote proven Phase 2 pilots to Tier 3 (scheduled) per Section 5's promotion rules; stand up Track 7 notification infrastructure
- Pilot Claude Tag reactive mode (Section 8) alongside notification rollout

**Deliverable:** 8–12 skills in production use; contribution process running.

#### Phase 4 — Advanced / Cross-Tool Skills (Week 8+)
- Cross-platform incident triage (Airflow → Databricks → Snowflake in one command)
- Scheduled reporting (daily data-ops health summary)
- Jira integration (auto-draft incident tickets with root-cause analysis)
- Claude Tag proactive mode, gated on Track 6 skills having proven themselves scheduled first (Section 8.3)

---

## 3. What Is an Agent Skill?

A skill is a folder containing:
- **`SKILL.md`** — instructions, conventions, and domain knowledge the AI agent follows when the skill is invoked (e.g., which API endpoints to call, what thresholds indicate a problem, how to format the report)
- **Optional helper scripts** — deterministic Python/PowerShell scripts for API calls, parsing, etc.

Skills are plain text files: reviewable in pull requests, versioned in Git, and distributed by simply cloning the repo. Team members install them once and invoke them from any project with `/skill-name` (or the equivalent invocation syntax of their agent — `$skill-name` in Snowflake CoCo, auto-discovery in Cortex Agents, etc.).

### 3.5 AI-Agnostic by Design: One Skill, Many Runtimes

`SKILL.md` is not a Claude-proprietary format — it's an **open Agent Skills standard** that Snowflake, Databricks, and others have adopted, and that maps cleanly onto every major AI coding assistant's customization mechanism. We author skills once, to the standard, and treat each runtime as a distribution target.

**Runtime compatibility matrix:**

| Runtime | How our skills run there | Adaptation effort |
|---|---|---|
| **Claude Code** (primary dev target) | Native `SKILL.md` in `.claude/skills/` — zero changes | None |
| **Snowflake Cortex Agents** | Native `SKILL.md` hosted on a Snowflake stage or linked Git repo; agent auto-discovers and invokes | None–minimal (hosting step only) |
| **Snowflake CoCo (Cortex Code)** | Native `SKILL.md`, invoked as `$skill-name`; generic skills can be contributed back to the public `coco-skills` repo | None–minimal |
| **Databricks Genie Code** | Implements the open Agent Skills standard; installed via `databricks aitools` CLI | None–minimal |
| **GitHub Copilot** | Supports the same open Agent Skills standard (per Databricks' docs) | Minimal |
| **Cursor** | Skill instructions map to Cursor Rules (`.cursor/rules/*.mdc`); helper scripts run via Cursor's agent terminal | Light — frontmatter/format conversion, scriptable |
| **OpenAI Codex CLI / GPT tooling** | Instructions map to `AGENTS.md` / custom-GPT instructions; helper scripts callable as-is from Codex CLI; hosted GPT Actions need an OpenAPI wrapper around scripts | Moderate — heaviest for hosted GPTs, light for Codex CLI |

**Authoring rules that keep skills portable (added to the skill template):**

1. **Vendor-neutral core** — the body of every `SKILL.md` describes the workflow (endpoints, thresholds, checks, output format) with no references to Claude-specific tools or behaviors. Anything runtime-specific lives in a clearly-marked, optional frontmatter/adapter block.
2. **Helper scripts are plain Python/PowerShell** — deterministic, agent-independent, runnable by any assistant that can execute a script (or by a human directly). The intelligence lives in the instructions; the scripts are just deterministic plumbing every runtime can call.
3. **A converter script, not N copies** — the repo ships a small `tools/export-skill` utility that emits a skill in each target's format (Cursor rules file, Snowflake stage upload, `AGENTS.md` merge) from the single canonical `SKILL.md`. One source of truth; conversions are generated, never hand-maintained.
4. **Path abstraction, not hardcoded paths** — a skill that references `./scripts/my-script.py` assumes a Claude Code-style relative-path execution context. Snowflake CoCo's CLI sandbox and Cortex Agents running off a stage resolve paths differently. Every helper script reference in `SKILL.md` uses a token placeholder — `{{SKILL_DIR}}/scripts/my-script.py` — and `tools/export-skill` substitutes the correct absolute or relative path for each target runtime at export time.
5. **Native data access, not hardcoded integration mechanisms** — an instruction like "use the `postgres-mcp-server` to fetch data" reads as an ordinary instruction but silently assumes an MCP-capable runtime. It breaks outright on Databricks Genie or Snowflake, which route data access through their own native governance (Unity Catalog, RBAC) — an MCP reference doesn't just fail to port, it fights the platform's own access model. Skills instead describe the *what* — "query the database tracking this data-quality metric" — and let each runtime's own connector or governance layer handle the *how*. The same logic applies to Claude-specific tool names, slash commands, or other vendor shortcuts (`/compact`, `/zap`) — never referenced in the vendor-neutral core.
6. **CI checks portability** — the existing PR pipeline (Section 7.5c) gains a `portability-lint` check: flags hardcoded paths (rule 4), MCP/plugin/vendor-specific command references (rule 5), and any other Claude-only phrasing; verifies the vendor-neutral core parses standalone.

**Portability caveats — the honest exceptions:**

Most of the catalog ports cleanly because a skill is instructions + deterministic scripts. A few skill *categories* depend on runtime capabilities that not every assistant has:

| Capability needed | Skills affected | Runtime reality |
|---|---|---|
| **Script execution** | Any skill with a helper script — all log-triage skills (mandatory pre-filter scripts, Section 7.5a), most audits | Fine in Claude Code, Cursor, Copilot, Codex CLI, CoCo, Genie Code. **Hosted GPTs (custom GPTs) can't run local scripts** — they need an OpenAPI Action wrapper per script, which is why they're the last-priority export target |
| **Scheduling / autonomous runs** | All Tier 3 product-loop skills (`daily-data-ops-report`, `airflow-dag-health` scheduled mode, the watchdogs) | Needs a runtime with a scheduler or an external trigger (cron + CLI). Claude Code and Cortex Agents support this natively; **Cursor and Copilot are editor-bound — on those runtimes these skills run on-demand only** |
| **Multi-step agentic tool use** | Cross-cutting skills (`pipeline-incident-triage`, `freshness-trace`) that chain many API calls across systems | Works on all agentic runtimes; degrades on instruction-only surfaces (a Cursor rules file guides the agent but the agent must still have terminal/tool access enabled) |
| **Platform-native data access** | Snowflake-internal skills running as Cortex Agents skills | Actually *better* natively — no external credentials needed at all; the reverse caveat |

Rule of thumb: **every skill runs everywhere at Tier 1 (on-demand) on any runtime that can execute scripts; higher loop tiers (Section 5) require a runtime with scheduling.** The export tooling emits a per-runtime capability note automatically so nobody discovers a gap in front of a client.

**Why this matters:**
- **Internally** — engineers aren't forced onto one assistant; whoever uses Cursor or Copilot gets the same `/airflow-dag-health` capability.
- **For clients** — the skill library is deliverable on whatever the client already runs: natively inside their Snowflake or Databricks platform with no AI assistant at all, or via their existing Copilot/Cursor/GPT licenses. "Works with your stack, whatever it is" is the differentiator.
- **Strategically** — we're not betting the library on any single vendor's roadmap. If the assistant landscape shifts, the skills — where all the domain knowledge lives — carry over.

---

## 4. Skills Roadmap

> **All skills below are runtime-agnostic.** Every skill in this catalog is authored once to the open Agent Skills standard and runs on every runtime in the Section 3.5 matrix — Claude Code, Cursor, Snowflake CoCo/Cortex Agents, Databricks Genie Code, GitHub Copilot, and OpenAI Codex/GPT. There is no separate "Cursor catalog" or "GPT catalog" to maintain; one canonical `SKILL.md` per skill, exported per runtime. The handful of capability-dependent exceptions are covered in the portability caveats note in Section 3.5.

### Snowflake

| Skill | Purpose |
|---|---|
| `snowflake-cost-audit` | Warehouse credit usage, idle/oversized warehouses, top expensive queries |
| `snowflake-query` | Safe ad-hoc SQL execution with auto-limits and formatted results |
| `snowflake-schema-explorer` | Table descriptions, column profiling, data dictionary generation |
| `snowflake-query-optimizer` | Analyze slow queries via query profile; suggest fixes |
| `snowflake-data-quality` | Null/duplicate/freshness checks with anomaly report |
| `snowflake-access-review` | Audit roles and grants for compliance |
| `snowflake-clustering-advisor` | Analyzes actual query filter patterns + micro-partition pruning efficiency to recommend clustering keys — not just "warehouse is oversized," but "this specific table needs re-clustering" |
| `snowflake-materialized-view-advisor` | Detects repeated expensive query patterns across query history and recommends materialized views or search optimization |
| `snowflake-multi-cluster-scaling-advisor` | Analyzes warehouse queueing/contention patterns to recommend multi-cluster scaling policy, not just flag an idle warehouse |
| `snowflake-workload-isolation-advisor` | Detects concurrent workload contention and recommends splitting into separate warehouses by workload type |
| `snowflake-time-travel-cost-optimizer` | Analyzes Time Travel/Fail-safe storage costs against actual retention needs, recommends per-table adjustments |

### Databricks

| Skill | Purpose |
|---|---|
| `databricks-job-triage` | Diagnose failed jobs from run logs; suggest fixes |
| `databricks-cluster-audit` | Idle/oversized clusters, cost hotspots |
| `databricks-notebook-review` | Lint notebooks for anti-patterns |
| `databricks-job-deploy` | Create/update jobs from validated specs |
| `databricks-liquid-clustering-advisor` | Recommends liquid clustering keys from actual query patterns — the Databricks analog to Snowflake's clustering advisor |
| `databricks-photon-migration-advisor` | Analyzes workload characteristics to identify which jobs would benefit from the Photon engine |
| `databricks-serverless-migration-advisor` | Identifies classic-compute jobs that could move to serverless for cost/latency wins |
| `databricks-workflow-dependency-optimizer` | Analyzes multi-task job DAGs for unnecessary serialization, suggests restructuring for parallelism |
| `databricks-mlflow-drift-detector` | Monitors registered ML models for performance/data drift over time — extends into MLOps, not just job triage |
| `databricks-autoloader-tuning-advisor` | Tunes Auto Loader checkpoint/file-discovery settings for streaming ingestion workloads |

### Airflow

| Skill | Purpose |
|---|---|
| `airflow-dag-health` | Environment-wide failed/stuck DAG summary with root causes |
| `airflow-failed-task-triage` | Pull task logs, identify error, suggest fix |
| `airflow-dag-review` | Lint DAG code: retries, idempotency, catchup, top-level code |
| `airflow-backfill` | Plan and execute validated backfills |
| `airflow-dag-generator` | Scaffold new DAGs from team conventions |
| `airflow-dependency-graph-optimizer` | Analyzes the full cross-DAG dependency graph for critical-path bottlenecks, suggests restructuring for parallelism |
| `airflow-executor-scaling-advisor` | Analyzes worker/executor utilization trends (Celery/K8s executor) to recommend scaling config |
| `airflow-dataset-migration-assistant` | Helps migrate DAGs from `schedule_interval`-based scheduling to Airflow Datasets (data-aware scheduling) |
| `airflow-cost-per-dag-attribution` | Attributes compute cost per DAG/task for chargeback — especially valuable with KubernetesExecutor pods |
| `airflow-dynamic-task-mapping-reviewer` | Finds DAGs with repetitive tasks that should use dynamic task mapping instead |

### AWS

| Skill | Purpose |
|---|---|
| `aws-cost-report` | Cost Explorer summary with month-over-month deltas and anomalies |
| `aws-well-architected-reviewer` | Runs a full Well-Architected Framework review (all 6 pillars) against a workload, producing a structured gap report |
| `aws-savings-plan-optimizer` | Analyzes usage history to recommend specific Reserved Instance/Savings Plan purchases — goes beyond `aws-cost-report`'s "here's what you spent" |
| `aws-step-functions-reviewer` | Reviews Step Functions state machines for missing error handling/retry logic |
| `aws-eventbridge-rule-auditor` | Finds orphaned, duplicate, or overly broad EventBridge rules |
| `aws-vpc-flow-log-analyzer` | Analyzes VPC flow logs for anomalous traffic patterns — a genuine security-analytics skill, not just a config audit |
| `aws-service-quota-forecaster` | Tracks quota usage trend and predicts when a service limit will actually be hit |

### Power BI

| Skill | Purpose |
|---|---|
| `powerbi-refresh-triage` | Diagnose failed dataset refreshes (credentials, gateway, timeouts) |
| `powerbi-workspace-audit` | Unused reports, stale datasets, orphaned content inventory |
| `powerbi-lineage-trace` | Impact analysis: which reports depend on a given source table |
| `powerbi-dax-review` | Review DAX measures for performance anti-patterns |
| `powerbi-access-review` | Workspace/report sharing and RLS audit |
| `powerbi-composite-model-performance-advisor` | Analyzes composite/DirectQuery models for bottlenecks, recommends aggregation tables |
| `powerbi-premium-capacity-planner` | Analyzes Premium/Fabric capacity unit (CU) consumption trend, forecasts capacity needs |
| `powerbi-usage-deep-analyzer` | Goes past "unused reports" — analyzes which specific visuals/pages within a used report are actually viewed, recommends simplification |
| `powerbi-deployment-pipeline-auditor` | Audits dev/test/prod pipeline config and content parity |

### Tableau

| Skill | Purpose |
|---|---|
| `tableau-extract-triage` | Diagnose failed extract refreshes |
| `tableau-content-audit` | Stale workbooks and unused data sources via usage stats |
| `tableau-workbook-review` | Workbook performance review (filters, calcs, extract design) |
| `tableau-lineage-trace` | Which dashboards depend on a given database table (Metadata API) |
| `tableau-hyper-extract-tuner` | Analyzes Hyper extract structure + query logs to recommend restructuring (filters, aggregation) for performance |
| `tableau-server-capacity-planner` | Analyzes backgrounder/VizQL process utilization to recommend server scaling |
| `tableau-calc-field-complexity-auditor` | Finds duplicated/overly complex calculated fields scattered across workbooks, recommends consolidating into the shared data source |
| `tableau-governance-certification-advisor` | Recommends which data sources should be certified/promoted based on usage and quality signals |

### Cross-Cutting (Phase 4)

| Skill | Purpose |
|---|---|
| `pipeline-incident-triage` | End-to-end failure trace across Airflow → Databricks → Snowflake |
| `freshness-trace` | "Dashboard shows stale data" → trace BI refresh → Snowflake source → Airflow DAG to pinpoint the break |
| `daily-data-ops-report` | One morning summary: DAG health + job failures + cost anomalies + freshness |
| `jira-ticket-from-incident` | Auto-draft Jira tickets with logs and root-cause after triage |
| `runbook-executor` | Convert existing ops runbooks into executable skills |
| `unified-cost-optimizer` | Aggregates savings recommendations from `snowflake-multi-cluster-scaling-advisor`, `databricks-serverless-migration-advisor`, and `aws-savings-plan-optimizer` into one prioritized, ranked cost-savings roadmap — instead of three separate reports leadership has to reconcile themselves |
| `workload-placement-advisor` | For a given ETL job, reasons about cost/performance characteristics to recommend whether it should run in Snowflake (Snowpark) vs. Databricks (Spark) — genuine architecture decision support, not just monitoring either platform |
| `end-to-end-capacity-planner` | Combines `airflow-executor-scaling-advisor` + Databricks cluster capacity + Snowflake warehouse scaling + Power BI/Tableau capacity into one holistic forecast: "here's when the whole stack runs out of headroom," not tool-by-tool |
| `unified-governance-scorecard` | Rolls up catalog-orphan-finder, pii-scanner, `snowflake-access-review`, `tableau-governance-certification-advisor`, etc. into one governance health score — the kind of single number leadership actually wants |
| `cross-platform-architecture-advisor` | Given a new workload requirement, recommends which warehouse and which BI tool best fits, based on the org's actual cost/performance data rather than a generic recommendation |
| `multi-tool-migration-planner` | For consulting engagements specifically: given a client's current stack, produces a migration roadmap (e.g., Tableau → Power BI, on-prem Airflow → Databricks Workflows) with effort/risk estimates per step |
| `lineage-impact-simulator` | Extends `data-lineage-graph-builder` — "if I re-cluster this table / change this DAG schedule, which downstream reports and dashboards are affected, and by how much" — turns lineage from descriptive into predictive |

### Git

| Skill | Purpose |
|---|---|
| `repo-hygiene-audit` | Stale branches, unmerged PRs sitting for months, large binaries accidentally committed |
| `commit-message-linter` | Enforce conventional-commit format on a PR; explain what's wrong if it fails |
| `stale-branch-cleanup-report` | Branches untouched for X months, flagged as safe-to-delete candidates — report only, human deletes |
| `pr-size-analyzer` | Flags oversized PRs that are hard to review, suggests where to split |
| `changelog-generator` | Structured changelog from commit history using semantic-version conventions — broader than `release-notes`, which is PR-summary focused |
| `git-bisect-assistant` | Given a regression, orchestrates `git bisect` + test runs to find the exact commit that introduced it |
| `branch-protection-auditor` | Checks branch protection rules/required reviewers against the team's agreed convention |
| `git-hooks-generator` | Scaffolds pre-commit/pre-push hooks (lint, secrets-scan, test) matching team standards |
| `large-file-detector` | Finds large binaries in history, recommends git-lfs migration |
| `repo-migration-assistant` | Migrates a repo between Git platforms (e.g. Bitbucket → GitHub) preserving history/PRs/issues where possible |
| `git-blame-context` | For a given file/line, summarizes why it last changed and who to ask — turns blame output into actual context instead of just a name and date |
| `git-history-rewrite-planner` | Plans a safe history rewrite (e.g. purging a leaked secret from history via `git filter-repo`/BFG) — has to account for every collaborator's local clones, coordinate a force-push window, and produce a rollback plan. Real damage if done carelessly |
| `git-secrets-history-remediation` | Goes further: a secret found already committed (not just staged) needs both a credential rotation and a history scrub, sequenced correctly (rotate first, always) — a genuine verify-before-act write skill |
| `rebase-conflict-predictor` | Before a long-lived feature branch merges, diffs it against main's recent changes to predict which files will conflict — lets someone rebase early instead of discovering a nightmare merge at PR time |
| `dependency-graph-impact-analyzer` | For a monorepo: given a changed file, determines which packages/services are actually affected and need rebuilding/retesting — the same "affected detection" problem tools like Bazel/Nx solve, applied generically |
| `code-ownership-drift-detector` | Compares the CODEOWNERS file against who's actually been committing to each path (via git log/blame history) and flags where the documented owner no longer matches reality |
| `git-forensics-investigator` | For a security incident: reconstructs exactly what changed, by whom, and when across a suspicious window — correlating commits, force-pushes, and reflog entries, not just a normal git log |
| `release-readiness-analyzer` | Cross-cutting — aggregates open PRs, CI status, changelog completeness, and linked incident tickets into a single go/no-go score for a release branch, instead of a human manually checking four dashboards |
| `dora-metrics-analyzer` | Cross-cutting — computes real DORA metrics (lead time, deploy frequency, change failure rate, MTTR) by correlating Git history with CI/CD and incident data. Genuinely valuable to engineering leadership, not just individual contributors |

---

## 5. Loop Engineering Model

### 5.1 Why we need this section

Mid-2026 introduced a new piece of shared vocabulary across the AI engineering world: "the loop." Anthropic, Cursor, Warp, Factory, and several independent practitioners have all converged on loop-based framings for how autonomous coding agents should be — but the word is used to mean at least four different architectures, and the debate about "how much autonomy is too much" is really four separate debates wearing one name.

Rather than adopt "autonomy" as a single on/off switch for this skills library, we're adopting a **five-tier loop model**: four nested loop types that describe increasing scope of autonomy, plus one loop — the oversight loop — that never gets delegated. Every skill in the catalog gets classified against this model at design time. This isn't academic — it's the difference between a "read-only triage skill" that quietly grows write behavior nobody signed off on, and a catalog where every skill's blast radius is legible before it's built.

### 5.2 The four loops, applied to our catalog

**1. Execution loop — the agent's own act/observe/decide cycle**

This is the innermost loop and it's not something we design — it's built into Claude Code (or any agent runtime) by default. Within a single skill invocation, the agent calls a tool, reads the result, decides the next action, and repeats until there's nothing left to do or it decides it's finished. Every skill in our catalog has this, whether it's a one-shot `snowflake-query` or a multi-step `pipeline-incident-triage`.

The known failure mode: the loop ends whenever the agent *decides* it's done, whether or not it actually is. This is why our existing Section 7.5 safeguards (log pre-filtering, degradation caps) matter — they keep the execution loop from silently declaring victory on a truncated or misleading payload.

**2. Task loop — re-run against a fixed spec until it resolves**

A task loop restarts a skill against the same condition repeatedly — not a single pass, but a bounded retry-until-resolved cycle. The defining trait (from Geoffrey Huntley's "Ralph Loop" pattern) is that each retry gets a *fresh* look at the problem rather than accumulating context, which avoids the context rot that degrades long agent sessions. Exit condition is always well-defined — spec compliance or test pass — and it always eventually terminates or escalates.

Existing and new candidates that fit this tier:

| Skill | What the retry-until-resolved cycle looks like |
|---|---|
| `airflow-backfill` | Plan → attempt → validate → retry against the backfill spec until it succeeds or a real blocker surfaces |
| `git-bisect-assistant` | Iterate bisect + test cycles until the regressing commit is isolated |
| `self-healing-pipeline` (Track 8) | Retry a known-safe remediation pattern (e.g. transient timeout → retry) until resolved or escalate |
| `snowflake-ddl-diff` (migration mode) | Generate migration DDL, validate against target schema, retry the diff until dev/qa/prod converge |
| `databricks-job-deploy` | Deploy from spec, validate the run, retry/adjust config until the job succeeds against its validation spec |
| `dbt-test-triage` | Re-run failing dbt tests after each suggested fix until the model passes or the failure is traced to source data (human call from there) |
| `terraform-plan-review` (apply-loop variant) | Re-plan after each flagged change is addressed, until the plan is clean or only human-approved destructive changes remain |
| *New:* `rebase-conflict-resolver` | Attempts auto-resolution of predicted conflicts (from `rebase-conflict-predictor`) file-by-file, retrying until only genuinely ambiguous hunks are left for a human |
| *New:* `dq-rule-remediation-loop` | Once `dq-rule-generator` flags a failing rule, retries candidate fixes (null-handling, dedup logic) against the rule until it passes or is flagged unfixable automatically |

**3. Product loop — continuous, externally-triggered, no fixed exit**

This is where a skill stops being something an engineer invokes and starts being something that runs itself: on a schedule, or triggered by an external event, watching for signals that originate outside the skill entirely (a DAG failure, a cost anomaly, a stale dashboard). There's no single "done" — it just keeps running and reports when something needs attention. This is precisely **Track 6 (Scheduled/Autonomous Skills)** and its "stay quiet when healthy" rule.

| Skill | Trigger | Why it fits product-loop |
|---|---|---|
| `daily-data-ops-report` | Daily, fixed time (e.g. 7 AM) | Designed from the start to be a morning summary; silence on a healthy day |
| `airflow-dag-health` | Every 15–30 min, business hours | Catches failures minutes after they happen instead of whenever someone checks |
| `snowflake-cost-audit` | Weekly | Cost anomalies are a trend signal, not real-time |
| `pipeline-incident-triage` | Event/webhook-triggered | Fires the moment a DAG/job fails — genuinely event-driven, not time-driven |
| `ssl-cert-check`, `dependency-audit` | Weekly | Classic "don't forget to check this" hygiene tasks |
| `powerbi-refresh-triage` (monitor mode) | On refresh-failure webhook | Same triage logic as on-demand, just fired by the platform instead of a person |
| `aws-service-quota-forecaster` | Weekly | Needs a trend window to forecast against, not a point-in-time check |
| *New:* `freshness-watchdog` | Continuous, tied to `freshness-trace` | Watches source-to-dashboard freshness SLAs and only speaks when one is breached |
| *New:* `cost-anomaly-watchdog` | Daily, tied to `cost-anomaly-correlator` (Track 8) | Runs the correlation daily but only reports when a spike exceeds a defined threshold |
| *New:* `repo-hygiene-watchdog` | Weekly, tied to `repo-hygiene-audit` | Standing job that only posts when new stale branches/large binaries appear since last run |

The human role here is deliberately configurable — Track 7's severity-based routing (critical → paged, informational → digest) is exactly how we decide, per finding, whether a human needs to be in the loop right now or just informed later.

**4. System loop — improving the skill library itself**

The outermost automatable loop doesn't do the ops work — it studies and maintains the thing that does the ops work. It iterates on thresholds, fixture accuracy, and skill effectiveness over time, the same way Track 8's `warehouse-right-sizing-advisor` reasons about trend rather than point-in-time state, but applied reflexively to our own catalog instead of a client's infrastructure.

New candidate skills worth adding to the catalog specifically because of this loop tier:

| Skill | What it audits |
|---|---|
| `skill-effectiveness-auditor` | Reviews a skill's output history against actual outcomes (was the diagnosis right? did the recommended fix work?), flags skills whose thresholds or logic have gone stale — feeds directly into existing **version-drift protection** (Section 7.5b) and the **fixture-test CI check** (Section 7.5c) rather than replacing them |
| `skill-usage-analyzer` | Which skills are actually invoked, how often, and by whom — surfaces low-adoption skills worth retiring or high-demand ones worth prioritizing for the "advisor" upgrade path (Section 4) |
| `fixture-drift-detector` | Flags `test-fixtures/<skill-name>/` fixtures that no longer resemble current production log/report shapes, before a stale fixture gives a false sense of CI coverage |
| `false-positive-tracker` | Logs cases where a human overrode or dismissed a skill's finding, aggregates patterns to tune thresholds (e.g. `snowflake-cost-audit` flagging warehouses that are intentionally oversized for a known burst window) |
| `prompt-regression-detector` | On any `SKILL.md` prose edit, re-runs the fixture suite and flags if a previously-passing scenario now behaves differently — the "small tweak silently regresses a different path" risk named in Section 7.5c, made continuous instead of PR-triggered only |

**5. The oversight loop — always human, never delegated**

Above all four automatable loops sits the one ring that sets goals, allocates scope, and culls work — and in this project, it stays human, full stop. Concretely, this is:
- Every confirmation gate already required in Section 7 ("Read-only by default... any write-capable skill requires explicit user confirmation")
- The Track 8 gating criteria — no auto-remediation skill goes beyond "propose the fix, human approves" without explicit sign-off
- Decisions about *which* skills get promoted from on-demand → scheduled (Track 6) → auto-remediating (Track 8) in the first place

### 5.3 The autonomy dial is per-skill, not per-track

The industry debate at the June/July 2026 AI Engineer World's Fair split roughly into two camps: "ratchet autonomy up as trust accumulates" (Warp, Factory) versus "the dial has a hard stop, full delegation costs people their understanding of the system" (HumanLayer, Notion). Our position doesn't have to pick a side globally — the loop tier is a **per-skill decision**, and most of our catalog will never need to leave Tier 2:

| Autonomy tier | Maps to loop type | Representative skills |
|---|---|---|
| **On-demand** | Execution loop only | The large majority of the catalog — `snowflake-query`, `databricks-notebook-review`, `sql-review`, `pr-review-standards`, `eda-starter`, and every triage/audit/review/generate skill invoked by `/skill-name` |
| **Retry-until-resolved** | Task loop | `airflow-backfill`, `git-bisect-assistant`, `databricks-job-deploy`, `dbt-test-triage`, `snowflake-ddl-diff`, `rebase-conflict-resolver`, `dq-rule-remediation-loop` |
| **Scheduled / event-driven, notify-only** | Product loop | `daily-data-ops-report`, `airflow-dag-health`, `snowflake-cost-audit`, `pipeline-incident-triage`, `ssl-cert-check`, `dependency-audit`, `freshness-watchdog`, `cost-anomaly-watchdog`, `repo-hygiene-watchdog` |
| **Pattern-gated auto-remediation** | Task loop nested inside a product loop, with an oversight checkpoint on anything novel | Only `self-healing-pipeline`, and only after Track 8's gating criteria are met |
| **Self-improving** | System loop | `skill-effectiveness-auditor`, `skill-usage-analyzer`, `fixture-drift-detector`, `false-positive-tracker`, `prompt-regression-detector` — improves the library, never touches client/production systems directly |

### 5.4 Concrete additions to the skill authoring template

1. **Declare a loop tier** in every `SKILL.md` frontmatter, alongside the existing risk tag (Read-only / Generates code / Write — confirm) — e.g. `loop-tier: on-demand` or `loop-tier: scheduled-notify-only`. Reviewers should be able to see *what a skill can do* and *how often it decides to act* in the same glance.
2. **No skill starts above Tier 1 (on-demand).** Promotion to a higher tier (scheduled, retry-until-resolved, auto-remediating) is a separate, explicit PR — never bundled with the skill's initial authoring — so the oversight decision is visible in Git history on its own.
3. **Any skill at Tier 4 (pattern-gated auto-remediation)** must document its exact escalation boundary in `SKILL.md`: which patterns are pre-approved, and the literal fallback behavior ("anything else → escalate, do not attempt") — this is the same discipline the Master Plan already requires for `self-healing-pipeline`, now made a template-level rule instead of a one-off callout.

### 5.5 Loop safety limits

A loop without a wired-in exit signal doesn't converge — it just runs until something external stops it. Every tier above on-demand therefore carries mandatory operational limits, declared in the skill's `SKILL.md` frontmatter and enforceable in review:

| Tier | Mandatory limits |
|---|---|
| **Retry-until-resolved (task loop)** | Hard retry cap (default: 3 attempts, then escalate with a summary of what was tried). Each retry must change something — identical retry of an identical action is a bug, not persistence. |
| **Scheduled / notify-only (product loop)** | Every scheduled skill must have a notification channel wired before it goes live (Track 7 — a loop that runs and discards output is worthless). A per-run cost/token budget. A documented pause/kill mechanism (disable the schedule) that any team member can operate, not just the author. |
| **Pattern-gated auto-remediation** | All of the above, plus: an action allowlist (the pre-approved patterns, nothing else), a rate limit (e.g. max N remediations per day — repeated firing on the same pipeline means the pattern match is wrong, not that the fix keeps working), and a full audit log of every action taken, reviewable weekly. |
| **Self-improving (system loop)** | Proposes changes as PRs only — a system-loop skill never edits another skill directly. The human merge is the loop's exit signal. |

Two rules apply to every tier:
- **Every loop iteration is logged** — timestamp, trigger, action, outcome — somewhere a human can audit after the fact. If we can't reconstruct what a loop did last Tuesday, it doesn't run unattended.
- **No loop monitors itself.** The signal that a scheduled skill has gone quiet (crashed, hung, silently failing) has to come from outside it — a simple heartbeat check is part of the Track 7 infrastructure, not each skill's own responsibility.

### 5.6 Worked example: the lifecycle of `airflow-dag-health`

To make the model concrete, here's how one pilot skill moves through the tiers over its first months:

1. **Phase 2 — born at Tier 1 (on-demand).** An engineer types `/airflow-dag-health` each morning. The skill scans the environment, summarizes failed/stuck DAGs with root causes. Humans validate every output because they're reading it live.
2. **Trust accumulates.** After ~a month of daily use, the pilot group agrees its diagnoses are consistently right. The false positives it does produce (e.g. flagging a DAG that's paused intentionally) are noted.
3. **Promotion to Tier 3 — a separate, explicit PR.** The PR adds the schedule (every 30 min, business hours), the notification route (findings → `#data-ops`, silence when healthy), the per-run budget, and the kill switch. Reviewers evaluate the *promotion decision* on its own, not buried in feature changes.
4. **The system loop closes around it.** Its dismissed findings flow into `false-positive-tracker`; after two weeks the pattern "paused DAGs get flagged" is confirmed and a threshold fix is proposed as a PR to the skill. `fixture-drift-detector` keeps its test fixtures honest as the Airflow environment evolves.
5. **The oversight loop stays where it was.** No part of this lifecycle gave the skill write access to Airflow. If it ever should (e.g. auto-retrying a known-transient failure), that's a Tier 4 promotion with Track 8's gating criteria — a new decision for a human, not a gradual drift.

The pattern generalizes: **skills earn autonomy through observed reliability, promotion is always an explicit reviewed decision, and the write boundary never moves implicitly.**

---

## 6. Requirements

### 6.1 Access & Licensing

| Item | Details |
|---|---|
| AI assistant licenses | Claude Code seats for the pilot group (primary dev target); skills equally usable from Cursor, GitHub Copilot, or OpenAI Codex seats engineers already hold — no single-vendor requirement (see Section 3.5) |
| Git repository | New repo on our Git platform (GitHub/GitLab/Bitbucket) with team access |
| Snowflake access | Each user's existing role; a monitoring role with `ACCOUNT_USAGE` read access needed for cost-audit skills |
| Databricks access | Personal access tokens or existing SSO profiles per user; workspace read access to Jobs/Clusters APIs |
| Airflow access | REST API enabled + read credentials per user (or a read-only service role) |
| AWS access | Existing SSO profiles; read access to Cost Explorer, CloudWatch, S3 metadata, IAM (read-only) |
| Power BI access | Power BI REST API access via each user's Azure AD account; admin-read API for tenant-wide audits (optional) |
| Tableau access | Tableau Server/Cloud REST + Metadata API; personal access tokens per user |
| Snowflake Cortex Agents Skills (native) | Ability to create/write to a Snowflake named stage (or a linked Git repo) to host skills for Snowflake's in-platform Cortex Agents; SQL/REST/Snowsight access to attach skills to an agent |
| Databricks Agent Skills (native) | `databricks aitools` CLI access (bundled with Databricks CLI) to install/publish skills for Databricks' native Genie Code agent |

### 6.2 Tooling (per user machine)

- Claude Code CLI (or Cursor / GitHub Copilot / OpenAI Codex CLI — see Section 3.5)
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

1. **No credentials in the repository** — skills reference the user's own environment (AWS SSO, Snowflake config, Databricks profiles). `.gitignore` blocks `.env` and config files; no API keys, secrets, or connection strings are ever committed.
2. **No client data or PII in skills or outputs** — skills operate on metadata (logs, configs, usage stats) by default; any data-touching skill (e.g., data-quality checks) reports aggregates only.
3. **Read-only by default** — Phases 1–3 are almost entirely read-only. Any write-capable skill (backfill, job deploy) requires explicit user confirmation before acting and is clearly labeled.
4. **Existing access controls preserved** — a skill can only do what the invoking user's credentials allow; no shared/elevated service accounts for interactive use.
5. **PR review for all skills** — every skill is code-reviewed before merge, like any production code.
6. **Private repository** — internal access only.
7. **Governance is runtime-independent** — rules 1–6 apply identically whether a skill runs in Claude Code, Cursor, Copilot, a GPT, or natively inside Snowflake/Databricks. In particular, exporting a skill to another runtime (Section 3.5) never bakes in credentials, endpoints, or client identifiers that the canonical `SKILL.md` doesn't contain — the `portability-lint` CI check enforces this on every export path.

### 7.5 Engineering Safeguards

Raised during technical review — three real risks that only surface after skills are in daily use, so they're addressed now.

#### a) Log pre-filtering (mandatory, not optional)

Skills that ingest raw logs (`databricks-job-triage`, `aws-glue-triage`, `airflow-failed-task-triage`) risk blowing past context limits or drowning the model in boilerplate (repeated GC logs, verbose retry noise) before it ever reaches the actual error.

- **Rule:** any skill that touches logs must ship a helper script that pre-filters *before* the payload reaches the AI agent — strip repetitive noise, isolate the actual exception/failure block via regex or structural parsing.
- **Degradation rule:** even after filtering, cap the payload (e.g., last N lines of the real failure + a count of suppressed noise lines) so a worse-than-expected log truncates gracefully instead of silently.
- This is a mandatory field in the skill template, not left to each author's discretion.

#### b) Version drift protection

Because skills are cloned/pulled locally, an engineer can end up running a stale `/snowflake-cost-audit` with an outdated threshold or deprecated schema reference — producing confident-but-wrong output.

- **Approach:** the install process writes a version stamp; each skill checks that stamp against the repo's latest tag/commit on invocation and prints a one-line staleness warning if behind.
- Deliberately lightweight — no custom wrapper binaries or background processes (higher maintenance surface, more places to break across OS/shell differences) — just a check baked into the skill's own instructions.

#### c) CI/CD validation for skills (not just for clients)

A `SKILL.md` is prose, not code — a "small tweak" to fix one edge case can silently regress a different path the same skill handles, and nobody reviews prose diffs the way they review logic diffs.

**Pipeline run on every PR to the skills repo:**

| Check | What it catches |
|---|---|
| `structure-check` | Every changed skill folder has a valid `SKILL.md` with required frontmatter (name, description, risk tag, loop tier — Section 5.4) |
| `secrets-scan` | Gitleaks/TruffleHog over the diff — hard fail on anything resembling a key/token/connection string |
| `portability-lint` | Flags hardcoded paths, MCP/plugin/vendor-specific command references, and other Claude-only phrasing (Section 3.5, rules 4–6) |
| `fixture-test` | Replays the modified skill against `test-fixtures/<skill-name>/` (anonymized historical logs/reports) and asserts on **structure**, not exact wording — did it cite evidence, identify the right root-cause category, stay read-only where required |
| `lint-conventions` | Naming convention (tool-prefix-verb), risk tag present, no hardcoded client names/URLs |

Any failing check blocks merge. Fixture assertions deliberately check structural properties, not literal output text — asserting exact wording would fail on harmless phrasing changes and the team would start ignoring the check.

**Dogfooding note:** since `ci-failure-triage`, `dependency-audit`, and `secrets-scan` are already planned as client-facing skills (Section 4, DevOps track — see Master Plan), the same skills run against our own repo's pipeline. Useful as a demo point: the skill diagnosing its own repo's CI failures.

---

## 8. AI as a Coworker — Claude Tag in Slack

The tracks above treat the AI agent as a tool an engineer invokes (on-demand skills) or a process that runs itself and posts results (scheduled skills). There's a third delivery surface between those two: the agent as a **persistent teammate in the channels where ops conversations already happen**. Anthropic ships this as **Claude Tag** (Claude in Slack); it's also the pattern Anthropic reports using internally — agents that are "delegated and proactive: not 'fix this bug' but take responsibility for this part of the codebase, monitor this feedback channel, and pick up tasks on your own."

### 8.1 What it adds over notifications

The notification infrastructure (Master Plan Track 7) makes Slack a *destination* — skills post findings there. Claude Tag makes Slack a *workplace*: the same skill library becomes conversational, in-thread, and eventually proactive, with zero context-switch to a terminal.

| Mode | What it looks like | Trust tier |
|---|---|---|
| **Reactive (first)** | An engineer tags `@Claude` in `#data-incidents`: "the marketing dashboard looks stale" → Claude runs `freshness-trace`, replies in-thread with the diagnosis, evidence links, and the suggested fix. Same skill, same output — just invoked conversationally where the conversation already is | Same as on-demand skills (Tier 1) — a human asked, a human reads the answer |
| **Thread-following** | During an incident, Claude stays in the thread: updates as its triage progresses (one updated message, not spam — Track 7's threading principle), answers follow-ups ("which other dashboards are affected?") by chaining `lineage-impact-simulator` without being re-briefed | Still reactive — it only acts within a thread a human started |
| **Proactive (later, explicit promotion)** | Claude watches designated channels for patterns: someone reports "dashboard stale" for the third time this week → it offers to run `freshness-trace` unprompted, or drafts the Jira ticket (`jira-ticket-from-incident`) when a triage thread concludes without one | This is a **product-loop promotion (Section 5, Tier 3)** — requires the same explicit promotion PR, notification rules, and kill switch as any scheduled skill |

### 8.2 Why this fits the project rather than extending it

- **Same skills, new surface** — nothing in the catalog is rewritten. Claude Tag invokes the identical `SKILL.md` content; Slack is just another runtime column in the Section 3.5 matrix (agentic, script-capable via Claude's backend, no local editor needed).
- **Same loop governance** — reactive use is Tier 1, channel-watching is Tier 3, and anything write-capable (posting a Jira ticket, triggering a backfill) surfaces its confirmation gate *as a Slack approval prompt* instead of a terminal prompt. The oversight loop moves into the thread; it doesn't disappear.
- **Same security rules** — Section 7 applies unchanged: no credentials in messages, findings reference log/query IDs with links back to source systems rather than pasting sensitive payloads (Track 7's no-sensitive-data rule), and Claude Tag's workspace access is scoped to designated ops channels only.
- **Adoption accelerant** — for teammates who never open a terminal (analysts, PMs, on-call managers), this is the first surface where the skill library is usable at all. "Ask the bot in the incident channel" has a much lower barrier than "install the repo and run a CLI."

### 8.3 Rollout position

Land this alongside Track 7 (notification infrastructure) in Phase 3/4 — reactive mode first, since it reuses on-demand skills as-is and needs only the Slack workspace installation (`/install-slack-app`, workspace admin approval, channel scoping). Proactive mode waits until the promoted skill has proven itself scheduled (Track 6) — a bot that volunteers wrong diagnoses in a busy incident channel burns trust faster than any demo can rebuild it.

**New requirements row:** Slack workspace admin approval to install Claude Tag; agreed list of channels it joins (`#data-incidents`, `#data-ops-daily`); Teams equivalent tracked as a later follow-on if/when Anthropic ships one (until then, Teams remains notification-only via Track 7 webhooks).

---

## 9. Success Metrics

| Metric | Target (first quarter) |
|---|---|
| Skills in production use | 10+ |
| Active users | Full pilot team, expanding to wider group |
| Time saved on routine triage/audit tasks | 30–50% reduction (measured via before/after task timing on 3 benchmark workflows) |
| Mean time to diagnose pipeline failures | Reduced from ~30 min to <10 min for covered failure types |
| Team contributions | ≥3 skills authored by engineers other than me |
| Cross-runtime usage | ≥1 skill demonstrably invoked from a non-Claude runtime (Cursor, Copilot, CoCo, or Genie Code) — validates the AI-agnostic claim in Section 3.5, not just its design |

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Incorrect diagnosis/suggestion by a skill | Skills report findings with evidence (logs, query IDs); human validates before acting; read-only default |
| Credential misuse or leakage | No secrets in repo; per-user auth only; org security settings enforced |
| API access not granted in time | Requirements list in Section 6.1 raised in week 1; pilot skills chosen to need minimal new access |
| Low adoption | Start with the most painful daily tasks; one-command install; demo sessions |
| Skill sprawl / inconsistency | Template + conventions doc + mandatory PR review |
| A loop runs unattended and never converges | Section 5.5's mandatory retry caps, kill switches, and audit logs — no skill is promoted past Tier 1 without them |
| Portability claim breaks in practice (a skill silently assumes Claude-only behavior) | `portability-lint` CI check (Section 7.5c) enforced on every PR; caveats documented up front in Section 3.5 rather than discovered in front of a client |
| Claude Tag surfaces a wrong diagnosis in a live incident channel | Reactive mode only until proven (Section 8.3); proactive mode requires the same Tier 3 promotion discipline as any scheduled skill |

---

## 11. Immediate Next Steps (Pending Approval)

1. Approve pilot scope (Phases 1–2) and confirm pilot user group
2. Request the access items in Section 6.1 (Snowflake monitoring role, Airflow API access)
3. Create the repository and begin Phase 1
4. Confirm which secondary runtime (Cursor, Copilot, or CoCo) to validate the AI-agnostic export path against during Phase 2, so cross-runtime usage (Section 9) has a concrete target from day one

---

*Questions or scope adjustments welcome — the roadmap in Section 4 is a menu, not a commitment; we'll prioritize based on team input.*
