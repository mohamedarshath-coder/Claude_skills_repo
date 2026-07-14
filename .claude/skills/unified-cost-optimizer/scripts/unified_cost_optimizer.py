#!/usr/bin/env python3
"""
unified-cost-optimizer helper script.

Aggregates findings from snowflake-cost-audit and databricks-cluster-audit
into one ranked roadmap -- genuinely calls those skills' own scripts
(subprocess, real cross-skill dependency, not a reimplementation), so a
fix to either underlying skill automatically flows through here too.

IMPORTANT HONESTY NOTE, carried over from both source skills: this does
NOT produce one blended dollar total across platforms. Only Snowflake's
cost-anomaly findings carry a real, quantified credit figure. Everything
else (Snowflake idle/oversized warehouse flags, all Databricks cluster
findings) is a CONFIGURATION-RISK observation with no dollar amount
attached -- ranking them by fabricated cost-equivalence would be exactly
the overclaiming that databricks-cluster-audit was corrected for earlier.
Instead: quantified findings are Tier 1 (ranked by real credits), every
other finding is Tier 2 (configuration risk, listed but not cost-ranked
against Tier 1 or against each other across platforms).

Usage:
    python unified_cost_optimizer.py [--days N] [--snowflake-connection NAME] [--databricks-profile NAME]

Requires: the sibling snowflake-cost-audit and databricks-cluster-audit
scripts to exist at their expected relative paths in this repo.
"""
import argparse
import json
import os
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_ROOT = os.path.dirname(SKILL_DIR)

SNOWFLAKE_SCRIPT = os.path.join(SKILLS_ROOT, "snowflake-cost-audit", "scripts", "cost_audit.py")
DATABRICKS_SCRIPT = os.path.join(SKILLS_ROOT, "databricks-cluster-audit", "scripts", "cluster_audit.py")


def run_script(script_path, extra_args):
    result = subprocess.run(
        [sys.executable, script_path] + extra_args,
        capture_output=True, text=True,
    )
    # The sibling scripts print their JSON error to stderr and exit 1 on a
    # connection/auth failure (stdout is empty in that case) -- surface
    # that real reason instead of a generic "couldn't parse empty string".
    if not result.stdout.strip():
        try:
            err = json.loads(result.stderr.strip())
            return None, err.get("error", result.stderr.strip()[:300])
        except (json.JSONDecodeError, AttributeError):
            return None, result.stderr.strip()[:300] or f"no output, exit code {result.returncode}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as e:
        return None, f"could not parse output: {e}"


def build_roadmap(sf_data, db_data):
    tier1_quantified = []
    tier2_config_risk = []
    source_errors = {}

    if sf_data is None:
        source_errors["snowflake"] = "snowflake-cost-audit did not return usable data"
    else:
        for a in sf_data.get("cost_anomalies", []):
            tier1_quantified.append({
                "source": "snowflake",
                "finding": "cost_anomaly",
                "target": a["warehouse"],
                "quantified_credits": a["latest_credits"],
                "evidence": f"{a['latest_credits']} credits on {a['latest_day']}, +{a['pct_increase']}% vs trailing avg {a['trailing_avg_credits']}",
            })
        for f in sf_data.get("flagged_warehouses", []):
            tier2_config_risk.append({
                "source": "snowflake",
                "finding": f["issue"],
                "target": f["warehouse"],
                "evidence": {k: v for k, v in f.items() if k not in ("warehouse", "issue")},
            })
        if sf_data.get("errors"):
            source_errors["snowflake_partial"] = sf_data["errors"]

    if db_data is None:
        source_errors["databricks"] = "databricks-cluster-audit did not return usable data"
    else:
        for r in db_data.get("results", []):
            for issue in r.get("issues", []):
                if issue["issue"] == "currently_running":
                    continue  # not a cost-optimization finding, it's an activity notice
                tier2_config_risk.append({
                    "source": "databricks",
                    "finding": issue["issue"],
                    "target": r["cluster_name"],
                    "evidence": issue["evidence"],
                    "severity": issue.get("severity"),
                })

    tier1_quantified.sort(key=lambda x: x["quantified_credits"], reverse=True)

    return {
        "tier1_quantified_savings_opportunities": tier1_quantified,
        "tier2_configuration_risks_unquantified": tier2_config_risk,
        "source_errors": source_errors,
        "quantified_total_credits": round(sum(x["quantified_credits"] for x in tier1_quantified), 2),
        "unquantified_finding_count": len(tier2_config_risk),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--snowflake-connection", default=os.environ.get("SNOWFLAKE_CONNECTION_NAME", "default"))
    parser.add_argument("--databricks-profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    args = parser.parse_args()

    sf_args = ["--days", str(args.days), "--connection", args.snowflake_connection]
    sf_data, sf_err = run_script(SNOWFLAKE_SCRIPT, sf_args)

    db_args = []
    if args.databricks_profile:
        db_args = ["--profile", args.databricks_profile]
    db_data, db_err = run_script(DATABRICKS_SCRIPT, db_args)

    roadmap = build_roadmap(sf_data, db_data)
    if sf_err:
        roadmap["source_errors"]["snowflake_script_error"] = sf_err
    if db_err:
        roadmap["source_errors"]["databricks_script_error"] = db_err

    print(json.dumps(roadmap, indent=2, default=str))


if __name__ == "__main__":
    main()
