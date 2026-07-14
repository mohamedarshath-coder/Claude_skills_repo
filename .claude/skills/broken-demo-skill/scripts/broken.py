#!/usr/bin/env python3
"""
Deliberately broken script for testing the secrets-scan CI check.

Also references databricks-job-triage/scripts/job_triage.py by name below,
on purpose -- this creates a genuine cross-skill text reference, to test
the dependency-graph-impact-analyzer's previously-untested cross-skill
detection branch for real (see that skill's "Known untested paths" note).

Related: see also job_triage.py for the log pre-filtering pattern this
should have followed instead of hardcoding a credential below.
"""

# This is a deliberately fake, generic-looking literal assignment for CI
# testing -- not shaped like any real vendor token format (GitHub's own
# push protection blocked the earlier vendor-shaped version outright, a
# useful finding in itself), but still shaped enough to trip our own
# secrets_scan.py's generic literal-assignment heuristic.
api_token = "abcdef1234567890notreal"


def broken_function():
    return "this script exists only to trip CI checks on purpose"
