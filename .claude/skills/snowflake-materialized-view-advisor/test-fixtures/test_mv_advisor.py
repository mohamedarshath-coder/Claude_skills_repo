#!/usr/bin/env python3
"""
Unit tests for mv_advisor's pure-logic function -- check_mv_eligibility.
The SQL-based detection (find_candidates) and the write-confirm/loop
path (apply_materialized_view, verify_improvement) are proven live
against a real account instead, per this repo's convention of not
unit-testing pure SQL orchestration.

Run: python test_mv_advisor.py   (also picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from mv_advisor import check_mv_eligibility  # noqa: E402

failures = []


def check(name, condition):
    if not condition:
        failures.append(name)


# --- Eligible case: a genuinely simple, single-table, deterministic query ---
eligible, reasons = check_mv_eligibility("SELECT REGION, SUM(AMOUNT) AS TOTAL FROM RAW.SALES GROUP BY REGION")
check("a simple single-table GROUP BY aggregate is eligible", eligible is True)
check("an eligible query has zero ineligibility reasons", reasons == [])

# --- Each real restriction, checked independently ---
elig, reasons = check_mv_eligibility("SELECT * FROM information_schema.columns WHERE table_name = 'X'")
check("a system-schema query is correctly flagged ineligible", elig is False)
check("system-schema reason is present", any("system schema" in r for r in reasons))

elig, reasons = check_mv_eligibility("SELECT a.x FROM RAW.A a JOIN RAW.B b ON a.id = b.id")
check("a JOIN query is correctly flagged ineligible", elig is False)
check("JOIN reason is present", any("JOIN" in r for r in reasons))

elig, reasons = check_mv_eligibility("SELECT * FROM RAW.SALES WHERE ts > CURRENT_TIMESTAMP()")
check("a non-deterministic function is correctly flagged ineligible", elig is False)
check("non-deterministic reason is present", any("non-deterministic" in r for r in reasons))

elig, reasons = check_mv_eligibility("SELECT * FROM RAW.SALES ORDER BY amount LIMIT 10")
check("ORDER BY / LIMIT is correctly flagged ineligible", elig is False)
check("disallowed-clause reason is present", any("ORDER BY" in r for r in reasons))

elig, reasons = check_mv_eligibility("SELECT amount, SUM(amount) OVER (PARTITION BY region) FROM RAW.SALES")
check("a window function is correctly flagged ineligible", elig is False)
check("window-function reason is present", any("window function" in r for r in reasons))

elig, reasons = check_mv_eligibility("")
check("empty/masked query text is correctly flagged ineligible", elig is False)
check("empty-text reason is present", any("empty" in r or "masked" in r for r in reasons))

elig, reasons = check_mv_eligibility(None)
check("None query text does not crash and is correctly flagged ineligible", elig is False)

# --- Compound case: multiple real restrictions violated at once ---
compound = "SELECT a.x FROM RAW.A a JOIN RAW.B b ON a.id=b.id WHERE ts > CURRENT_TIMESTAMP() ORDER BY a.x LIMIT 5"
elig, reasons = check_mv_eligibility(compound)
check("a query violating multiple restrictions reports ALL of them, not just the first", len(reasons) >= 3)

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All mv_advisor unit tests passed.")
sys.exit(0)
