#!/usr/bin/env python3
"""
aws-cost-report helper script.

Pulls a Cost Explorer summary: daily cost by service over a lookback
window, a month-over-month comparison, and day-over-day cost anomalies
per service. Uses boto3's normal credential chain (a named profile via
--profile, or AWS_* environment variables) -- never accepts or stores
credentials directly.

Cost Explorer's API only has an endpoint in us-east-1, regardless of
which region the account's actual resources run in -- the client is
pinned there deliberately, not a mistake.

Anomaly detection carries a minimum-dollar floor from the start (see
--min-anomaly-dollars): a service going from $0.0001 to $0.0002 is a
"100% increase" that means nothing, the same failure mode found and
deferred in snowflake-cost-audit. Applying that lesson here rather than
rediscovering it.

Degrades gracefully: Cost Explorer must be explicitly enabled per-account
(a one-time console action, with up to a 24h data backfill delay after
first enabling) -- that failure is caught and reported in `errors`,
not raised as a crash.

Usage:
    python aws_cost_report.py [--profile NAME] [--days N] [--min-anomaly-dollars N]

Requires: boto3 (already installed in this environment).
"""
import argparse
import json
import sys
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

CE_REGION = "us-east-1"  # Cost Explorer has no other regional endpoint.


def get_client(profile_name):
    session = boto3.Session(profile_name=profile_name) if profile_name else boto3.Session()
    return session.client("ce", region_name=CE_REGION)


def fetch_section(errors, section_name, fetch_fn):
    """Run one Cost Explorer call; on failure record the real error and
    return None so the rest of the report still completes."""
    try:
        return fetch_fn()
    except (ClientError, NoCredentialsError) as e:
        errors[section_name] = str(e)[:300]
        return None


def get_daily_cost_by_service(client, days):
    end = date.today()
    start = end - timedelta(days=days)
    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    rows = []
    for day in resp["ResultsByTime"]:
        day_start = day["TimePeriod"]["Start"]
        for group in day["Groups"]:
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amount == 0:
                continue
            rows.append({
                "usage_day": day_start,
                "service": group["Keys"][0],
                "unblended_cost": round(amount, 6),
            })
    return rows


def get_month_over_month(client):
    """Compares month-to-date against the SAME number of days at the
    start of the prior month -- a full-month vs. partial-month comparison
    would be misleading, so this deliberately aligns the day count instead
    of comparing a full prior month to a partial current one."""
    today = date.today()
    days_elapsed = today.day  # 1-indexed day of month == days elapsed so far

    this_month_start = today.replace(day=1)
    prior_month_end_day = this_month_start - timedelta(days=1)
    prior_month_start = prior_month_end_day.replace(day=1)
    prior_month_comparable_end = prior_month_start + timedelta(days=days_elapsed)

    prior_partial_resp = client.get_cost_and_usage(
        TimePeriod={"Start": prior_month_start.isoformat(), "End": prior_month_comparable_end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    this_month_resp = client.get_cost_and_usage(
        TimePeriod={"Start": this_month_start.isoformat(), "End": today.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )

    def total_of(resp_obj):
        return sum(float(r["Total"]["UnblendedCost"]["Amount"]) for r in resp_obj["ResultsByTime"])

    this_month_cost = total_of(this_month_resp)
    prior_partial_cost = total_of(prior_partial_resp)
    pct_change = None
    if prior_partial_cost > 0:
        pct_change = round(((this_month_cost / prior_partial_cost) - 1) * 100, 1)

    return {
        "this_month_start": this_month_start.isoformat(),
        "days_elapsed_this_month": days_elapsed,
        "this_month_cost_to_date": round(this_month_cost, 4),
        "prior_month_same_day_range": {
            "start": prior_month_start.isoformat(),
            "end": prior_month_comparable_end.isoformat(),
        },
        "prior_month_comparable_cost": round(prior_partial_cost, 4),
        "pct_change_vs_prior_month_same_period": pct_change,
    }


def detect_service_anomalies(daily_rows, threshold_pct=50, min_anomaly_dollars=1.0):
    """Flags a service whose most recent day is more than threshold_pct
    above its trailing average AND whose latest-day cost is at least
    min_anomaly_dollars -- the dollar floor exists specifically so a
    service going from $0.0001 to $0.0002 doesn't register as a "100%
    increase" that means nothing. Mirrors snowflake-cost-audit's
    percentage-only check, but closes the gap that check left open."""
    by_service = {}
    for row in daily_rows:
        by_service.setdefault(row["service"], []).append(row)

    anomalies = []
    for service, rows in by_service.items():
        rows_sorted = sorted(rows, key=lambda r: r["usage_day"])
        if len(rows_sorted) < 2:
            continue
        *prior, latest = rows_sorted
        prior_values = [r["unblended_cost"] for r in prior]
        trailing_avg = sum(prior_values) / len(prior_values) if prior_values else 0
        latest_value = latest["unblended_cost"]
        if latest_value < min_anomaly_dollars:
            continue
        if trailing_avg > 0 and latest_value > trailing_avg * (1 + threshold_pct / 100):
            pct_increase = round(((latest_value / trailing_avg) - 1) * 100, 1)
            anomalies.append({
                "service": service,
                "latest_day": latest["usage_day"],
                "latest_cost": round(latest_value, 4),
                "trailing_avg_cost": round(trailing_avg, 4),
                "pct_increase": pct_increase,
            })
        elif trailing_avg == 0 and latest_value >= min_anomaly_dollars:
            # A brand-new cost with no prior history at all -- worth
            # surfacing distinctly from a pct-based anomaly, since there's
            # no "trailing average" to compare against.
            anomalies.append({
                "service": service,
                "latest_day": latest["usage_day"],
                "latest_cost": round(latest_value, 4),
                "trailing_avg_cost": 0.0,
                "pct_increase": None,
                "note": "new cost with no prior activity in this window",
            })
    return sorted(anomalies, key=lambda a: a["latest_cost"], reverse=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Named AWS CLI profile (boto3 credential chain)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-anomaly-dollars", type=float, default=1.0,
                        help="Minimum latest-day cost (USD) for a service to be eligible as an anomaly")
    args = parser.parse_args()

    try:
        client = get_client(args.profile)
    except Exception as e:
        print(json.dumps({"error": f"could not create Cost Explorer client: {e}"}), file=sys.stderr)
        sys.exit(1)

    errors = {}

    daily_rows = fetch_section(errors, "daily_cost_by_service",
                               lambda: get_daily_cost_by_service(client, args.days))
    month_over_month = fetch_section(errors, "month_over_month",
                                     lambda: get_month_over_month(client))

    total_cost = sum(r["unblended_cost"] for r in (daily_rows or []))
    anomalies = detect_service_anomalies(daily_rows or [], min_anomaly_dollars=args.min_anomaly_dollars) if daily_rows is not None else []

    output = {
        "window_days": args.days,
        "region_note": "Cost Explorer has no regional scope of its own -- API calls are pinned to us-east-1 regardless of where resources actually run.",
        "scope_note": "Costs reflect only what this IAM principal's Cost Explorer access can see -- under AWS Organizations consolidated billing, a linked-account principal may see a narrower scope than the full org.",
        "min_anomaly_dollars": args.min_anomaly_dollars,
        "total_unblended_cost": round(total_cost, 4),
        "daily_cost_by_service": daily_rows or [],
        "cost_anomalies": anomalies,
        "month_over_month": month_over_month,
        "errors": errors,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
