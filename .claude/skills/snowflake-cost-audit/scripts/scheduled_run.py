#!/usr/bin/env python3
"""
Scheduled (Tier 3 / product-loop) wrapper around cost_audit.py.

This is what a Windows Task Scheduler / cron entry calls -- NOT what
Claude invokes on-demand. The interactive skill (`/snowflake-cost-audit`)
is unchanged and still runs cost_audit.py directly for ad-hoc checks.

What this adds, per .claude/rules/loop-engineering.md's mandatory
Tier-3 safety limits:
  - Kill switch: set COST_AUDIT_SCHEDULE_DISABLED=1 to pause the schedule
    without touching the Task Scheduler config.
  - Run logging: every run appends one line to digest.log (timestamp,
    trigger, outcome) -- an unattended loop must be auditable after the fact.
  - Stay-quiet-when-healthy: only appends a FINDING block (and posts to
    Slack, if SLACK_WEBHOOK_URL is set) when cost_anomalies is non-empty.
    A clean day produces exactly one quiet digest.log line, nothing more.
  - Per-run scope: always a 1-day lookback -- this runs daily, so "today
    vs trailing average" is the anomaly signal, not a growing window.

Usage (called by the scheduler, not a person):
    python scheduled_run.py
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST_LOG = os.path.join(SKILL_DIR, "digest.log")


def log_line(text):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(DIGEST_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {text}\n")


def post_to_slack(finding_text):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return  # No Slack configured yet -- digest.log is the only channel for now.
    payload = json.dumps({"text": finding_text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log_line(f"WARNING: Slack notification failed: {e}")


def main():
    if os.environ.get("COST_AUDIT_SCHEDULE_DISABLED"):
        log_line("SKIPPED: kill switch COST_AUDIT_SCHEDULE_DISABLED is set")
        return

    script_path = os.path.join(SKILL_DIR, "scripts", "cost_audit.py")
    result = subprocess.run(
        [sys.executable, script_path, "--days", "1"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log_line(f"ERROR: cost_audit.py failed: {result.stderr.strip()[:300]}")
        return

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log_line(f"ERROR: could not parse cost_audit.py output: {e}")
        return

    anomalies = data.get("cost_anomalies", [])
    flagged = data.get("flagged_warehouses", [])
    errors = data.get("errors", {})

    if not anomalies and not flagged and not errors:
        log_line(f"OK: no findings (total credits: {data.get('total_warehouse_credits_used')})")
        return

    lines = [f"snowflake-cost-audit daily check found {len(anomalies)} cost anomaly(ies), {len(flagged)} flagged warehouse(s):"]
    for a in anomalies:
        lines.append(f"  - {a['warehouse']}: {a['latest_credits']} credits on {a['latest_day']} (+{a['pct_increase']}% vs trailing avg {a['trailing_avg_credits']})")
    for f_ in flagged:
        lines.append(f"  - {f_['warehouse']}: {f_['issue']}")
    if errors:
        lines.append(f"  - partial data: {list(errors.keys())}")
    finding_text = "\n".join(lines)

    log_line(f"FINDING: {finding_text}")
    post_to_slack(finding_text)


if __name__ == "__main__":
    main()
