#!/usr/bin/env python3
"""
Unit tests for job_deploy's pure-logic functions -- spec validation and
the early-escalation decision. The real deploy/run/retry loop was
proven live against a real Databricks workspace instead (a success
case converging in 1 attempt, and a deterministic-failure case
correctly stopping early after 2 identical errors rather than
exhausting the retry cap).

Run: python test_job_deploy.py   (also picked up by tools/ci/unit_tests.py)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from job_deploy import load_and_validate_spec, should_stop_early  # noqa: E402

failures = []


def check(name, condition):
    if not condition:
        failures.append(name)


def write_spec(obj):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(obj, f)
    f.close()
    return f.name


# --- load_and_validate_spec ---
valid = write_spec({"tasks": [{"task_key": "a"}, {"task_key": "b"}]})
spec, errors = load_and_validate_spec(valid)
check("a valid spec with 2 tasks passes with zero errors", errors == [])
check("the parsed spec has the right task count", len(spec["tasks"]) == 2)
os.unlink(valid)

missing_key = write_spec({"tasks": [{"job_cluster_key": "x"}]})
spec, errors = load_and_validate_spec(missing_key)
check("a task missing task_key is flagged", len(errors) == 1 and "task_key" in errors[0])
os.unlink(missing_key)

empty_tasks = write_spec({"tasks": []})
spec, errors = load_and_validate_spec(empty_tasks)
check("an empty tasks list is flagged", any("non-empty" in e for e in errors))
os.unlink(empty_tasks)

not_a_dict = write_spec(["not", "a", "dict"])
spec, errors = load_and_validate_spec(not_a_dict)
check("a spec that isn't a JSON object is flagged", any("JSON object" in e for e in errors))
os.unlink(not_a_dict)

multi_error = write_spec({"tasks": [{"task_key": "ok"}, {"job_cluster_key": "x"}, {}]})
spec, errors = load_and_validate_spec(multi_error)
check("multiple invalid tasks are ALL reported, not just the first", len(errors) == 2)

# --- should_stop_early: the real escalation rule found live ---
check("identical consecutive errors trigger early stop", should_stop_early("boom", "boom") is True)
check("different errors do NOT trigger early stop (retry is still worth trying)", should_stop_early("boom", "crash") is False)
check("the first attempt (no prior error) never triggers early stop", should_stop_early("boom", None) is False)

if failures:
    print("FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("All job_deploy unit tests passed.")
sys.exit(0)
