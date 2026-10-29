"""Strict provider acceptance for managed targets with no in-cluster work."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nebius.api.nebius.mk8s.v1 import (
    ClusterServiceClient,
    ClusterStatus,
    GetClusterRequest,
    ListNodeGroupsRequest,
    NodeGroupServiceClient,
    NodeGroupStatus,
)

from .cluster_config import cluster_rows
from .component_instances import component_instance_id
from .deploy_targets import app_chart_target_ref, deploy_target_is_external_mk8s
from .deploy_validation_report import build_deploy_validation_report
from .mk8s_node_groups import cluster_name, cluster_public_endpoint_enabled, iter_node_groups
from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk


def infrastructure_component(config: Any, target_ref: str) -> Mapping[str, Any] | None:
    """Classify desired work, never endpoint reachability or a failed kubectl call."""
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return None
    components = [
        row
        for row in payload.get("infra", {}).get("components", [])
        if row.get("id") == "mk8s"
        and row.get("enabled", False)
        and component_instance_id(row) == target_ref
    ]
    if len(components) != 1:
        return None
    for row in payload.get("apps", {}).get("charts", []):
        if (
            row.get("enabled", False)
            and (app_chart_target_ref(row) or component_instance_id(row)) == target_ref
        ):
            return None
    for row in cluster_rows(payload):
        if row.get("instance_id") == target_ref and (
            deploy_target_is_external_mk8s(row)
            or row.get("observability", {}).get("enabled", False)
        ):
            return None
    if any(group.gpu for group in iter_node_groups(components[0].get("inputs", {}))):
        return None
    return components[0]


def infrastructure_only_target(
    config: Any,
    target: Mapping[str, Any],
    *,
    validations: Sequence[Mapping[str, Any]] = (),
    application_identity: Mapping[str, Any] | None = None,
    paths: Any = None,
) -> bool:
    ref = str(target["target_ref"])
    if (
        target.get("ownership") != "managed"
        or application_identity
        or infrastructure_component(config, ref) is None
        or any(
            row.get("kind") != "mk8s_cluster_smoke"
            for row in validations
            if row.get("target_ref") == ref
        )
    ):
        return False
    if paths is not None:
        from .deployment_local import LocalObjectStore
        from .deployment_state import DeploymentState
        from .ordinary_apps import BASELINE_FILENAME
        from .terraform_backend import backend_settings_from_config

        baseline = paths.reports_dir / BASELINE_FILENAME
        if baseline.exists():
            # Malformed prior ownership is an error, never proof of no apps.
            previous = json.loads(baseline.read_text())
            if previous.get("identities", {}).get(ref):
                return False
        # Completed local evidence can disqualify cloud-only acceptance, but
        # cannot establish current readiness or supply a physical binding.
        completed = DeploymentState(
            LocalObjectStore.for_project(paths),
            backend_settings_from_config(config),
            assert_held=lambda: None,
        ).latest_completed()
        evidence = (completed or {}).get("evidence", {})
        previous_target = evidence.get("targets", {}).get(ref, {})
        if evidence.get("identities", {}).get(ref) or (
            previous_target and not previous_target.get("infrastructure")
        ):
            return False
    return True


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("MK8s API returned incomplete node counts")
    return value


def _ready_snapshot(
    cluster: Any,
    groups: Sequence[Any],
    *,
    cluster_id: str,
    project_id: str,
    component: Mapping[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    inputs = component.get("inputs", {})
    if (
        cluster.metadata.id != cluster_id
        or cluster.metadata.parent_id != project_id
        or cluster.metadata.name != cluster_name(inputs, fallback=target_ref)
    ):
        raise RuntimeError("MK8s infrastructure immutable identity differs from Terraform intent")
    status = cluster.status
    if status.state == ClusterStatus.State.DELETING:
        raise RuntimeError("MK8s infrastructure cluster is deleting")
    endpoints = status.control_plane.endpoints
    public = cluster_public_endpoint_enabled(inputs)
    endpoint_ready = bool(endpoints.public_endpoint if public else endpoints.private_endpoint)
    if not public and endpoints.public_endpoint:
        raise RuntimeError("Private MK8s infrastructure unexpectedly has a public endpoint")
    # The shared input helper's fallback is a display label (the group key).
    # Terraform's resource name defaults to <cluster-name>-<group-key>.
    expected = {}
    for group in iter_node_groups(inputs):
        raw_name = inputs["node_groups"][group.key].get("name")
        name = str(raw_name or "").strip() or f"{cluster.metadata.name}-{group.key}"
        if name in expected:
            raise RuntimeError("MK8s Terraform intent has ambiguous node-group names")
        expected[name] = group
    observed: dict[str, Any] = {}
    ids: set[str] = set()
    for group in groups:
        if (
            not group.metadata.id
            or group.metadata.id in ids
            or group.metadata.name in observed
            or group.metadata.parent_id != cluster_id
        ):
            raise RuntimeError("MK8s API returned ambiguous node-group identity")
        ids.add(group.metadata.id)
        observed[group.metadata.name] = group
    if set(observed) - set(expected):
        raise RuntimeError("MK8s API node-group inventory differs from Terraform intent")
    checks = [
        {
            "name": "Cluster identity",
            "passed": True,
            "summary": "Cluster ID, project and name match Terraform intent.",
        },
        {
            "name": "Cluster readiness",
            "passed": status.state == ClusterStatus.State.RUNNING and status.reconciling is False,
            "summary": f"State: {status.state.name}; reconciling: {status.reconciling}.",
        },
        {
            "name": f"Configured {'public' if public else 'private'} endpoint",
            "passed": endpoint_ready,
            "summary": "Configured endpoint is present."
            if endpoint_ready
            else "Configured endpoint is unavailable.",
        },
        {
            "name": "Node-group inventory",
            "passed": set(observed) == set(expected),
            "summary": f"Observed {len(observed)} of {len(expected)} configured node groups.",
        },
    ]
    rows = []
    for name, desired in expected.items():
        if name not in observed:
            continue
        group = observed[name]
        current = group.status
        if current.state == NodeGroupStatus.State.DELETING:
            raise RuntimeError("MK8s infrastructure node group is deleting")
        total = _count(current.node_count)
        target = _count(current.target_node_count)
        available = _count(current.ready_node_count)
        outdated = _count(current.outdated_node_count)
        if desired.autoscaling_min_node_count is not None:
            upper = desired.autoscaling_max_node_count
            capacity_matches = upper is not None and (
                desired.autoscaling_min_node_count <= target <= upper
            )
        else:
            capacity_matches = desired.node_count is not None and target == desired.node_count
        group_ready = (
            current.state == NodeGroupStatus.State.RUNNING
            and current.reconciling is False
            and capacity_matches
            and available == total == target
            and outdated == 0
        )
        checks.append(
            {
                "name": f"Node-group readiness: {name}",
                "passed": group_ready,
                "summary": (
                    f"State: {current.state.name}; reconciling: {current.reconciling}; "
                    f"ready: {available}/{target}; total: {total}; outdated: {outdated}; "
                    f"configured capacity matches: {capacity_matches}."
                ),
            }
        )
        rows.append(
            {
                "id": group.metadata.id,
                "name": name,
                "ready": available,
                "target": target,
                "total": total,
                "outdated": outdated,
                "passed": group_ready,
            }
        )
    return {
        "cluster_id": cluster_id,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "endpoint": "public" if public else "private",
        "endpoint_ready": endpoint_ready,
        "expected_node_groups": len(expected),
        "node_groups": rows,
        "evidence_source": "nebius-api",
        "scope": "managed-infrastructure",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def wait_infrastructure_ready(
    *,
    cluster_id: str,
    project_id: str,
    component: Mapping[str, Any],
    target_ref: str,
    timeout: float = 600,
    poll_interval: float = 5,
    emit: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Require complete fresh provider evidence, with one bounded owned SDK client."""
    deadline = time.monotonic() + timeout
    try:
        sdk = init_nebius_sdk(
            parent_id=project_id,
            context="MK8s infrastructure readiness",
            require_renewable_auth=True,
        )
    except Exception:
        raise RuntimeError(
            "MK8s infrastructure authentication unavailable; readiness unverified"
        ) from None
    try:
        clusters, node_groups = ClusterServiceClient(sdk), NodeGroupServiceClient(sdk)

        def request(method: Any, value: Any) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MK8s infrastructure readiness timed out")
            try:
                return method(value, timeout=min(30, remaining)).wait()
            except Exception:
                raise RuntimeError(
                    "MK8s infrastructure API read failed; readiness unverified"
                ) from None

        while True:
            cluster = request(clusters.get, GetClusterRequest(id=cluster_id))
            groups: list[Any] = []
            token = ""
            seen: set[str] = set()
            for _ in range(100):
                page = request(
                    node_groups.list,
                    ListNodeGroupsRequest(parent_id=cluster_id, page_token=token, page_size=100),
                )
                groups.extend(page.items)
                token = page.next_page_token
                if not token:
                    break
                if token in seen:
                    raise RuntimeError("MK8s API returned repeated node-group pagination")
                seen.add(token)
            else:
                raise RuntimeError("MK8s API node-group inventory exceeded its page bound")
            proof = _ready_snapshot(
                cluster,
                groups,
                cluster_id=cluster_id,
                project_id=project_id,
                component=component,
                target_ref=target_ref,
            )
            if proof["passed"]:
                return proof
            emit("Waiting for Nebius API cluster and node-group readiness")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MK8s infrastructure readiness timed out")
            time.sleep(min(poll_interval, remaining))
    finally:
        sdk.sync_close(timeout=5)


