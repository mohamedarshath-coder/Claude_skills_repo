#!/usr/bin/env python3
"""
Unit tests for snowflake-cost-audit's never-live-fired branches:

1. detect_cost_anomalies' POSITIVE path -- every real run returned empty
   (spend has been low and stable); the one prior "FINDING" demo was a
   hand-crafted log line that bypassed this function entirely.
2. flag_idle_or_oversized's "possibly_oversized" branch -- every real
   warehouse seen is X-Small, so the big-size condition never fired.
3. scheduled_run.post_to_slack -- never executed against any webhook
   (SLACK_WEBHOOK_URL has never been configured). Tested here against a
   REAL local HTTP listener: the actual urllib POST is sent and the
   received payload is asserted -- not a mock of the request itself.

Run: python test_cost_audit.py   (picked up by tools/ci/unit_tests.py)
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
from cost_audit import detect_cost_anomalies, flag_idle_or_oversized  # noqa: E402
import scheduled_run  # noqa: E402


def day(n, credits, wh="WH_A"):
    return {"warehouse_name": wh, "usage_day": f"2026-07-{n:02d}", "credits_used": credits}


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # --- detect_cost_anomalies: positive path (never fired on real data) ---
    rows = [day(10, 1.0), day(11, 1.0), day(12, 1.2), day(13, 4.2)]
    anomalies = detect_cost_anomalies(rows)
    check("anomaly fires when latest day is far above trailing avg",
          len(anomalies) == 1 and anomalies[0]["warehouse"] == "WH_A")
    check("anomaly reports correct pct_increase (~294% for 4.2 vs avg 1.067)",
          anomalies and 280 <= anomalies[0]["pct_increase"] <= 300)
    check("no anomaly at exactly the 50% threshold (strictly greater-than)",
          detect_cost_anomalies([day(10, 1.0), day(11, 1.0), day(12, 1.5)]) == [])
    check("anomaly fires just above the 50% threshold",
          len(detect_cost_anomalies([day(10, 1.0), day(11, 1.0), day(12, 1.51)])) == 1)
    check("single-day warehouse is skipped (needs >=2 days)",
          detect_cost_anomalies([day(10, 99.0)]) == [])
    check("two warehouses evaluated independently",
          len(detect_cost_anomalies(
              [day(10, 1.0), day(11, 5.0)] + [day(10, 1.0, "WH_B"), day(11, 1.0, "WH_B")])) == 1)
    check("zero-usage prior days (trailing avg 0) do not divide-by-zero or flag",
          detect_cost_anomalies([day(10, 0), day(11, 3.0)]) == [])

    # --- materiality floor (found live 2026-07-15, deferred, now closed) ---
    # A warehouse going from 0.00003 to 0.00006 credits is technically a
    # >50% increase, but both numbers are financially meaningless -- the
    # exact real pattern that originally revealed this gap.
    near_zero_rows = [day(10, 0.00003), day(11, 0.00003), day(12, 0.00006)]
    check("near-zero swing is suppressed by the default materiality floor",
          detect_cost_anomalies(near_zero_rows) == [])
    check("the same near-zero swing fires once the floor is lowered below it",
          len(detect_cost_anomalies(near_zero_rows, min_anomaly_credits=0.00001)) == 1)
    check("a real, meaningful anomaly still fires with the default floor in place",
          len(detect_cost_anomalies([day(10, 1.0), day(11, 1.0), day(12, 4.2)], min_anomaly_credits=0.05)) == 1)

    # --- flag_idle_or_oversized: possibly_oversized branch (never fired live) ---
    config = [{"warehouse_name": "BIG_WH", "warehouse_size": "2X-LARGE", "auto_suspend": 60, "auto_resume": "true"}]
    activity = [{"warehouse_name": "BIG_WH", "avg_query_seconds": 3.0, "query_count": 500}]
    flags = flag_idle_or_oversized(config, activity)
    check("possibly_oversized fires: 2X-LARGE warehouse averaging 3s queries",
          any(f["issue"] == "possibly_oversized" for f in flags))
    check("X-Small warehouse with short queries is NOT flagged oversized",
          not any(f["issue"] == "possibly_oversized" for f in flag_idle_or_oversized(
              [{"warehouse_name": "SMALL_WH", "warehouse_size": "X-Small", "auto_suspend": 60}],
              [{"warehouse_name": "SMALL_WH", "avg_query_seconds": 3.0, "query_count": 500}])))
    check("LARGE warehouse with long-running queries (real work) is NOT flagged",
          not any(f["issue"] == "possibly_oversized" for f in flag_idle_or_oversized(
              [{"warehouse_name": "BUSY_WH", "warehouse_size": "LARGE", "auto_suspend": 60}],
              [{"warehouse_name": "BUSY_WH", "avg_query_seconds": 120.0, "query_count": 50}])))
    check("auto_suspend=None flags high_or_disabled",
          any(f["issue"] == "auto_suspend_high_or_disabled" for f in flag_idle_or_oversized(
              [{"warehouse_name": "W", "warehouse_size": "X-Small", "auto_suspend": None}], [])))

    # --- post_to_slack: real HTTP POST against a live local listener ---
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received["body"] = json.loads(self.rfile.read(length))
            received["content_type"] = self.headers.get("Content-Type")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass  # keep test output clean

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    os.environ["SLACK_WEBHOOK_URL"] = f"http://127.0.0.1:{port}/webhook"
    try:
        scheduled_run.post_to_slack("cost finding: WH_A +294% vs trailing avg")
        thread.join(timeout=5)
        check("post_to_slack sends a real HTTP POST with the finding as Slack-format JSON",
              received.get("body") == {"text": "cost finding: WH_A +294% vs trailing avg"})
        check("post_to_slack sets Content-Type application/json",
              received.get("content_type") == "application/json")
    finally:
        del os.environ["SLACK_WEBHOOK_URL"]
        server.server_close()

    check("post_to_slack is a silent no-op when SLACK_WEBHOOK_URL is unset",
          scheduled_run.post_to_slack("should go nowhere") is None)

    print(f"\n{len(failures)} failure(s) of 14 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
