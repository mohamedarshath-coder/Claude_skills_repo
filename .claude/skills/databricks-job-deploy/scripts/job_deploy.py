#!/usr/bin/env python3
"""
databricks-job-deploy helper script.

Creates or updates a Databricks job from a validated JSON spec (the
Jobs API's own task/cluster shape), then triggers a real run and
retries -- genuinely, not just resubmitting blindly -- until it
succeeds, the same real error repeats twice in a row (a strong signal
of a deterministic bug, not a transient failure -- stop and escalate
rather than waste the retry budget), or a hard cap is hit.

Auth: uses databricks-sdk's WorkspaceClient, which reads DATABRICKS_HOST /
DATABRICKS_TOKEN env vars (or a named profile in ~/.databrickscfg) that
each user configures locally. Never touches credentials directly.

Safety design (write-confirm, retry-until-resolved):
  - DRY RUN BY DEFAULT. Without --execute, only validates the spec and
    reports what would be created/updated -- touches nothing in the
    workspace.
  - Each retry is a genuinely new real run, never a cached/assumed
    result -- the Task Loop re-checks real ground truth (the run's
    actual terminal state) every time, the same discipline as every
    other Task Loop skill in this repo.
  - Early escalation, not blind retrying: if the SAME real error message
    repeats on a retry, that's evidence the failure is deterministic,
    not transient -- retrying again would not "change something" (the
    loop-engineering rule every retry must satisfy), so the loop stops
    immediately rather than exhausting --max-retries on a hopeless case.
  - Full audit log: every attempt, its real run_id, its real outcome,
    and a real timestamp.
  - Never deletes or destroys anything -- only creates/updates the one
    named job and triggers runs on it.

Usage:
    python job_deploy.py --job-name NAME --spec-file PATH
        [--profile NAME] [--execute] [--max-retries N]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs as jobs_api
from databricks.sdk.service import compute as compute_api

TERMINAL_STATES = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}
POLL_INTERVAL_SECONDS = 15
POLL_TIMEOUT_SECONDS = 900

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def get_client(profile):
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def load_and_validate_spec(spec_path):
    """Minimal structural validation -- not a full Databricks Jobs API
    schema validator, just enough to catch an obviously malformed spec
    before ever calling the API: must be a JSON object with a non-empty
    'tasks' list, and each task must have a task_key."""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    errors = []
    if not isinstance(spec, dict):
        errors.append("spec file must contain a JSON object")
        return spec, errors
    tasks = spec.get("tasks")
    if not tasks or not isinstance(tasks, list):
        errors.append("spec must have a non-empty 'tasks' list")
    else:
        for i, t in enumerate(tasks):
            if not t.get("task_key"):
                errors.append(f"tasks[{i}] is missing a task_key")
    return spec, errors


def dict_to_task(task_dict):
    kwargs = dict(task_dict)
    if "notebook_task" in kwargs:
        kwargs["notebook_task"] = jobs_api.NotebookTask(**kwargs["notebook_task"])
    if "depends_on" in kwargs:
        kwargs["depends_on"] = [jobs_api.TaskDependency(**d) for d in kwargs["depends_on"]]
    return jobs_api.Task(**kwargs)


def dict_to_job_cluster(jc_dict):
    kwargs = dict(jc_dict)
    if "new_cluster" in kwargs:
        kwargs["new_cluster"] = compute_api.ClusterSpec(**kwargs["new_cluster"])
    return jobs_api.JobCluster(**kwargs)


def find_existing_job(client, job_name):
    for job in client.jobs.list(name=job_name):
        if job.settings and job.settings.name == job_name:
            return job.job_id
    return None


def create_or_update_job(client, job_name, spec):
    tasks = [dict_to_task(t) for t in spec["tasks"]]
    job_clusters = [dict_to_job_cluster(jc) for jc in spec.get("job_clusters", [])]

    existing_job_id = find_existing_job(client, job_name)
    if existing_job_id:
        client.jobs.reset(job_id=existing_job_id, new_settings=jobs_api.JobSettings(
            name=job_name, tasks=tasks, job_clusters=job_clusters or None,
        ))
        return existing_job_id, "updated"
    else:
        job = client.jobs.create(name=job_name, tasks=tasks, job_clusters=job_clusters or None)
        return job.job_id, "created"


def extract_failure_summary(client, run):
    """Same real-error-extraction approach as databricks-job-triage:
    pull the specific failing task's real error message, not just the
    top-level 'a task failed' message."""
    for task in (run.tasks or [run]):
        task_state = getattr(task, "state", run.state)
        result = getattr(getattr(task_state, "result_state", None), "value", None)
        if result != "FAILED":
            continue
        task_run_id = getattr(task, "run_id", run.run_id)
        try:
            output = client.jobs.get_run_output(run_id=task_run_id)
            error = output.error or ""
        except Exception as e:
            error = f"(could not fetch run output: {e})"
        return {"task_key": getattr(task, "task_key", None), "error_message": error}
    return {"task_key": None, "error_message": getattr(run.state, "state_message", None) or "unknown failure"}


def wait_for_terminal_state(client, run_id, timeout_seconds=POLL_TIMEOUT_SECONDS):
    start = time.time()
    while time.time() - start < timeout_seconds:
        run = client.jobs.get_run(run_id=run_id)
        if str(run.state.life_cycle_state).split(".")[-1] in TERMINAL_STATES:
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    return client.jobs.get_run(run_id=run_id)


def should_stop_early(current_error_message, last_error_message):
    """The early-escalation rule: retrying only makes sense if something
    could plausibly be different this time. An identical real error
    message across two attempts is strong evidence the failure is
    deterministic (a real code bug), not transient (a flaky cluster
    provision, a network blip) -- continuing would violate this repo's
    own loop-engineering rule that each retry must change something."""
    return last_error_message is not None and current_error_message == last_error_message


def run_until_resolved(client, job_id, max_retries):
    """Task Loop: each attempt triggers a genuinely new run and waits
    for its real terminal state. Stops early -- before max_retries --
    if the exact same real error repeats, since a repeat with nothing
    changed violates the 'each retry must change something' rule this
    repo's loop-engineering rules require."""
    audit_log = []
    last_error_message = None

    for attempt in range(1, max_retries + 1):
        run = client.jobs.run_now(job_id=job_id)
        run_id = run.run_id
        final_run = wait_for_terminal_state(client, run_id)
        result_state = getattr(final_run.state.result_state, "value", None)

        entry = {
            "attempt": attempt,
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "result_state": result_state,
        }

        if result_state == "SUCCESS":
            entry["outcome"] = "success"
            audit_log.append(entry)
            return True, audit_log, None

        failure = extract_failure_summary(client, final_run)
        entry["outcome"] = "failed"
        entry["failed_task_key"] = failure["task_key"]
        entry["error_message"] = failure["error_message"]
        audit_log.append(entry)

        if should_stop_early(failure["error_message"], last_error_message):
            return False, audit_log, "identical_error_repeated_stopping_early"
        last_error_message = failure["error_message"]

    return False, audit_log, f"max_retries_reached ({max_retries})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--spec-file", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Actually create/update the job and run it. Without this flag, validates and plans only.")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    spec, spec_errors = load_and_validate_spec(args.spec_file)
    if spec_errors:
        print(json.dumps({"error": "invalid spec", "spec_errors": spec_errors}), file=sys.stderr)
        sys.exit(1)

    try:
        client = get_client(args.profile)
    except Exception as e:
        print(json.dumps({"error": f"could not create Databricks client: {e}"}), file=sys.stderr)
        sys.exit(1)

    output = {
        "job_name": args.job_name,
        "task_count": len(spec["tasks"]),
        "execute_mode": args.execute,
        "job_id": None,
        "action_taken": None,
        "converged": None,
        "stopped_reason": None,
        "audit_log": [],
    }

    if not args.execute:
        output["overall_status"] = "dry_run_validated"
        output["detail"] = f"Spec is structurally valid: {len(spec['tasks'])} task(s). Re-run with --execute to create/update the job and run it."
        print(json.dumps(output, indent=2, default=str))
        return

    try:
        job_id, action = create_or_update_job(client, args.job_name, spec)
        output["job_id"] = job_id
        output["action_taken"] = action
    except Exception as e:
        output["overall_status"] = "deploy_failed"
        output["detail"] = str(e)[:500]
        print(json.dumps(output, indent=2, default=str))
        sys.exit(1)

    converged, audit_log, stopped_reason = run_until_resolved(client, job_id, args.max_retries)
    output["converged"] = converged
    output["stopped_reason"] = stopped_reason
    output["audit_log"] = audit_log
    output["overall_status"] = "converged_success" if converged else "did_not_converge"
    if converged:
        output["detail"] = f"Job '{args.job_name}' ({action}) ran successfully after {len(audit_log)} attempt(s)."
    else:
        last = audit_log[-1]
        output["detail"] = (
            f"Did not converge after {len(audit_log)} attempt(s). Last failure: task '{last.get('failed_task_key')}' -- "
            f"{last.get('error_message')}. {'The same error repeated, so this is very likely a deterministic bug in the spec, not a transient failure -- fix the spec before retrying.' if stopped_reason == 'identical_error_repeated_stopping_early' else 'Investigate before re-running.'}"
        )

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
