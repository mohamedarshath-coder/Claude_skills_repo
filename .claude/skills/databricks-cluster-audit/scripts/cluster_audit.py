#!/usr/bin/env python3
"""
databricks-cluster-audit helper script.

Flags concrete, evidence-backed cost/idle risks in all-purpose (UI/API)
clusters -- never job or pipeline clusters, whose lifecycle is managed
by the job/pipeline framework itself, not by autotermination settings
(flagging those would be a false positive).

Uses databricks-sdk's WorkspaceClient, which reads the connection from
DATABRICKS_HOST/DATABRICKS_TOKEN env vars or a named profile in
~/.databrickscfg -- each user configures locally. Never touches the
token directly.

Usage:
    python cluster_audit.py [--profile NAME] [--idle-threshold-minutes N] [--large-worker-threshold N]

Requires: databricks-sdk (already installed in this environment).
"""
import argparse
import json
import os
import sys

from databricks.sdk import WorkspaceClient

# Documented, fixed thresholds -- this is the actual "what counts as a
# problem" logic, same discipline as snowflake-query-optimizer.
DEFAULT_IDLE_THRESHOLD_MINUTES = 120   # >2h auto-suspend on a UI cluster is a real idle-cost risk
DEFAULT_LARGE_WORKER_THRESHOLD = 10    # fixed (non-autoscaling) cluster this big warrants a second look

# Cluster sources whose lifecycle is controlled externally (by the job
# or DLT pipeline that created them) -- autotermination_minutes == 0 is
# NORMAL for these and must never be flagged.
EXTERNALLY_MANAGED_SOURCES = {"JOB", "PIPELINE"}


def source_name(cluster_source):
    """cluster_source comes back as an enum (ClusterSource.UI); normalize to the plain string."""
    if cluster_source is None:
        return None
    return getattr(cluster_source, "value", str(cluster_source)).upper()


def state_name(state):
    if state is None:
        return None
    return getattr(state, "value", str(state)).upper()


def diagnose(cluster, idle_threshold, large_worker_threshold):
    issues = []
    src = source_name(cluster.cluster_source)
    state = state_name(cluster.state)
    autoterm = cluster.autotermination_minutes

    if state == "RUNNING":
        issues.append({
            "issue": "currently_running",
            "severity": "info",
            "evidence": f"cluster is RUNNING right now (source: {src})",
            "recommendation": "Actively costing compute. Confirm this is expected, or terminate if left on by mistake.",
        })

    if src not in EXTERNALLY_MANAGED_SOURCES:
        if autoterm is None or autoterm == 0:
            issues.append({
                "issue": "no_autotermination",
                "severity": "high",
                "evidence": f"autotermination_minutes = {autoterm} on a {src}-created cluster",
                "recommendation": "This cluster has no auto-suspend and will run (and bill) indefinitely once started. Set an autotermination value.",
            })
        elif autoterm > idle_threshold:
            issues.append({
                "issue": "high_autotermination",
                "severity": "medium",
                "evidence": f"autotermination_minutes = {autoterm} ({autoterm / 60:.1f}h) on a {src}-created cluster",
                "recommendation": f"Auto-suspend is set well above {idle_threshold} min. Consider lowering it to reduce idle-billing risk, unless long-running interactive sessions are the norm here.",
            })

    num_workers = cluster.num_workers
    has_autoscale = cluster.autoscale is not None
    if not has_autoscale and num_workers is not None and num_workers >= large_worker_threshold:
        issues.append({
            "issue": "large_fixed_size_cluster",
            "severity": "medium",
            "evidence": f"{num_workers} fixed workers, no autoscale configured",
            "recommendation": "A large fixed-size cluster risks overprovisioning for variable workloads. Consider enabling autoscale with this as the max.",
        })

    return issues


def get_client(profile):
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--idle-threshold-minutes", type=int, default=DEFAULT_IDLE_THRESHOLD_MINUTES)
    parser.add_argument("--large-worker-threshold", type=int, default=DEFAULT_LARGE_WORKER_THRESHOLD)
    args = parser.parse_args()

    try:
        client = get_client(args.profile)
        clusters = list(client.clusters.list())
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    results = []
    source_counts = {}
    for c in clusters:
        src = source_name(c.cluster_source) or "UNKNOWN"
        source_counts[src] = source_counts.get(src, 0) + 1

        issues = diagnose(c, args.idle_threshold_minutes, args.large_worker_threshold)
        results.append({
            "cluster_id": c.cluster_id,
            "cluster_name": c.cluster_name,
            "cluster_source": src,
            "state": state_name(c.state),
            "node_type_id": c.node_type_id,
            "num_workers": c.num_workers,
            "autoscale": {
                "min_workers": c.autoscale.min_workers,
                "max_workers": c.autoscale.max_workers,
            } if c.autoscale else None,
            "autotermination_minutes": c.autotermination_minutes,
            "issues": issues,
        })

    output = {
        "total_clusters": len(results),
        "clusters_by_source": source_counts,
        "clusters_with_issues": sum(1 for r in results if r["issues"]),
        "results": results,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
