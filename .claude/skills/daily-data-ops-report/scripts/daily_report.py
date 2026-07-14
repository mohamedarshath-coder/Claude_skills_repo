#!/usr/bin/env python3
"""
daily-data-ops-report helper script.

The "morning summary" from the original proposal: one command aggregating
four sibling skills across both platforms --

  1. Failed Databricks jobs (last day)     <- databricks-job-triage
  2. Snowflake cost + anomalies (last day) <- snowflake-cost-audit
  3. Databricks cluster config risks       <- databricks-cluster-audit
  4. Freshness of watched tables           <- snowflake-data-quality

Like unified-cost-optimizer, this genuinely CALLS the sibling skills'
scripts (subprocess) rather than reimplementing them -- fixes there flow
through here automatically. Each section degrades independently: one
platform failing doesn't take down the rest of the report, and every
failure is surfaced with its real error, never silently dropped.

Freshness targets are explicit (--watch-table SCHEMA.TABLE, repeatable):
the report never guesses which tables matter. No targets = section says
"not configured", plainly.

Usage:
    python daily_report.py [--days 1]
        [--watch-table RAW.RAW_ORDERS --watch-table MART.MART_DAILY_SALES ...]
        [--snowflake-connection NAME] [--databricks-profile NAME]
        [--freshness-threshold-days N]
"""
import argparse
import json
import os
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_ROOT = os.path.dirname(SKILL_DIR)

SCRIPTS = {
    "job_triage": os.path.join(SKILLS_ROOT, "databricks-job-triage", "scripts", "job_triage.py"),
    "cost_audit": os.path.join(SKILLS_ROOT, "snowflake-cost-audit", "scripts", "cost_audit.py"),
    "cluster_audit": os.path.join(SKILLS_ROOT, "databricks-cluster-audit", "scripts", "cluster_audit.py"),
    "data_quality": os.path.join(SKILLS_ROOT, "snowflake-data-quality", "scripts", "data_quality.py"),
}


def run_script(script_path, extra_args):
    result = subprocess.run([sys.executable, script_path] + extra_args, capture_output=True, text=True)
    if not result.stdout.strip():
        try:
            err = json.loads(result.stderr.strip())
            return None, err.get("error", result.stderr.strip()[:300])
        except (json.JSONDecodeError, AttributeError):
            return None, result.stderr.strip()[:300] or f"no output, exit {result.returncode}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as e:
        return None, f"could not parse output: {e}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--watch-table", action="append", default=[],
                        help="SCHEMA.TABLE to freshness-check; repeatable")
    parser.add_argument("--snowflake-connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--databricks-profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--freshness-threshold-days", type=int, default=2)
    args = parser.parse_args()

    errors = {}
    report = {"window_days": args.days}

    db_args = ["--profile", args.databricks_profile] if args.databricks_profile else []

    # 1. Failed Databricks jobs
    jobs, err = run_script(SCRIPTS["job_triage"], ["--days", str(args.days)] + db_args)
    if err:
        errors["failed_jobs"] = err
    report["failed_jobs"] = {
        "failed_run_count": jobs["failed_run_count"],
        "jobs": sorted({r["job_name"] for r in jobs["failed_runs"]}),
        "runs": [{"job_name": r["job_name"], "run_id": r["run_id"],
                  "failed_tasks": [t["task_key"] for t in r["failed_tasks"]],
                  "error": (r["failed_tasks"][0]["error_message"] if r["failed_tasks"] else r.get("state_message"))}
                 for r in jobs["failed_runs"]],
    } if jobs else None

    # 2. Snowflake cost
    cost, err = run_script(SCRIPTS["cost_audit"],
                           ["--days", str(args.days), "--connection", args.snowflake_connection])
    if err:
        errors["snowflake_cost"] = err
    report["snowflake_cost"] = {
        "total_warehouse_credits_used": cost["total_warehouse_credits_used"],
        "cost_anomalies": cost["cost_anomalies"],
        "flagged_warehouses": cost["flagged_warehouses"],
        "scope_note": cost["scope_note"],
    } if cost else None

    # 3. Databricks cluster config risks
    clusters, err = run_script(SCRIPTS["cluster_audit"], db_args)
    if err:
        errors["cluster_risks"] = err
    report["cluster_risks"] = {
        "clusters_with_issues": clusters["clusters_with_issues"],
        "issues": [{"cluster": r["cluster_name"], "issue": i["issue"], "severity": i["severity"]}
                   for r in clusters["results"] for i in r["issues"]],
    } if clusters else None

    # 4. Freshness of watched tables
    freshness_results = []
    for target in args.watch_table:
        if "." not in target:
            errors[f"freshness:{target}"] = "expected SCHEMA.TABLE format"
            continue
        schema, table = target.split(".", 1)
        dq, err = run_script(SCRIPTS["data_quality"],
                             ["--schema", schema, "--table", table,
                              "--connection", args.snowflake_connection,
                              "--freshness-threshold-days", str(args.freshness_threshold_days)])
        if err:
            errors[f"freshness:{target}"] = err
            continue
        judged = [c for c in dq["freshness"]["columns"] if c.get("stale") is not None]
        freshness_results.append({
            "table": target,
            "stale_columns": [c for c in judged if c["stale"]],
            "fresh_columns_count": sum(1 for c in judged if not c["stale"]),
        })
    report["freshness"] = freshness_results if args.watch_table else {
        "note": "no --watch-table targets configured"}

    # Overall verdict: quiet-when-healthy signal for a future scheduled run
    findings = 0
    if report.get("failed_jobs"):
        findings += report["failed_jobs"]["failed_run_count"]
    if report.get("snowflake_cost"):
        findings += len(report["snowflake_cost"]["cost_anomalies"]) + len(report["snowflake_cost"]["flagged_warehouses"])
    if report.get("cluster_risks"):
        findings += report["cluster_risks"]["clusters_with_issues"]
    if isinstance(report.get("freshness"), list):
        findings += sum(len(t["stale_columns"]) for t in report["freshness"])

    report["errors"] = errors
    report["total_findings"] = findings
    report["all_clear"] = findings == 0 and not errors

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