def verify_infrastructure_target(
    cli: Any,
    config: Any,
    paths: Any,
    target: Mapping[str, Any],
    *,
    validations: Sequence[Mapping[str, Any]],
    expected_cluster_id: str = "",
    assert_authority: Callable[[], Any] = lambda: None,
) -> dict[str, Any]:
    """Resolve Terraform ownership, check the SDK, and write scoped required smoke evidence."""
    ref = str(target["target_ref"])
    component = infrastructure_component(config, ref)
    if component is None or target.get("ownership") != "managed":
        raise RuntimeError("Target requires Kubernetes application verification")
    assert_authority()
    output = str(target.get("cluster_id_output_name") or "")
    if not output:
        raise RuntimeError("MK8s infrastructure target has no Terraform cluster output")
    cluster_id = str(
        cli.terraform_output_raw(
            paths.infra_dir, output, extra_env=cli._terraform_runtime_env(config), initialize=False
        )
    ).strip()
    if not cluster_id or (expected_cluster_id and cluster_id != expected_cluster_id):
        raise RuntimeError("MK8s infrastructure cluster differs from accepted Terraform identity")
    project_id = str(to_plain_data(config)["client_info"]["nebius"]["project_id"])
    selected = [v for v in validations if v.get("target_ref") == ref]
    if any(v.get("kind") != "mk8s_cluster_smoke" for v in selected):
        raise RuntimeError("Target requires Kubernetes validation")
    report: dict[str, Any] = {
        "validation": "MK8s infrastructure readiness (Nebius API)",
        "kind": "mk8s_cluster_smoke",
        "target_ref": ref,
        "passed": False,
        "evidence_source": "nebius-api",
        "scope": "managed-infrastructure",
    }
    try:
        proof = wait_infrastructure_ready(
            cluster_id=cluster_id,
            project_id=project_id,
            component=component,
            target_ref=ref,
            emit=cli.progress_console.print,
        )
        assert_authority()
        report.update(proof)
        report["summary"] = (
            f"Nebius API: cluster RUNNING; {len(proof['node_groups'])} node groups and "
            f"{sum(row['ready'] for row in proof['node_groups'])} nodes ready. "
            "Kubernetes workloads were not inspected."
        )
    except Exception:
        report["error"] = "Nebius API infrastructure readiness could not be verified."
        raise
    finally:
        for spec in selected:
            name = str(spec.get("report_file") or "")
            if not name or Path(name).name != name:
                raise RuntimeError("Invalid infrastructure validation report filename")
            paths.reports_dir.mkdir(parents=True, exist_ok=True)
            (paths.reports_dir / name).write_text(json.dumps(report, indent=2) + "\n")
    # Accept the same serialized evidence that the final summary consumes.
    aggregate = build_deploy_validation_report(selected, reports_dir=paths.reports_dir)
    for result in aggregate.results:
        if result.status != "passed":
            raise RuntimeError(f"MK8s infrastructure validation report failed: {result.summary}")
    return proof
