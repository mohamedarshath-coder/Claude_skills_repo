#!/usr/bin/env python3
"""
Unit tests for aws_cost_report's pure-logic functions -- specifically
detect_service_anomalies, since the account's Cost Explorer data was
still backfilling (first-enable delay, up to 24h) when this skill was
built, so no real anomaly has fired live yet.

Covers the exact gap this skill deliberately closed relative to
snowflake-cost-audit: a near-zero cost swinging by a huge percentage
must NOT be flagged, because of the min_anomaly_dollars floor.

Run: python test_aws_cost_report.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from aws_cost_report import detect_service_anomalies  # noqa: E402

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def day(n, service, cost):
    return {"usage_day": f"2026-07-{n:02d}", "service": service, "unblended_cost": cost}


# 1. Real anomaly: meaningful trailing average, real percentage jump, above the dollar floor.
rows = [day(1, "EC2", 10.0), day(2, "EC2", 10.5), day(3, "EC2", 30.0)]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("real anomaly detected", len(anomalies) == 1 and anomalies[0]["service"] == "EC2")
check("real anomaly pct_increase computed", anomalies[0]["pct_increase"] > 100)

# 2. The exact gap this skill was built to close: a near-zero cost swinging
# by a huge percentage must be suppressed by the dollar floor.
rows = [day(1, "AWS Support (Business)", 0.00003), day(2, "AWS Support (Business)", 0.00003),
        day(3, "AWS Support (Business)", 0.00006)]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("near-zero swing suppressed by dollar floor", len(anomalies) == 0)

# 3. Same shape, but with the floor lowered below the actual value -- should now fire.
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=0.00001)
check("same data flags when floor is lowered below it", len(anomalies) == 1)

# 4. Brand-new cost with no prior history at all (trailing_avg == 0) -- distinct code path.
rows = [day(1, "SageMaker", 5.0)]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("single-day service (no trailing average) is skipped, not divided by zero", len(anomalies) == 0)

# 5. Two services, one anomalous and one not -- independence between services.
rows = [
    day(1, "EC2", 10.0), day(2, "EC2", 11.0),          # stable, not anomalous
    day(1, "Lambda", 2.0), day(2, "Lambda", 8.0),        # real jump, above floor
]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("only the anomalous service is flagged", [a["service"] for a in anomalies] == ["Lambda"])

# 6. Clean case: no anomalies at all -- must return an empty list, not None or a crash.
rows = [day(1, "S3", 3.0), day(2, "S3", 3.1), day(3, "S3", 3.2)]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("clean/stable case returns empty list", anomalies == [])

# 7. Exact-threshold boundary: exactly 50% increase should NOT fire (check is strictly >).
rows = [day(1, "RDS", 10.0), day(2, "RDS", 10.0), day(3, "RDS", 15.0)]
anomalies = detect_service_anomalies(rows, threshold_pct=50, min_anomaly_dollars=1.0)
check("exactly-50%-increase boundary does not fire", anomalies == [])

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All aws_cost_report unit tests passed.")
sys.exit(0)
