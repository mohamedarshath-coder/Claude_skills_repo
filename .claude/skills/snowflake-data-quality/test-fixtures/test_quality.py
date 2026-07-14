#!/usr/bin/env python3
"""
Unit tests for data_quality's freshness verdict semantics -- the logic
that was fixed after the first live run flagged DATE_OF_BIRTH as "stale"
at 9328 days (a birth date is supposed to be old).

check_freshness needs a cursor for the MAX() query; stubbed here so the
verdict logic (the part that was actually wrong) is what's under test.

Run: python test_quality.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import data_quality  # noqa: E402


class StubCursor:
    """Feeds check_freshness a canned age per column."""
    def __init__(self, ages):
        self.ages = ages

    def execute(self, sql, params=None):
        self.description = [(f"{c}__AGE",) for c in self.ages]
        self._row = tuple(self.ages.values())

    def fetchall(self):
        return [self._row]


def freshness(ages, threshold=7, freshness_columns=None):
    cols = [{"column_name": c, "data_type": "DATE"} for c in ages]
    cur = StubCursor(ages)
    return data_quality.check_freshness(cur, "S", "T", cols, threshold, freshness_columns)


def verdicts(result):
    return {c["column"]: c["stale"] for c in result["columns"]}


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    # The live-caught semantic bug, pinned:
    v = verdicts(freshness({"DATE_OF_BIRTH": 9328, "LAST_LOGIN_DATE": 560, "UPDATED_AT": 2}))
    check("birth-date column is measured but NOT judged (stale=None)", v["DATE_OF_BIRTH"] is None)
    check("genuine recency column IS judged stale past threshold", v["LAST_LOGIN_DATE"] is True)
    check("fresh column judged ok", v["UPDATED_AT"] is False)

    v = verdicts(freshness({"DOB": 9000, "EVENT_DATE": 100}))
    check("'DOB' name variant also exempted", v["DOB"] is None)

    # Explicit --freshness-columns overrides everything:
    v = verdicts(freshness({"DATE_OF_BIRTH": 9328, "LAST_LOGIN_DATE": 560, "EVENT_DATE": 3},
                           freshness_columns=["EVENT_DATE"]))
    check("explicit freshness-columns: only the named column is judged",
          v["EVENT_DATE"] is False and v["LAST_LOGIN_DATE"] is None and v["DATE_OF_BIRTH"] is None)

    check("exactly-at-threshold is not stale (strictly greater-than)",
          verdicts(freshness({"EVENT_DATE": 7}))["EVENT_DATE"] is False)
    check("no date columns handled cleanly",
          data_quality.check_freshness(None, "S", "T", [{"column_name": "X", "data_type": "TEXT"}], 7)
          == {"date_columns_found": 0, "columns": [], "note": "no date/timestamp columns in this table"})

    print(f"\n{len(failures)} failure(s) of 7 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
