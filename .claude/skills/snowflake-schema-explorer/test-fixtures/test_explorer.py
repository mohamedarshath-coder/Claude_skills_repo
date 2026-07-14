#!/usr/bin/env python3
"""
Unit tests for schema_explorer.profile_columns -- pins the case-sensitivity
fix found on the first live run: result-set keys come back lowercased (via
run_query's normalization of cursor.description), but the SQL aliases were
built from original-case column names, so every profiled value silently
came back None until the lookup was lowercased to match.

Run: python test_explorer.py   (picked up by tools/ci/unit_tests.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from schema_explorer import profile_columns  # noqa: E402


class StubCursor:
    """Mimics Snowflake's behavior: alias names come back in cursor.description
    exactly as written in SQL (uppercase, since Snowflake uppercases unless
    the alias itself is quoted -- ours ARE quoted, preserving original case)."""
    def execute(self, sql, params=None):
        # Emulate the real shape: quoted aliases preserve case; run_query lowercases them.
        self.description = [("TOTAL_ROWS",),
                            ("CUSTOMER_ID__null_count",), ("CUSTOMER_ID__distinct_count",),
                            ("Email__null_count",), ("Email__distinct_count",)]
        self._row = (6000, 0, 6000, 3, 5990)

    def fetchall(self):
        return [self._row]


def run_tests():
    failures = []

    def check(name, condition):
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name}")

    columns = [{"column_name": "CUSTOMER_ID"}, {"column_name": "Email"}]
    profile = profile_columns(StubCursor(), "S", "T", columns)

    check("total_rows populated", profile["total_rows"] == 6000)
    check("uppercase column's counts resolve (the live-caught bug: these were all None)",
          profile["columns"]["CUSTOMER_ID"] == {"null_count": 0, "distinct_count": 6000})
    check("mixed-case column's counts also resolve",
          profile["columns"]["Email"] == {"null_count": 3, "distinct_count": 5990})
    check("no column silently maps to None values",
          all(v["null_count"] is not None and v["distinct_count"] is not None
              for v in profile["columns"].values()))

    print(f"\n{len(failures)} failure(s) of 4 tests")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
