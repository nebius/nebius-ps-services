"""Canonical read-only Soperator discovery bundle artifacts."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data
from .soperator_artifacts import (
    SoperatorClusterArtifactIdentity,
    soperator_cluster_artifact_identity,
    soperator_cluster_artifact_identity_from_snapshot,
    soperator_cluster_report_dir,
)

SOPERATOR_DISCOVERY_SCHEMA = "nebius-cxcli-soperator-discovery/v1"
SOPERATOR_DISCOVERY_MANIFEST_NAME = "manifest.json"
SOPERATOR_PUBLIC_DISCOVERY_SCHEMA = "nebius-cxcli.soperator-public-discovery.v2"
SOPERATOR_PUBLIC_DISCOVERY_DIR_NAME = "soperator-discovery"
SOPERATOR_PUBLIC_DISCOVERY_JSON_NAME = "report.json"
SOPERATOR_PUBLIC_DISCOVERY_MARKDOWN_NAME = "report.md"
SOPERATOR_PUBLIC_DISCOVERY_COMPLETE = "complete"
SOPERATOR_PUBLIC_DISCOVERY_PARTIAL = "partial"
SOPERATOR_PUBLIC_DISCOVERY_NOT_DETECTED = "not-detected"
SOPERATOR_DISCOVERY_SECTION_FILES = (
    "identity.json",
    "kubernetes.json",
    "slurm.json",
    "accounting.json",
    "customizations.json",
    "fingerprints.json",
    "findings.json",
    "summary.md",
)

_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "privatekey",
    "private_key",
    "clientkey",
    "client_key",
    "tls.key",
    "ca.crt",
    "certificate",
    "cert",
)


def _now_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sequence(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(value)


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalized_token(value: Any, default: str = "") -> str:
    return normalize_component_token(value) or default


def _text(value: Any) -> str:
    return str(value or "").strip()


_PUBLIC_DISCOVERY_SAFE_NODE_LABELS = frozenset(
    {
        "beta.kubernetes.io/os",
        "failure-domain.beta.kubernetes.io/region",
        "kubernetes.io/arch",
        "kubernetes.io/os",
        "nebius.com/node-group",
        "nebius.com/node-group-id",
        "node.kubernetes.io/instance-type",
        "nvidia.com/cuda.driver.major",
        "nvidia.com/cuda.driver.minor",
        "nvidia.com/cuda.driver.rev",
        "nvidia.com/cuda.runtime.major",
        "nvidia.com/cuda.runtime.minor",
        "nvidia.com/gpu.driver.major",
        "nvidia.com/gpu.driver.minor",
        "nvidia.com/gpu.driver.revision",
        "nvidia.com/gpu.product",
        "topology.kubernetes.io/region",
        "topology.kubernetes.io/zone",
        "topology.nebius.com/block",
        "topology.nebius.com/rack",
        "topology.nebius.com/region",
        "yandex.cloud/node-group-id",
    }
)


def _public_discovery_safe_node_observations(
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for node in _sequence_of_mappings(snapshot.get("kubernetes_nodes")):
        metadata = _mapping(node.get("metadata"))
        status = _mapping(node.get("status"))
        labels = _mapping(metadata.get("labels"))
        safe_labels = {
            str(key): _text(value)
            for key, value in sorted(labels.items())
            if str(key) in _PUBLIC_DISCOVERY_SAFE_NODE_LABELS and _text(value)
        }
        node_info = _mapping(status.get("nodeInfo"))
        safe_node_info = {
            key: _text(node_info.get(key))
            for key in ("kubeletVersion", "osImage", "kernelVersion", "containerRuntimeVersion")
            if _text(node_info.get(key))
        }
        observations.append(
            {
                "name": _text(metadata.get("name")),
                "labels": safe_labels,
                "node_info": safe_node_info,
            }
        )
    return observations


def soperator_public_discovery_snapshot_region(snapshot: Mapping[str, Any]) -> str:
    regions: set[str] = set()
    for node in _public_discovery_safe_node_observations(snapshot):
        labels = node.get("labels")
        if not isinstance(labels, Mapping):
            continue
        for key in (
            "topology.kubernetes.io/region",
            "topology.nebius.com/region",
            "failure-domain.beta.kubernetes.io/region",
        ):
            value = _text(labels.get(key))
            if value:
                regions.add(value)
    if len(regions) > 1:
        raise RuntimeError(
            "Soperator discovery found nodes from multiple Kubernetes regions: "
            + ", ".join(sorted(regions))
        )
    return next(iter(regions), "")


def _public_discovery_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _public_discovery_condition_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for condition in _sequence_of_mappings(value):
        condition_type = _text(condition.get("type"))
        if not condition_type:
            continue
        rows.append(
            {
                "type": condition_type,
                "status": _text(condition.get("status")) or None,
                "reason": _text(condition.get("reason")) or None,
            }
        )
    return sorted(rows, key=lambda row: (row["type"], row["status"] or "", row["reason"] or ""))


def _public_discovery_node_record(node: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(node.get("metadata"))
    spec = _mapping(node.get("spec"))
    status = _mapping(node.get("status"))
    labels = _mapping(metadata.get("labels"))
    safe_labels = {
        str(key): _text(value)
        for key, value in sorted(labels.items())
        if str(key) in _PUBLIC_DISCOVERY_SAFE_NODE_LABELS and _text(value)
    }
    node_info = _mapping(status.get("nodeInfo"))
    conditions = _public_discovery_condition_rows(status.get("conditions"))
    ready_condition = next((row for row in conditions if row["type"] == "Ready"), None)
    ready = None if ready_condition is None else ready_condition["status"] == "True"
    return {
        "name": _text(metadata.get("name")) or None,
        "uid": _text(metadata.get("uid")) or None,
        "labels": safe_labels,
        "unschedulable": spec.get("unschedulable") is True,
        "ready": ready,
        "conditions": conditions,
        "node_info": {
            key: _text(node_info.get(key))
            for key in (
                "containerRuntimeVersion",
                "kernelVersion",
                "kubeletVersion",
                "osImage",
            )
            if _text(node_info.get(key))
        },
    }


def _public_discovery_release_record(release: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (
            ("name", _text(release.get("name")) or None),
            ("namespace", _text(release.get("namespace")) or None),
            (
                "chart",
                _text(
                    release.get("chart")
                    or release.get("chart_name")
                    or release.get("chart_version")
                )
                or None,
            ),
            (
                "app_version",
                _text(release.get("app_version") or release.get("appVersion")) or None,
            ),
            ("status", _text(release.get("status")) or None),
            ("revision", _public_discovery_non_negative_int(release.get("revision"))),
        )
        if value is not None
    }


def _public_discovery_resource_record(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(resource.get("metadata"))
    status = _mapping(resource.get("status"))
    spec = _mapping(resource.get("spec"))
    record: dict[str, Any] = {
        "api_version": _text(resource.get("apiVersion")) or None,
        "kind": _text(resource.get("kind")) or None,
        "name": _text(metadata.get("name")) or None,
        "namespace": _text(metadata.get("namespace")) or None,
        "uid": _text(metadata.get("uid")) or None,
        "generation": _public_discovery_non_negative_int(metadata.get("generation")),
        "observed_generation": _public_discovery_non_negative_int(status.get("observedGeneration")),
        "conditions": _public_discovery_condition_rows(status.get("conditions")),
    }
    kind = _text(resource.get("kind"))
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "AdvancedStatefulSet"}:
        record["replicas"] = {
            "desired": _public_discovery_non_negative_int(spec.get("replicas")),
            "ready": _public_discovery_non_negative_int(status.get("readyReplicas")),
            "available": _public_discovery_non_negative_int(status.get("availableReplicas")),
            "updated": _public_discovery_non_negative_int(status.get("updatedReplicas")),
        }
    elif kind == "Pod":
        record["phase"] = _text(status.get("phase")) or None
    elif kind == "Job":
        record["counts"] = {
            key: _public_discovery_non_negative_int(status.get(key))
            for key in ("active", "succeeded", "failed")
        }
    elif kind == "Service":
        record["service_type"] = _text(spec.get("type")) or None
    elif kind in {"Secret", "ConfigMap"}:
        record["data_keys"] = sorted(
            _text(key) for key in _sequence(resource.get("data_keys")) if _text(key)
        )
        if kind == "Secret":
            record["secret_type"] = _text(resource.get("type")) or None
    claim_names = resource.get("pvc_claim_names")
    if isinstance(claim_names, Sequence) and not isinstance(claim_names, (str, bytes, bytearray)):
        record["pvc_claim_names"] = sorted(
            {_text(claim_name) for claim_name in claim_names if _text(claim_name)}
        )
    return record


def _public_discovery_storage_record(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(resource.get("metadata"))
    spec = _mapping(resource.get("spec"))
    status = _mapping(resource.get("status"))
    capacity = _mapping(status.get("capacity")) or _mapping(spec.get("capacity"))
    return {
        "kind": _text(resource.get("kind")) or None,
        "name": _text(metadata.get("name")) or None,
        "namespace": _text(metadata.get("namespace")) or None,
        "uid": _text(metadata.get("uid")) or None,
        "storage_class": _text(spec.get("storageClassName")) or None,
        "volume_mode": _text(spec.get("volumeMode")) or None,
        "access_modes": sorted(_text(item) for item in spec.get("accessModes", ()) if _text(item))
        if isinstance(spec.get("accessModes"), Sequence)
        and not isinstance(spec.get("accessModes"), (str, bytes, bytearray))
        else [],
        "phase": _text(status.get("phase")) or None,
        "capacity": {
            _text(key): _text(value)
            for key, value in sorted(capacity.items())
            if _text(key) and _text(value)
        },
    }


def _public_discovery_provider_group(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(group.get("id")) or None,
        "name": _text(group.get("name")) or None,
        "kubernetes_version": _text(group.get("kubernetes_version")) or None,
        "platform": _text(group.get("platform")) or None,
        "preset": _text(group.get("preset")) or None,
        "os": _text(group.get("os")) or None,
        "drivers_preset": _text(group.get("drivers_preset")) or None,
        "gpu": group.get("gpu") is True,
        "actual_node_count": _public_discovery_non_negative_int(group.get("actual_node_count")),
        "target_node_count": _public_discovery_non_negative_int(group.get("target_node_count")),
        "provider_ready_node_count": _public_discovery_non_negative_int(
            group.get("provider_ready_node_count")
        ),
        "outdated_node_count": _public_discovery_non_negative_int(group.get("outdated_node_count")),
        "state": _text(group.get("state")) or None,
        "reconciling": group.get("reconciling")
        if isinstance(group.get("reconciling"), bool)
        else None,
        "resource_version": _text(group.get("resource_version")) or None,
    }


def _public_discovery_record_sort_key(record: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _text(record.get("namespace")),
        _text(record.get("kind")),
        _text(record.get("name")),
        _text(record.get("uid")),
        _stable_json(record),
    )


def _public_discovery_is_gpu_operator_release(release: Mapping[str, Any]) -> bool:
    name = _text(release.get("name")).lower()
    chart = _text(release.get("chart")).lower()
    return name in {"gpu-operator", "nvidia-gpu-operator"} or bool(
        re.fullmatch(r"gpu-operator(?:[-_].+)?", chart)
    )


def _public_discovery_is_network_operator_release(release: Mapping[str, Any]) -> bool:
    name = _text(release.get("name")).lower()
    chart = _text(release.get("chart")).lower()
    return name in {"network-operator", "nvidia-network-operator"} or bool(
        re.fullmatch(r"network-operator(?:[-_].+)?", chart)
    )


def _public_discovery_claim_references(
    snapshot: Mapping[str, Any],
) -> set[tuple[str, str]]:
    references: set[tuple[str, str]] = set()

    def visit(value: Any, *, namespace: str) -> None:
        if isinstance(value, Mapping):
            persistent_volume_claim = _mapping(value.get("persistentVolumeClaim"))
            claim_name = _text(persistent_volume_claim.get("claimName"))
            if namespace and claim_name:
                references.add((namespace, claim_name))
            for nested in value.values():
                visit(nested, namespace=namespace)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for nested in value:
                visit(nested, namespace=namespace)

    for key in (
        "soperator_resources",
        "soperator_namespace_resources",
        "component_namespace_resources",
    ):
        for resource in _sequence_of_mappings(snapshot.get(key)):
            namespace = _text(_mapping(resource.get("metadata")).get("namespace"))
            claim_names = resource.get("pvc_claim_names")
            if isinstance(claim_names, Sequence) and not isinstance(
                claim_names, (str, bytes, bytearray)
            ):
                for claim_name in claim_names:
                    if namespace and _text(claim_name):
                        references.add((namespace, _text(claim_name)))
            visit(resource, namespace=namespace)
    return references


def _public_discovery_distribution(values: Sequence[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        normalized = _text(value) or "unknown"
        counts[normalized] = counts.get(normalized, 0) + 1
    return [{"value": value, "count": counts[value]} for value in sorted(counts)]


def _public_discovery_summary_value(values: Sequence[Mapping[str, Any]]) -> str:
    distinct = [_text(item.get("value")) for item in values if _text(item.get("value"))]
    if not distinct:
        return "unknown"
    sample = distinct[:3]
    suffix = f" +{len(distinct) - len(sample)}" if len(distinct) > len(sample) else ""
    return ", ".join(sample) + suffix


def _public_discovery_collection(
    snapshot: Mapping[str, Any],
    *,
    provider: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def safe_error_message(collector: str) -> str:
        return {
            "slurm-health": "Slurm health could not be checked.",
            "soperator-identity": "Soperator identity could not be resolved unambiguously.",
            "nebius-mk8s-node-groups": "Provider node-group inventory could not be collected.",
            "nebius-mk8s-node-group": "A provider node-group record could not be normalized.",
        }.get(collector, "A read-only inventory collector failed.")

    errors: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(snapshot.get("collection_errors")):
        raw_collector = _text(item.get("collector")).lower()
        collector = (
            raw_collector
            if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", raw_collector)
            else "read-only-collector"
        )
        severity = _text(item.get("severity")).lower()
        errors.append(
            {
                "collector": collector,
                "message": safe_error_message(collector),
                "severity": severity if severity in {"warning", "error"} else "warning",
            }
        )
    provided_lanes = _sequence_of_mappings(snapshot.get("collection_lanes"))
    lanes = [
        {
            "name": _text(lane.get("name")),
            "status": _text(lane.get("status")),
            "item_count": _public_discovery_non_negative_int(lane.get("item_count")),
        }
        for lane in provided_lanes
        if _text(lane.get("name"))
        and _text(lane.get("status")) in {"succeeded", "failed", "not-applicable"}
    ]
    provider_lanes = _sequence_of_mappings(provider.get("collection_lanes"))
    lanes.extend(
        {
            "name": _text(lane.get("name")),
            "status": _text(lane.get("status")),
            "item_count": _public_discovery_non_negative_int(lane.get("item_count")),
        }
        for lane in provider_lanes
        if _text(lane.get("name"))
        and _text(lane.get("status")) in {"succeeded", "failed", "not-applicable"}
    )
    deduplicated = {lane["name"]: lane for lane in lanes}
    return sorted(deduplicated.values(), key=lambda lane: lane["name"]), sorted(
        errors,
        key=lambda item: (item["collector"], item["message"], item["severity"]),
    )


def _public_discovery_component_summaries(
    *,
    snapshot: Mapping[str, Any],
    provider: Mapping[str, Any],
    soperator_releases: Sequence[Mapping[str, Any]],
    gpu_releases: Sequence[Mapping[str, Any]],
    network_releases: Sequence[Mapping[str, Any]],
    detected: bool,
    absence_conclusive: bool,
) -> list[dict[str, Any]]:
    def resolved(values: Sequence[Any], *, candidate_count: int) -> tuple[str, str]:
        candidates = sorted({_text(value) for value in values if _text(value)})
        if candidate_count > 1:
            return f"ambiguous ({candidate_count} candidates)", "ambiguous"
        if not candidates:
            return "unknown", "unknown"
        if len(candidates) == 1:
            return candidates[0], "observed"
        return f"ambiguous ({len(candidates)})", "ambiguous"

    chart, chart_state = resolved(
        [release.get("chart") for release in soperator_releases],
        candidate_count=len(soperator_releases),
    )
    app_version, app_state = resolved(
        [release.get("app_version") for release in soperator_releases],
        candidate_count=len(soperator_releases),
    )
    gpu_chart, gpu_state = resolved(
        [release.get("chart") for release in gpu_releases],
        candidate_count=len(gpu_releases),
    )
    network_chart, network_state = resolved(
        [release.get("chart") for release in network_releases],
        candidate_count=len(network_releases),
    )
    control_plane = _text(provider.get("control_plane_version")) or "unknown"
    gpu_applicable = any(
        group.get("gpu") is True or _text(group.get("drivers_preset"))
        for group in _sequence_of_mappings(provider.get("node_groups"))
    )
    if not gpu_applicable and not gpu_releases:
        gpu_chart, gpu_state = "not applicable", "not-applicable"
    if not gpu_applicable and not network_releases:
        network_chart, network_state = "not applicable", "not-applicable"
    jail = soperator_discovery_jail_rootfs_record(
        snapshot=snapshot,
        report={},
        target_versions=None,
    )
    jail_image = _text(jail.get("current_image") or jail.get("live_desired_image"))
    slurm_health = _mapping(snapshot.get("slurm_health"))
    slurm_checked = slurm_health.get("checked") is True
    slurm_healthy = slurm_health.get("healthy") is True
    slurm_state = (
        "healthy"
        if slurm_checked and slurm_healthy
        else "unhealthy"
        if slurm_checked
        else "not-checked"
    )
    absent_state = "not-detected" if absence_conclusive else "unknown"
    return [
        {
            "component": "Soperator chart",
            "value": chart,
            "state": chart_state if detected else absent_state,
            "classification": "kubernetes-observed" if chart != "unknown" else "unknown",
        },
        {
            "component": "Soperator app",
            "value": app_version,
            "state": app_state if detected else absent_state,
            "classification": "kubernetes-observed" if app_version != "unknown" else "unknown",
        },
        {
            "component": "Kubernetes control plane",
            "value": control_plane,
            "state": "configured" if control_plane != "unknown" else "unknown",
            "classification": "provider-configured" if control_plane != "unknown" else "unknown",
        },
        {
            "component": "GPU Operator",
            "value": gpu_chart,
            "state": gpu_state,
            "classification": "kubernetes-observed" if gpu_chart != "unknown" else "unknown",
        },
        {
            "component": "Network Operator",
            "value": network_chart,
            "state": network_state,
            "classification": "kubernetes-observed" if network_chart != "unknown" else "unknown",
        },
        {
            "component": "Jail image",
            "value": jail_image or "unknown",
            "state": "observed" if jail_image else "unknown",
            "classification": "kubernetes-observed" if jail_image else "unknown",
        },
        {
            "component": "Slurm",
            "value": slurm_state,
            "state": slurm_state,
            "classification": "runtime-observed" if slurm_checked else "unknown",
        },
        {
            "component": "GPU driver runtime",
            "value": "unknown" if gpu_applicable else "not applicable",
            "state": "not-probed" if gpu_applicable else "not-applicable",
            "classification": "unknown",
        },
        {
            "component": "CUDA runtime",
            "value": "unknown",
            "state": "not-probed",
            "classification": "unknown",
        },
    ]


def build_soperator_public_discovery_report(
    *,
    tenant_id: str,
    project_id: str,
    cluster_id: str,
    cluster_name: str,
    region_id: str,
    access: str,
    snapshot: Mapping[str, Any],
    provider: Mapping[str, Any],
) -> dict[str, Any]:
    provider_groups = sorted(
        (
            _public_discovery_provider_group(group)
            for group in _sequence_of_mappings(provider.get("node_groups"))
        ),
        key=lambda group: (group["id"] or "", group["name"] or "", _stable_json(group)),
    )
    nodes = sorted(
        (
            _public_discovery_node_record(node)
            for node in _sequence_of_mappings(snapshot.get("kubernetes_nodes"))
        ),
        key=lambda node: (node["uid"] or "", node["name"] or "", _stable_json(node)),
    )
    soperator_releases = sorted(
        (
            _public_discovery_release_record(release)
            for release in _sequence_of_mappings(snapshot.get("helm_releases"))
        ),
        key=lambda release: (
            release.get("namespace") or "",
            release.get("name") or "",
            release.get("chart") or "",
            _stable_json(release),
        ),
    )
    gpu_stack = _mapping(snapshot.get("gpu_stack"))
    gpu_releases = sorted(
        (
            _public_discovery_release_record(release)
            for release in _sequence_of_mappings(gpu_stack.get("helm_releases"))
        ),
        key=lambda release: (
            release.get("namespace") or "",
            release.get("name") or "",
            release.get("chart") or "",
            _stable_json(release),
        ),
    )
    soperator_resources = sorted(
        (
            _public_discovery_resource_record(resource)
            for resource in _sequence_of_mappings(snapshot.get("soperator_resources"))
        ),
        key=_public_discovery_record_sort_key,
    )
    component_resources = sorted(
        (
            _public_discovery_resource_record(resource)
            for key in ("soperator_namespace_resources", "component_namespace_resources")
            for resource in _sequence_of_mappings(snapshot.get(key))
        ),
        key=_public_discovery_record_sort_key,
    )
    gpu_policies = sorted(
        (
            _public_discovery_resource_record(policy)
            for policy in _sequence_of_mappings(gpu_stack.get("policies"))
        ),
        key=_public_discovery_record_sort_key,
    )
    detected = bool(soperator_releases or soperator_resources) or any(
        "slurm.nebius.ai" in _text(crd).lower()
        for crd in _sequence(snapshot.get("crds"))
        if isinstance(crd, str)
    )
    lanes, collection_errors = _public_discovery_collection(snapshot, provider=provider)
    snapshot_identity = _mapping(snapshot.get("cluster_identity"))
    kubernetes_uid = _text(snapshot_identity.get("kubernetes_uid"))

    provider_candidates_by_id: dict[str, list[dict[str, Any]]] = {}
    for group in provider_groups:
        group_id = _text(group.get("id"))
        if group_id:
            provider_candidates_by_id.setdefault(group_id, []).append(group)
    duplicate_provider_ids = {
        group_id
        for group_id, candidates in provider_candidates_by_id.items()
        if len(candidates) > 1
    }
    providers_by_id = {
        group_id: candidates[0]
        for group_id, candidates in provider_candidates_by_id.items()
        if len(candidates) == 1
    }
    providers_by_name: dict[str, list[dict[str, Any]]] = {}
    for group in provider_groups:
        name = _text(group.get("name"))
        if name:
            providers_by_name.setdefault(name, []).append(group)
    nodes_by_provider_id: dict[str, list[dict[str, Any]]] = {
        group_id: [] for group_id in providers_by_id
    }
    unmatched_nodes: list[dict[str, Any]] = []
    correlation_reasons: set[str] = (
        {"duplicate-provider-group-id"} if duplicate_provider_ids else set()
    )
    for node in nodes:
        labels = _mapping(node.get("labels"))
        group_ids = {
            value
            for key in ("nebius.com/node-group-id", "yandex.cloud/node-group-id")
            if (value := _text(labels.get(key)))
        }
        group_id = next(iter(group_ids), "") if len(group_ids) == 1 else ""
        group_name = _text(labels.get("nebius.com/node-group"))
        matched: dict[str, Any] | None = None
        reason = ""
        if len(group_ids) > 1:
            reason = "conflicting-provider-group-id-labels"
        elif group_id in duplicate_provider_ids:
            reason = "duplicate-provider-group-id"
        elif group_id:
            matched = providers_by_id.get(group_id)
            if matched is None:
                reason = "unknown-provider-group-id"
            elif group_name and _text(matched.get("name")) and group_name != matched["name"]:
                matched = None
                reason = "contradictory-provider-group-labels"
        elif group_name:
            candidates = providers_by_name.get(group_name, [])
            if len(candidates) == 1:
                matched = candidates[0]
            elif len(candidates) > 1:
                reason = "ambiguous-provider-group-name"
            else:
                reason = "unknown-provider-group-name"
        else:
            reason = "missing-provider-group-labels"
        if matched is None:
            node["correlation"] = {"status": "unmatched", "reason": reason}
            unmatched_nodes.append(node)
            correlation_reasons.add(reason)
        else:
            provider_group_id = _text(matched.get("id"))
            node["correlation"] = {
                "status": "matched",
                "provider_group_id": provider_group_id,
            }
            nodes_by_provider_id.setdefault(provider_group_id, []).append(node)

    def group_summary(
        group: Mapping[str, Any] | None,
        group_nodes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        node_info: list[Mapping[str, Any]] = [
            _mapping(node.get("node_info")) for node in group_nodes
        ]
        labels: list[Mapping[str, Any]] = [_mapping(node.get("labels")) for node in group_nodes]
        ready_values = [node.get("ready") for node in group_nodes]
        return {
            "id": _text(group.get("id")) if group else None,
            "name": _text(group.get("name")) if group else "unmatched",
            "correlation": "provider" if group else "unmatched",
            "counts": {
                "ready": sum(value is True for value in ready_values),
                "observed": len(group_nodes),
                "actual": group.get("actual_node_count") if group else None,
                "target": group.get("target_node_count") if group else None,
                "readiness_unknown": sum(value is None for value in ready_values),
                "unschedulable": sum(node.get("unschedulable") is True for node in group_nodes),
            },
            "kubernetes": {
                "configured": _text(group.get("kubernetes_version")) or None if group else None,
                "observed": _public_discovery_distribution(
                    [item.get("kubeletVersion") for item in node_info]
                ),
            },
            "os": {
                "configured": _text(group.get("os")) or None if group else None,
                "observed": _public_discovery_distribution(
                    [item.get("osImage") for item in node_info]
                ),
            },
            "gpu": {
                "preset": _text(group.get("drivers_preset")) or None if group else None,
                "products": _public_discovery_distribution(
                    [item.get("nvidia.com/gpu.product") for item in labels]
                ),
            },
        }

    node_group_summary = [
        group_summary(group, nodes_by_provider_id.get(_text(group.get("id")), ()))
        for group in provider_groups
    ]
    if unmatched_nodes:
        node_group_summary.append(group_summary(None, unmatched_nodes))
    node_group_summary.sort(
        key=lambda group: (
            group["correlation"] == "unmatched",
            _text(group.get("name")),
            _text(group.get("id")),
        )
    )

    slurm_health = _mapping(snapshot.get("slurm_health"))
    safe_slurm_health = {
        "checked": slurm_health.get("checked") is True,
        "healthy": slurm_health.get("healthy") is True,
        "reason": None
        if slurm_health.get("checked") is True and slurm_health.get("healthy") is True
        else "Slurm health check did not pass."
        if slurm_health.get("checked") is True
        else "Slurm health was not checked.",
    }
    detection_lane_names = {"kubernetes-crds", "soperator-resources", "soperator-helm"}
    detection_lanes = {lane["name"]: lane for lane in lanes if lane["name"] in detection_lane_names}
    conclusive_absence = set(detection_lanes) == detection_lane_names and all(
        lane["status"] in {"succeeded", "not-applicable"} for lane in detection_lanes.values()
    )
    gpu_operator_releases = [
        release for release in gpu_releases if _public_discovery_is_gpu_operator_release(release)
    ]
    network_operator_releases = [
        release
        for release in gpu_releases
        if _public_discovery_is_network_operator_release(release)
    ]
    component_summaries = _public_discovery_component_summaries(
        snapshot=snapshot,
        provider=provider,
        soperator_releases=soperator_releases,
        gpu_releases=gpu_operator_releases,
        network_releases=network_operator_releases,
        detected=detected,
        absence_conclusive=not detected and conclusive_absence,
    )
    incomplete_reasons: list[str] = []
    if collection_errors or any(lane["status"] == "failed" for lane in lanes):
        incomplete_reasons.append("one or more read-only collectors failed")
    if detected and not soperator_releases:
        incomplete_reasons.append(
            "Soperator resources were detected but no Helm release was observed"
        )
    if (
        detected
        and any(
            group.get("gpu") is True or _text(group.get("drivers_preset"))
            for group in provider_groups
        )
        and not gpu_operator_releases
    ):
        incomplete_reasons.append("GPU Operator Helm release was not observed for GPU nodes")
    if detected and not safe_slurm_health["checked"]:
        incomplete_reasons.append("Slurm health could not be checked")
    if any(component["state"] == "ambiguous" for component in component_summaries):
        incomplete_reasons.append("component version evidence is ambiguous")
    if unmatched_nodes:
        incomplete_reasons.append(
            f"{len(unmatched_nodes)} Kubernetes node(s) could not be correlated to a provider node group"
        )
    if duplicate_provider_ids:
        incomplete_reasons.append("provider node-group identities are not unique")
    if not region_id:
        incomplete_reasons.append("cluster region could not be derived")
    if not kubernetes_uid:
        incomplete_reasons.append("kube-system namespace UID was not observed")
    if set(detection_lanes) != detection_lane_names:
        incomplete_reasons.append("authoritative Soperator detection lanes are incomplete")
    incomplete_reasons = sorted(dict.fromkeys(incomplete_reasons))
    if not detected and conclusive_absence and not incomplete_reasons:
        status = SOPERATOR_PUBLIC_DISCOVERY_NOT_DETECTED
    else:
        status = (
            SOPERATOR_PUBLIC_DISCOVERY_PARTIAL
            if incomplete_reasons or not detected
            else SOPERATOR_PUBLIC_DISCOVERY_COMPLETE
        )

    topology = _mapping(snapshot.get("worker_topology_by_nodeset"))
    safe_topology = {
        _text(nodeset): {
            key: _public_discovery_non_negative_int(value)
            for key, value in sorted(values.items())
            if key in {"cpus", "boards", "sockets", "cores_per_socket", "threads_per_core"}
        }
        for nodeset, values in sorted(topology.items())
        if _text(nodeset) and isinstance(values, Mapping)
    }
    jail = soperator_discovery_jail_rootfs_record(
        snapshot=snapshot,
        report={},
        target_versions=None,
    )
    component_namespaces = sorted(
        {
            namespace
            for resource in (*soperator_resources, *component_resources, *gpu_policies)
            if (namespace := _text(resource.get("namespace")))
        }
        | {
            namespace
            for release in (*soperator_releases, *gpu_releases)
            if (namespace := _text(release.get("namespace")))
        }
    )
    claim_references = _public_discovery_claim_references(snapshot)
    scoped_pvcs = [
        pvc
        for pvc in _sequence_of_mappings(snapshot.get("pvcs"))
        if (
            _text(_mapping(pvc.get("metadata")).get("namespace")),
            _text(_mapping(pvc.get("metadata")).get("name")),
        )
        in claim_references
    ]
    bound_persistent_volume_names = {
        name for pvc in scoped_pvcs if (name := _text(_mapping(pvc.get("spec")).get("volumeName")))
    }
    scoped_pvs = [
        pv
        for pv in _sequence_of_mappings(snapshot.get("pvs"))
        if _text(_mapping(pv.get("metadata")).get("name")) in bound_persistent_volume_names
        or (
            _text(_mapping(_mapping(pv.get("spec")).get("claimRef")).get("namespace")),
            _text(_mapping(_mapping(pv.get("spec")).get("claimRef")).get("name")),
        )
        in claim_references
    ]
    inventory = {
        "provider": {
            "control_plane_version": _text(provider.get("control_plane_version")) or None,
            "node_groups": provider_groups,
        },
        "kubernetes": {"nodes": nodes},
        "components": {
            "soperator_helm_releases": soperator_releases,
            "soperator_resources": soperator_resources,
            "namespace_resources": component_resources,
            "gpu_helm_releases": gpu_releases,
            "gpu_policies": gpu_policies,
            "namespaces": component_namespaces,
            "soperator_crds": sorted(
                _text(crd)
                for crd in _sequence(snapshot.get("crds"))
                if isinstance(crd, str)
                and _text(crd)
                and ("slurm.nebius.ai" in _text(crd).lower() or "soperator" in _text(crd).lower())
            ),
            "jail": {
                "current_image": _text(jail.get("current_image")) or None,
                "current_version": _text(jail.get("current_version")) or None,
                "current_source": _text(jail.get("current_source")) or None,
                "live_desired_image": _text(jail.get("live_desired_image")) or None,
                "live_desired_version": _text(jail.get("live_desired_version")) or None,
                "live_desired_source": _text(jail.get("live_desired_source")) or None,
                "evidence_status": _text(jail.get("current_evidence_status")) or None,
            },
        },
        "storage": {
            "persistent_volumes": sorted(
                (_public_discovery_storage_record(resource) for resource in scoped_pvs),
                key=_public_discovery_record_sort_key,
            ),
            "persistent_volume_claims": sorted(
                (_public_discovery_storage_record(resource) for resource in scoped_pvcs),
                key=_public_discovery_record_sort_key,
            ),
        },
        "topology": {"worker_nodesets": safe_topology},
        "health": {"slurm": safe_slurm_health},
    }
    summary = {
        "cluster": {
            "node_count": len(nodes),
            "ready_node_count": sum(node.get("ready") is True for node in nodes),
            "provider_node_group_count": len(provider_groups),
            "unmatched_node_count": len(unmatched_nodes),
        },
        "components": component_summaries,
        "node_groups": node_group_summary,
    }
    generated_at = _now_z()
    identity = {
        "tenant_id": _text(tenant_id),
        "project_id": _text(project_id),
        "cluster_id": _text(cluster_id),
        "cluster_name": _text(cluster_name),
        "region_id": _text(region_id) or "unknown",
        "kubernetes_uid": kubernetes_uid or "unknown",
        "soperator_namespace_uid": _text(snapshot_identity.get("soperator_uid")) or None,
        "slurmcluster_uid": _text(snapshot_identity.get("slurmcluster_uid")) or None,
        "access": _text(access),
    }
    summary_digest = _sha256_text(_stable_json(summary))
    report_id = _sha256_text(
        _stable_json(
            {
                "schema": SOPERATOR_PUBLIC_DISCOVERY_SCHEMA,
                "generated_at": generated_at,
                "identity": identity,
                "summary_digest": summary_digest,
                "inventory": inventory,
                "collection_lanes": lanes,
            }
        )
    )[:32]
    return {
        "schema": SOPERATOR_PUBLIC_DISCOVERY_SCHEMA,
        "report_id": report_id,
        "summary_digest": summary_digest,
        "generated_at": generated_at,
        "status": status,
        "information_only": True,
        "identity": identity,
        "summary": summary,
        "inventory": inventory,
        "collection": {
            "lanes": lanes,
            "errors": collection_errors,
            "incomplete_reasons": incomplete_reasons,
            "correlation_reasons": sorted(correlation_reasons),
        },
        "limitations": [
            "This report is information-only and is not registration or upgrade authority.",
            "Provider presets, image identities, Helm metadata, and Kubernetes labels do not prove exact runtime GPU driver or CUDA versions.",
            "No workload or Slurm job was created to collect this report.",
        ],
    }


_PUBLIC_DISCOVERY_MARKDOWN_LITERAL_CHARACTERS = frozenset("\\`*_{}[]()<>#+!|")
_PUBLIC_DISCOVERY_UNSAFE_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _public_discovery_terminal_safe_text(value: Any) -> str:
    text = _text(value)
    return "".join(
        f"\\u{ord(character):04x}"
        if unicodedata.category(character) in _PUBLIC_DISCOVERY_UNSAFE_UNICODE_CATEGORIES
        else character
        for character in text
    )


def _public_discovery_markdown_literal(value: Any) -> str:
    return "".join(
        f"\\{character}"
        if character in _PUBLIC_DISCOVERY_MARKDOWN_LITERAL_CHARACTERS
        else character
        for character in _public_discovery_terminal_safe_text(value)
    )


def _public_discovery_markdown_code(value: Any) -> str:
    text = _public_discovery_terminal_safe_text(value)
    longest_backtick_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)),
        default=0,
    )
    fence = "`" * max(1, longest_backtick_run + 1)
    padding = " " if longest_backtick_run else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _public_discovery_markdown(report: Mapping[str, Any], *, report_dir: Path) -> str:
    identity = _mapping(report.get("identity"))
    summary = _mapping(report.get("summary"))
    cluster = _mapping(summary.get("cluster"))
    collection = _mapping(report.get("collection"))
    cluster_name = _text(identity.get("cluster_name")) or _text(identity.get("cluster_id"))
    lines = [
        "# Soperator Discovery Report",
        "",
        f"- Status: {_public_discovery_markdown_code(_text(report.get('status')) or 'unknown')}",
        f"- Cluster: {_public_discovery_markdown_code(cluster_name)}",
        f"- Cluster ID: {_public_discovery_markdown_code(identity.get('cluster_id'))}",
        f"- Project ID: {_public_discovery_markdown_code(identity.get('project_id'))}",
        f"- Region: {_public_discovery_markdown_code(_text(identity.get('region_id')) or 'unknown')}",
        "- Nodes: "
        + _public_discovery_markdown_code(
            f"{cluster.get('ready_node_count', 0)}/{cluster.get('node_count', 0)} Ready"
        ),
        f"- Report ID: {_public_discovery_markdown_code(report.get('report_id'))}",
        "- Report directory: "
        + _public_discovery_markdown_code(
            f"{SOPERATOR_PUBLIC_DISCOVERY_DIR_NAME}/{report_dir.name}"
        ),
        "",
        "## Components",
        "",
        "| Component | Version / state | Evidence |",
        "| --- | --- | --- |",
    ]
    for component in summary.get("components", ()):
        if not isinstance(component, Mapping):
            continue
        value = _text(component.get("value")) or "unknown"
        state = _text(component.get("state"))
        value_and_state = value if not state or state == value else f"{value} ({state})"
        lines.append(
            "| "
            + " | ".join(
                _public_discovery_markdown_literal(cell)
                for cell in (
                    component.get("component"),
                    value_and_state,
                    component.get("classification"),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Node groups",
            "",
            "| Group | Ready / Actual / Target | K8s configured / observed | OS configured / observed | GPU preset / products |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for group in summary.get("node_groups", ()):
        if not isinstance(group, Mapping):
            continue
        counts = _mapping(group.get("counts"))
        kubernetes = _mapping(group.get("kubernetes"))
        operating_system = _mapping(group.get("os"))
        gpu = _mapping(group.get("gpu"))
        group_name = _text(group.get("name") or group.get("id")) or "unmatched"
        count_text = " / ".join(
            "?" if value is None else str(value)
            for value in (
                counts.get("ready"),
                counts.get("actual"),
                counts.get("target"),
            )
        )
        k8s_text = (
            f"{_text(kubernetes.get('configured')) or 'unknown'} / "
            f"{_public_discovery_summary_value(_sequence_of_mappings(kubernetes.get('observed')))}"
        )
        os_text = (
            f"{_text(operating_system.get('configured')) or 'unknown'} / "
            f"{_public_discovery_summary_value(_sequence_of_mappings(operating_system.get('observed')))}"
        )
        gpu_text = (
            f"{_text(gpu.get('preset')) or 'none'} / "
            f"{_public_discovery_summary_value(_sequence_of_mappings(gpu.get('products')))}"
        )
        lines.append(
            "| "
            + " | ".join(
                _public_discovery_markdown_literal(cell)
                for cell in (group_name, count_text, k8s_text, os_text, gpu_text)
            )
            + " |"
        )
    reasons = collection.get("incomplete_reasons")
    if (
        isinstance(reasons, Sequence)
        and not isinstance(reasons, (str, bytes, bytearray))
        and reasons
    ):
        lines.extend(["", "## Incomplete evidence", ""])
        visible_reasons = tuple(reason for reason in reasons if _text(reason))
        lines.extend(
            f"- {_public_discovery_markdown_literal(reason)}" for reason in visible_reasons[:5]
        )
        if len(visible_reasons) > 5:
            lines.append(
                f"- {_public_discovery_markdown_literal(f'+{len(visible_reasons) - 5} more in report.json')}"
            )
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- This report is information-only; onboarding re-collects live evidence independently.",
            "- No cloud or Kubernetes resource was mutated and no workload or Slurm job was created.",
            "- Runtime GPU driver and CUDA versions remain unknown unless separately runtime-tested.",
            "",
        ]
    )
    return "\n".join(lines)


def _public_discovery_fsync_directory(path: Path) -> None:
    with suppress(OSError):
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _public_discovery_pair_matches(json_path: Path, markdown_path: Path) -> bool:
    try:
        report_id = _text(json.loads(json_path.read_text(encoding="utf-8")).get("report_id"))
        markdown = markdown_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return False
    return bool(report_id) and f"- Report ID: `{report_id}`" in markdown.splitlines()


def write_soperator_public_discovery_report(
    output_root: Path,
    *,
    report: Mapping[str, Any],
) -> tuple[Path, Path, str]:
    identity = _mapping(report.get("identity"))
    artifact_identity = soperator_cluster_artifact_identity(
        cluster_id=identity.get("cluster_id"),
        cluster_name=identity.get("cluster_name"),
    )
    report_dir = (
        output_root.resolve() / SOPERATOR_PUBLIC_DISCOVERY_DIR_NAME / artifact_identity.cluster_key
    )
    json_path = report_dir / SOPERATOR_PUBLIC_DISCOVERY_JSON_NAME
    markdown_path = report_dir / SOPERATOR_PUBLIC_DISCOVERY_MARKDOWN_NAME
    markdown = _public_discovery_markdown(report, report_dir=report_dir)
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report_dir.mkdir(parents=True, exist_ok=True)
    lock_path = report_dir / ".report.lock"
    open_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    lock_fd = os.open(lock_path, open_flags, 0o600)
    staged_paths: list[Path] = []
    previous_json_path = report_dir / f".{SOPERATOR_PUBLIC_DISCOVERY_JSON_NAME}.previous"
    previous_markdown_path = report_dir / f".{SOPERATOR_PUBLIC_DISCOVERY_MARKDOWN_NAME}.previous"
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if previous_json_path.exists() or previous_markdown_path.exists():
            if (
                previous_markdown_path.exists()
                and not previous_json_path.exists()
                and json_path.exists()
                and not markdown_path.exists()
            ):
                os.replace(previous_markdown_path, markdown_path)
            elif _public_discovery_pair_matches(json_path, markdown_path):
                previous_json_path.unlink(missing_ok=True)
                previous_markdown_path.unlink(missing_ok=True)
            else:
                json_path.unlink(missing_ok=True)
                markdown_path.unlink(missing_ok=True)
                if previous_markdown_path.exists():
                    os.replace(previous_markdown_path, markdown_path)
                if previous_json_path.exists():
                    os.replace(previous_json_path, json_path)
            _public_discovery_fsync_directory(report_dir)
        for path, text in ((markdown_path, markdown), (json_path, json_text)):
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=report_dir,
                prefix=f".{path.name}.{_text(report.get('report_id'))}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                staged_path = Path(handle.name)
                staged_paths.append(staged_path)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        previous_markdown_moved = False
        previous_json_moved = False
        new_markdown_committed = False
        new_json_committed = False
        try:
            if markdown_path.exists():
                os.replace(markdown_path, previous_markdown_path)
                previous_markdown_moved = True
            if json_path.exists():
                os.replace(json_path, previous_json_path)
                previous_json_moved = True
            _public_discovery_fsync_directory(report_dir)
            os.replace(staged_paths[0], markdown_path)
            staged_paths.pop(0)
            new_markdown_committed = True
            _public_discovery_fsync_directory(report_dir)
            os.replace(staged_paths[0], json_path)
            staged_paths.pop(0)
            new_json_committed = True
            _public_discovery_fsync_directory(report_dir)
        except BaseException:
            if new_json_committed:
                json_path.unlink(missing_ok=True)
            if new_markdown_committed:
                markdown_path.unlink(missing_ok=True)
            if previous_markdown_moved and previous_markdown_path.exists():
                os.replace(previous_markdown_path, markdown_path)
            if previous_json_moved and previous_json_path.exists():
                os.replace(previous_json_path, json_path)
            _public_discovery_fsync_directory(report_dir)
            raise
        previous_json_path.unlink(missing_ok=True)
        previous_markdown_path.unlink(missing_ok=True)
        _public_discovery_fsync_directory(report_dir)
    finally:
        for staged_path in staged_paths:
            with suppress(OSError):
                staged_path.unlink()
        with suppress(OSError):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return json_path, markdown_path, markdown


def soperator_discovery_workload_namespace(snapshot: Mapping[str, Any]) -> str:
    resources = snapshot.get("soperator_resources")
    namespaces = {
        _text((resource.get("metadata") or {}).get("namespace"))
        for resource in resources or ()
        if isinstance(resource, Mapping)
        and isinstance(resource.get("metadata"), Mapping)
        and _text(resource.get("kind")).lower() == "slurmcluster"
    }
    namespaces.discard("")
    if len(namespaces) > 1:
        raise RuntimeError(
            "Soperator discovery found SlurmCluster workloads in multiple namespaces."
        )
    return next(iter(namespaces), "")


def soperator_discovery_k8s_minor_text(value: Any) -> str:
    text = _text(value).lstrip("v")
    if not text:
        return ""
    match = re.fullmatch(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:[.+-].*)?", text)
    if match is None:
        return ""
    return f"{match.group('major')}.{match.group('minor')}"


def soperator_discovery_snapshot_control_plane_k8s_version(
    snapshot: Mapping[str, Any] | None,
) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    provider = snapshot.get("provider")
    if not isinstance(provider, Mapping):
        return ""
    cluster = provider.get("mk8s_cluster")
    if not isinstance(cluster, Mapping):
        return ""
    for key in (
        "control_plane_version",
        "controlPlaneVersion",
        "k8s_version",
        "kubernetes_version",
        "version",
    ):
        version = soperator_discovery_k8s_minor_text(cluster.get(key))
        if version:
            return version
    return ""


def _report_finding_value(finding: Any, key: str) -> Any:
    if isinstance(finding, Mapping):
        return finding.get(key)
    return getattr(finding, key, None)


def soperator_discovery_report_k8s_versions(report: Any) -> tuple[str, str]:
    current_version = ""
    target_version = ""
    report_payload = report.to_dict() if hasattr(report, "to_dict") else report
    findings = getattr(report_payload, "findings", ())
    if isinstance(report_payload, Mapping):
        findings = report_payload.get("findings", ())
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes, bytearray)):
        return "", ""
    for finding in findings:
        layer = _text(_report_finding_value(finding, "layer"))
        if layer not in {"mk8s-node-template", "soperator-upgrade-support"}:
            continue
        evidence = _report_finding_value(finding, "evidence")
        if not isinstance(evidence, Mapping):
            continue
        control_plane = evidence.get("control_plane")
        if isinstance(control_plane, Mapping):
            current_version = current_version or soperator_discovery_k8s_minor_text(
                control_plane.get("current_k8s_version")
            )
            target_version = target_version or soperator_discovery_k8s_minor_text(
                control_plane.get("target_k8s_version")
            )
        current_version = current_version or soperator_discovery_k8s_minor_text(
            evidence.get("current_k8s_version")
        )
        target_version = target_version or soperator_discovery_k8s_minor_text(
            evidence.get("target_k8s_version")
        )
        if current_version and target_version:
            break
    return current_version, target_version


def _target_versions_k8s_version(target_versions: Mapping[str, Any] | None) -> str:
    if not isinstance(target_versions, Mapping):
        return ""
    for key in ("k8s_version", "kubernetes_version", "target_k8s_version"):
        version = soperator_discovery_k8s_minor_text(target_versions.get(key))
        if version:
            return version
    return ""


def _redacted_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return any(part in normalized or part in compact for part in _SECRET_KEY_PARTS)


def _redact(value: Any, *, parent_key: str = "", redaction: str = "support") -> Any:
    plain = to_plain_data(value)
    if parent_key and _redacted_key(parent_key):
        if plain in ("", None):
            return plain
        return "[redacted]"
    if isinstance(plain, Mapping):
        result: dict[str, Any] = {}
        for key, item in plain.items():
            key_text = str(key)
            result[key_text] = _redact(item, parent_key=key_text, redaction=redaction)
        return result
    if isinstance(plain, list):
        return [_redact(item, parent_key=parent_key, redaction=redaction) for item in plain]
    if isinstance(plain, tuple):
        return [_redact(item, parent_key=parent_key, redaction=redaction) for item in plain]
    return plain


def _sanitize_kubernetes_snapshot(snapshot: Mapping[str, Any], *, redaction: str) -> dict[str, Any]:
    sanitized = _redact(snapshot, redaction=redaction)
    if not isinstance(sanitized, dict):
        return {}
    namespace_resources = sanitized.get("namespace_resources")
    if isinstance(namespace_resources, dict):
        for namespace_payload in namespace_resources.values():
            if not isinstance(namespace_payload, dict):
                continue
            secrets = namespace_payload.get("secrets")
            if isinstance(secrets, list):
                for secret in secrets:
                    if isinstance(secret, dict):
                        secret.pop("data", None)
                        secret.pop("stringData", None)
                        secret.setdefault("data_redacted", True)
    return sanitized


def _release_chart_version(release: Mapping[str, Any]) -> str:
    explicit = str(release.get("chart_version", "") or "").strip()
    if explicit:
        return explicit
    chart = str(release.get("chart", "") or "").strip()
    match = re.match(r"^[A-Za-z0-9_.-]+-([0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9_.-]+)?)$", chart)
    return match.group(1) if match else ""


def _metadata(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = resource.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _metadata_labels(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    labels = _metadata(resource).get("labels")
    return labels if isinstance(labels, Mapping) else {}


def _source_slurmcluster_ref(
    snapshot: Mapping[str, Any],
    *,
    target_ref: str,
) -> dict[str, Any]:
    candidates: list[dict[str, str]] = []
    for item in _sequence_of_mappings(snapshot.get("soperator_resources")):
        if _text(item.get("kind")).lower() != "slurmcluster":
            continue
        metadata = _metadata(item)
        name = _text(metadata.get("name"))
        if not name:
            continue
        spec = item.get("spec", {})
        raw_secrets = spec.get("secrets", {}) if isinstance(spec, Mapping) else {}
        secrets = raw_secrets if isinstance(raw_secrets, Mapping) else {}
        sshd_keys_name = _text(secrets.get("sshdKeysName"))
        if not sshd_keys_name and raw_secrets != "[redacted]":
            sshd_keys_name = f"{name}-sshd-keys"
        candidates.append(
            {
                "namespace": _text(metadata.get("namespace")) or "soperator",
                "name": name,
                "uid": _text(metadata.get("uid")),
                "sshd_host_key_secret_name": sshd_keys_name,
            }
        )
    normalized_target = _normalized_token(target_ref)
    non_target = [
        item for item in candidates if _normalized_token(item["name"]) != normalized_target
    ]
    selected = non_target if non_target else candidates
    if len(selected) == 1:
        return {"status": "resolved", **selected[0]}
    return {
        "status": "ambiguous" if selected else "missing",
        "candidates": [
            {
                "namespace": item["namespace"],
                "name": item["name"],
                "uid": item["uid"],
            }
            for item in selected
        ],
    }


def _soperator_resource_release(
    snapshot: Mapping[str, Any],
    *,
    namespace: str = "",
    release_name: str = "",
) -> Mapping[str, Any]:
    resources = snapshot.get("soperator_resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes, bytearray)):
        return {}
    requested_namespace = str(namespace or "").strip().lower()
    requested_release = str(release_name or "").strip().lower()
    candidates: list[Mapping[str, Any]] = []
    for item in resources:
        if not isinstance(item, Mapping):
            continue
        labels = _metadata_labels(item)
        chart = str(labels.get("helm.sh/chart", "") or "").strip()
        app_version = str(labels.get("app.kubernetes.io/version", "") or "").strip()
        name = str(labels.get("app.kubernetes.io/instance", "") or "").strip()
        app_name = str(labels.get("app.kubernetes.io/name", "") or "").strip()
        metadata = _metadata(item)
        resource_namespace = str(metadata.get("namespace", "") or "soperator").strip()
        if not chart and not app_version:
            continue
        identity = " ".join((chart, name, app_name)).lower()
        if "soperator" not in identity and "slurm-operator" not in identity:
            continue
        if requested_namespace and resource_namespace.lower() != requested_namespace:
            continue
        if requested_release and name.lower() != requested_release:
            continue
        candidates.append(
            {
                "name": name or "soperator",
                "namespace": resource_namespace,
                "chart": chart,
                "chart_version": _release_chart_version({"chart": chart}),
                "app_version": app_version,
                "status": "resource-labels",
            }
        )
    if not candidates:
        return {}
    return sorted(
        candidates,
        key=lambda item: (
            str(item.get("namespace", "") or "").lower() != "soperator",
            str(item.get("name", "") or "").lower() != "soperator",
            str(item.get("namespace", "") or ""),
            str(item.get("name", "") or ""),
            str(item.get("chart", "") or ""),
        ),
    )[0]


def _soperator_release(
    snapshot: Mapping[str, Any], *, namespace: str = "", release_name: str = ""
) -> Mapping[str, Any]:
    releases = _sequence_of_mappings(snapshot.get("helm_releases"))
    requested_namespace = str(namespace or "").strip().lower()
    requested_release = str(release_name or "").strip().lower()
    for release in releases:
        name = str(release.get("name", "") or "").strip().lower()
        release_namespace = str(release.get("namespace", "") or "").strip().lower()
        if requested_release and name != requested_release:
            continue
        if requested_namespace and release_namespace != requested_namespace:
            continue
        if name in {"soperator", "slurm-operator", "soperator-controller"}:
            return release
    for release in releases:
        chart = str(release.get("chart", "") or release.get("chart_name", "") or "").lower()
        name = str(release.get("name", "") or "").strip().lower()
        if (
            "soperator" in chart
            or "slurm-operator" in chart
            or name in {"soperator", "slurm-operator"}
        ):
            return release
    return {}


def _soperator_status(identity: Mapping[str, Any]) -> str:
    state = str(identity.get("state", "") or "").strip()
    if state == "no-soperator-detected":
        return "not installed"
    source_version = str(identity.get("source_version", "") or "").strip()
    chart_version = str(identity.get("chart_version", "") or "").strip()
    app_version = str(identity.get("app_version", "") or "").strip()
    release = identity.get("helm_release")
    if (
        source_version
        or chart_version
        or app_version
        or (isinstance(release, Mapping) and bool(release))
    ):
        return "installed"
    if state.startswith("existing-soperator"):
        return "detected, version unknown"
    return "unknown"


def _nested_text(mapping: Mapping[str, Any], path: Sequence[str]) -> str:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return _text(current)


def _container_image_version(image: Any) -> str:
    text = _text(image)
    if not text:
        return ""
    without_digest, _sep, digest = text.partition("@")
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    if last_colon > last_slash:
        return without_digest[last_colon + 1 :]
    return digest if digest else ""


def _pod_template_container_image(resource: Mapping[str, Any]) -> str:
    containers = resource.get("containers")
    if not isinstance(containers, Sequence) or isinstance(containers, (str, bytes, bytearray)):
        containers = _nested_container_sequence(
            resource, ("spec", "template", "spec", "containers")
        )
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        name = _text(container.get("name")).lower()
        image = _text(container.get("image"))
        if image and ("populate" in name and "jail" in name):
            return image
    for container in containers:
        if isinstance(container, Mapping) and _text(container.get("image")):
            return _text(container.get("image"))
    return ""


def _nested_container_sequence(
    mapping: Mapping[str, Any],
    path: Sequence[str],
) -> Sequence[Any]:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return ()
        current = current.get(key)
    if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
        return current
    return ()


def _job_is_complete(resource: Mapping[str, Any]) -> bool:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return False
    succeeded = status.get("succeeded")
    if isinstance(succeeded, int) and succeeded > 0:
        return True
    if isinstance(succeeded, str) and succeeded.isdigit() and int(succeeded) > 0:
        return True
    conditions = status.get("conditions")
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes, bytearray)):
        return False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if (
            _text(condition.get("type")).lower() == "complete"
            and _text(condition.get("status")).lower() == "true"
        ):
            return True
    return False


def _job_is_failed(resource: Mapping[str, Any]) -> bool:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return False
    failed = status.get("failed")
    if (isinstance(failed, int) and not isinstance(failed, bool) and failed > 0) or (
        isinstance(failed, str) and failed.isdigit() and int(failed) > 0
    ):
        return True
    conditions = status.get("conditions")
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes, bytearray)):
        return False
    return any(
        isinstance(condition, Mapping)
        and _text(condition.get("type")).lower() == "failed"
        and _text(condition.get("status")).lower() == "true"
        for condition in conditions
    )


def _pvc_claim_name(reference: Any) -> str:
    if not isinstance(reference, Mapping):
        return ""
    persistent_volume_claim = reference.get("persistentVolumeClaim")
    if not isinstance(persistent_volume_claim, Mapping):
        return ""
    return _text(persistent_volume_claim.get("claimName"))


def _jail_volume_source_claims(spec: Mapping[str, Any]) -> tuple[dict[str, str], str]:
    raw_sources = spec.get("volumeSources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes, bytearray)):
        return {}, "SlurmCluster spec.volumeSources is unavailable"
    claims: dict[str, str] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, Mapping):
            continue
        name = _text(raw_source.get("name"))
        claim_name = _pvc_claim_name(raw_source)
        if not name or not claim_name:
            continue
        if name in claims and claims[name] != claim_name:
            return {}, f"SlurmCluster volume source {name!r} resolves to multiple PVCs"
        claims[name] = claim_name
    if not claims.get("jail"):
        return claims, "canonical SlurmCluster volume source 'jail' has no PVC binding"
    return claims, ""


def _jail_consumer_claim_name(
    jail: Any,
    *,
    volume_source_claims: Mapping[str, str],
) -> str:
    if not isinstance(jail, Mapping):
        return ""
    direct_claim = _pvc_claim_name(jail)
    if direct_claim:
        return direct_claim
    source_name = _text(jail.get("volumeSourceName"))
    return _text(volume_source_claims.get(source_name)) if source_name else ""


def _active_jail_pvc_binding(snapshot: Mapping[str, Any]) -> dict[str, str]:
    evidence = {
        "status": "unverified",
        "reason": "active Jail rootfs binding is unavailable",
        "slurmcluster_name": "",
        "namespace": "",
        "pvc_name": "",
        "pvc_uid": "",
        "jail_filesystem_id": "",
    }
    identity_snapshot = snapshot
    identity_resources = snapshot.get("identity_soperator_resources")
    if isinstance(identity_resources, Sequence) and not isinstance(
        identity_resources, (str, bytes, bytearray)
    ):
        identity_snapshot = {**snapshot, "soperator_resources": identity_resources}
    slurmclusters = _resource_items(identity_snapshot, "slurmclusters")
    resume_identity = snapshot.get("resume_slurmcluster_identity")
    if isinstance(resume_identity, Mapping):
        selected_key = _text(resume_identity.get("identity_role"))
        if selected_key not in {"source", "target"}:
            selected_key = "source"
        selected_ref = resume_identity.get(selected_key)
        selected_ref = selected_ref if isinstance(selected_ref, Mapping) else {}
        expected_namespace = _text(selected_ref.get("namespace")) or "soperator"
        expected_name = _text(selected_ref.get("name"))
        expected_uid = _text(selected_ref.get("uid"))
        slurmclusters = [
            item
            for item in slurmclusters
            if _text(_metadata(item).get("namespace")) in {"", expected_namespace}
            and _text(_metadata(item).get("name")) == expected_name
            and _text(_metadata(item).get("uid")) == expected_uid
        ]
    if len(slurmclusters) != 1:
        evidence["reason"] = (
            "active Jail rootfs evidence requires exactly one live SlurmCluster; "
            f"observed {len(slurmclusters)}"
        )
        return evidence
    slurmcluster = slurmclusters[0]
    metadata = slurmcluster.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    evidence["slurmcluster_name"] = _text(metadata.get("name"))
    namespace = _text(metadata.get("namespace")) or "soperator"
    evidence["namespace"] = namespace
    spec = slurmcluster.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    volume_source_claims, binding_error = _jail_volume_source_claims(spec)
    active_pvc = _text(volume_source_claims.get("jail"))
    evidence["pvc_name"] = active_pvc
    if binding_error:
        evidence["reason"] = binding_error
        return evidence

    consumer_claims: list[tuple[str, str]] = []
    unresolved_consumers: list[str] = []
    slurm_nodes = spec.get("slurmNodes")
    if isinstance(slurm_nodes, Mapping):
        for role_name, raw_role in sorted(slurm_nodes.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_role, Mapping):
                continue
            volumes = raw_role.get("volumes")
            volumes = volumes if isinstance(volumes, Mapping) else {}
            if "jail" not in volumes:
                continue
            consumer = f"SlurmCluster role {role_name}"
            claim_name = _jail_consumer_claim_name(
                volumes.get("jail"),
                volume_source_claims=volume_source_claims,
            )
            if claim_name:
                consumer_claims.append((consumer, claim_name))
            else:
                unresolved_consumers.append(consumer)
    for nodeset in _resource_items(snapshot, "nodesets"):
        nodeset_metadata = nodeset.get("metadata")
        nodeset_metadata = nodeset_metadata if isinstance(nodeset_metadata, Mapping) else {}
        nodeset_name = _text(nodeset_metadata.get("name")) or "<unnamed>"
        nodeset_spec = nodeset.get("spec")
        nodeset_spec = nodeset_spec if isinstance(nodeset_spec, Mapping) else {}
        slurmd = nodeset_spec.get("slurmd")
        slurmd = slurmd if isinstance(slurmd, Mapping) else {}
        volumes = slurmd.get("volumes")
        volumes = volumes if isinstance(volumes, Mapping) else {}
        if "jail" not in volumes:
            continue
        consumer = f"NodeSet {nodeset_name}"
        claim_name = _jail_consumer_claim_name(
            volumes.get("jail"),
            volume_source_claims=volume_source_claims,
        )
        if claim_name:
            consumer_claims.append((consumer, claim_name))
        else:
            unresolved_consumers.append(consumer)
    if unresolved_consumers:
        evidence["reason"] = "Jail rootfs consumers have unresolved PVC bindings: " + ", ".join(
            unresolved_consumers
        )
        return evidence
    if not consumer_claims:
        evidence["reason"] = "no live SlurmCluster or NodeSet Jail rootfs consumers were found"
        return evidence
    drifted_consumers = [
        f"{consumer}={claim_name}"
        for consumer, claim_name in consumer_claims
        if claim_name != active_pvc
    ]
    if drifted_consumers:
        evidence["reason"] = (
            f"canonical Jail PVC {active_pvc!r} is not used by all live consumers: "
            + ", ".join(drifted_consumers)
        )
        return evidence

    pvc_matches: list[Mapping[str, Any]] = []
    raw_pvcs = snapshot.get("pvcs")
    if isinstance(raw_pvcs, Sequence) and not isinstance(raw_pvcs, (str, bytes, bytearray)):
        for pvc in raw_pvcs:
            if not isinstance(pvc, Mapping):
                continue
            pvc_metadata = pvc.get("metadata")
            pvc_metadata = pvc_metadata if isinstance(pvc_metadata, Mapping) else {}
            if (
                _text(pvc_metadata.get("name")) == active_pvc
                and (_text(pvc_metadata.get("namespace")) or "default") == namespace
            ):
                pvc_matches.append(pvc)
    if len(pvc_matches) != 1:
        evidence["reason"] = (
            f"canonical Jail PVC {namespace}/{active_pvc} resolved to "
            f"{len(pvc_matches)} live PVC objects"
        )
        return evidence
    pvc = pvc_matches[0]
    pvc_metadata = pvc.get("metadata")
    pvc_metadata = pvc_metadata if isinstance(pvc_metadata, Mapping) else {}
    pvc_status = pvc.get("status")
    pvc_status = pvc_status if isinstance(pvc_status, Mapping) else {}
    pvc_uid = _text(pvc_metadata.get("uid"))
    evidence["pvc_uid"] = pvc_uid
    if not pvc_uid or _text(pvc_status.get("phase")) != "Bound":
        evidence["reason"] = (
            f"canonical Jail PVC {namespace}/{active_pvc} is not Bound with an immutable UID"
        )
        return evidence
    cluster_identity = snapshot.get("cluster_identity")
    cluster_identity = cluster_identity if isinstance(cluster_identity, Mapping) else {}
    jail_filesystem_id = _text(cluster_identity.get("jail_filesystem_id"))
    evidence["jail_filesystem_id"] = jail_filesystem_id
    if not jail_filesystem_id:
        evidence["reason"] = "immutable Jail filesystem identity is unavailable"
        return evidence
    evidence["status"] = "bound"
    evidence["reason"] = "canonical alias and all discovered Jail consumers use the same PVC"
    return evidence


def _job_pvc_claim_names(resource: Mapping[str, Any]) -> tuple[str, ...]:
    sanitized = resource.get("pvc_claim_names")
    if isinstance(sanitized, Sequence) and not isinstance(sanitized, (str, bytes, bytearray)):
        return tuple(sorted({_text(item) for item in sanitized if _text(item)}))
    volumes = _nested_container_sequence(resource, ("spec", "template", "spec", "volumes"))
    return tuple(sorted({_pvc_claim_name(volume) for volume in volumes if _pvc_claim_name(volume)}))


def _populate_jail_active_job_evidence(snapshot: Mapping[str, Any]) -> dict[str, str]:
    active_binding = _active_jail_pvc_binding(snapshot)
    evidence = {
        "status": "unverified",
        "reason": active_binding["reason"],
        "job_name": "",
        "job_uid": "",
        "slot": "",
        "image": "",
        "pvc_name": active_binding["pvc_name"],
        "pvc_uid": active_binding["pvc_uid"],
        "jail_filesystem_id": active_binding["jail_filesystem_id"],
        "slurmcluster_name": active_binding["slurmcluster_name"],
    }
    if active_binding["status"] != "bound":
        return evidence

    candidates: list[tuple[str, str, str, str, str]] = []
    wrong_pvc_jobs: list[str] = []
    resources = snapshot.get("soperator_namespace_resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes, bytearray)):
        evidence["reason"] = "Soperator namespace Job discovery is unavailable"
        return evidence
    for resource in resources:
        if not isinstance(resource, Mapping):
            continue
        if _text(resource.get("kind")) != "Job":
            continue
        name = _object_name(resource)
        if "populate-jail" not in name:
            continue
        metadata = resource.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        labels = metadata.get("labels")
        labels = labels if isinstance(labels, Mapping) else {}
        if _text(labels.get("slurm.nebius.ai/jail-rootfs-refresh")) != "active-passive":
            continue
        slot = _text(labels.get("slurm.nebius.ai/jail-rootfs-slot"))
        if slot not in {"slot-a", "slot-b"}:
            continue
        if not _job_is_complete(resource) or _job_is_failed(resource):
            continue
        image = _pod_template_container_image(resource)
        if not image:
            continue
        job_uid = _text(metadata.get("uid"))
        if not job_uid:
            continue
        pvc_claim_names = _job_pvc_claim_names(resource)
        if active_binding["pvc_name"] not in pvc_claim_names:
            wrong_pvc_jobs.append(name)
            continue
        completed_at = _nested_text(resource, ("status", "completionTime"))
        created_at = _nested_text(resource, ("metadata", "creationTimestamp"))
        candidates.append((completed_at or created_at, name, job_uid, slot, image))
    if not candidates:
        evidence["reason"] = (
            "completed active/passive populate Jobs do not target the canonical active PVC"
            if wrong_pvc_jobs
            else "no completed active/passive populate Job is bound to the canonical active PVC"
        )
        return evidence
    _timestamp, name, job_uid, slot, image = sorted(
        candidates,
        key=lambda item: item[0],
        reverse=True,
    )[0]
    evidence.update(
        {
            "status": "active-slot-verified",
            "reason": (
                "completed populate Job, canonical alias, immutable PVC, and all discovered "
                "Jail consumers agree on the active slot"
            ),
            "job_name": name,
            "job_uid": job_uid,
            "slot": slot,
            "image": image,
        }
    )
    return evidence


def _live_populate_jail_image(snapshot: Mapping[str, Any]) -> tuple[str, str]:
    for item in _resource_items(snapshot, "slurmclusters"):
        image = _nested_text(item, ("spec", "populateJail", "image"))
        if image:
            return _object_name(item), image
    return "", ""


def _report_selected_action_ids(report: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    actions = report.get("actions")
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, bytearray)):
        return result
    for action in actions:
        if isinstance(action, str):
            action_id = _text(action)
            selected = True
        elif isinstance(action, Mapping):
            action_id = _text(action.get("id"))
            selected = action.get("selected") is not False
        else:
            continue
        if action_id and selected:
            result.add(action_id)
    return result


def _target_jail_rootfs_image(target_versions: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(target_versions, Mapping):
        return "", ""
    jail_rootfs = target_versions.get("jail_rootfs")
    if isinstance(jail_rootfs, Mapping):
        return _text(jail_rootfs.get("target_image")), _text(jail_rootfs.get("target_source"))
    return "", ""


def _jail_rootfs_record(
    *,
    snapshot: Mapping[str, Any],
    report: Mapping[str, Any],
    target_versions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    active_evidence = _populate_jail_active_job_evidence(snapshot)
    job_name = active_evidence["job_name"]
    job_image = active_evidence["image"]
    slurmcluster_name, live_desired_image = _live_populate_jail_image(snapshot)
    current_image = job_image or live_desired_image
    current_source = (
        "completed-populate-jail-job"
        if job_image
        else "slurmcluster.spec.populateJail.image"
        if live_desired_image
        else "not-detected"
    )
    target_image, target_source = _target_jail_rootfs_image(target_versions)
    target_jail_rootfs = _mapping(_mapping(target_versions).get("jail_rootfs"))
    action_ids = _report_selected_action_ids(report)
    chart_upgrade_selected = "upgrade-soperator" in action_ids
    if current_image and target_image and current_image != target_image:
        refresh_required = True
        reason = "target populate-jail image differs from current jail rootfs image"
    elif current_source != "completed-populate-jail-job" and target_image:
        refresh_required = True
        reason = (
            "active Jail rootfs evidence is incomplete: "
            f"{active_evidence['reason']}; the SlurmCluster desired image does not prove "
            "a completed active-slot population"
        )
    elif chart_upgrade_selected:
        refresh_required = True
        reason = "Soperator chart upgrade is selected; jail rootfs compatibility is unproven"
    elif current_image and target_image:
        refresh_required = False
        reason = "current populate-jail image matches target chart image"
    else:
        refresh_required = False
        reason = "populate-jail image evidence is incomplete"
    return {
        "current_image": current_image,
        "current_version": _container_image_version(current_image),
        "current_source": current_source,
        "current_job_name": job_name,
        "current_job_uid": active_evidence["job_uid"],
        "current_slot": active_evidence["slot"],
        "current_pvc_name": active_evidence["pvc_name"],
        "current_pvc_uid": active_evidence["pvc_uid"],
        "current_jail_filesystem_id": active_evidence["jail_filesystem_id"],
        "current_evidence_status": active_evidence["status"],
        "current_evidence_reason": active_evidence["reason"],
        "live_desired_image": live_desired_image,
        "live_desired_version": _container_image_version(live_desired_image),
        "live_desired_source": "slurmcluster.spec.populateJail.image" if live_desired_image else "",
        "slurmcluster_name": slurmcluster_name,
        "target_image": target_image,
        "target_version": _container_image_version(target_image),
        "target_source": target_source,
        "target_cuda_version": _text(target_jail_rootfs.get("target_cuda_version")),
        "target_digest": _text(target_jail_rootfs.get("target_digest")),
        "target_identity_warning": _text(target_jail_rootfs.get("target_identity_warning")),
        "refresh_required": refresh_required,
        "reason": reason,
    }


def soperator_discovery_jail_rootfs_record(
    *,
    snapshot: Mapping[str, Any],
    report: Mapping[str, Any],
    target_versions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return _jail_rootfs_record(
        snapshot=snapshot,
        report=report,
        target_versions=target_versions,
    )


_SOPERATOR_RESOURCE_KIND_BY_KEY = {
    "activechecks": "ActiveCheck",
    "nodeconfigurators": "NodeConfigurator",
    "nodesets": "NodeSet",
    "slurmclusters": "SlurmCluster",
}


def _filter_soperator_resource_items(
    items: Any,
    resource_key: str,
) -> list[Mapping[str, Any]]:
    expected_kind = _SOPERATOR_RESOURCE_KIND_BY_KEY.get(resource_key, "")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []
    result: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if expected_kind and str(item.get("kind", "") or "") != expected_kind:
            continue
        result.append(item)
    return result


def _resource_items(snapshot: Mapping[str, Any], resource_key: str) -> list[Mapping[str, Any]]:
    resources = snapshot.get("soperator_resources")
    if isinstance(resources, Sequence) and not isinstance(resources, (str, bytes, bytearray)):
        return _filter_soperator_resource_items(resources, resource_key)
    if not isinstance(resources, Mapping):
        return []
    if "items" in resources:
        return _filter_soperator_resource_items(resources.get("items"), resource_key)
    raw = resources.get(resource_key)
    if isinstance(raw, Mapping):
        items = raw.get("items")
        return _filter_soperator_resource_items(items, resource_key)
    return _filter_soperator_resource_items(raw, resource_key)


def _object_name(item: Mapping[str, Any]) -> str:
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        return str(metadata.get("name", "") or "").strip()
    return ""


def _identity_section(
    *,
    target_ref: str,
    source_kind: str,
    snapshot: Mapping[str, Any],
    report: Mapping[str, Any],
    target_versions: Mapping[str, Any] | None,
    cluster_id: str,
    cluster_name: str,
    namespace: str,
    release_name: str,
    kube_context: str,
) -> dict[str, Any]:
    release = _soperator_release(snapshot, namespace=namespace, release_name=release_name)
    if not release:
        release = _soperator_resource_release(
            snapshot,
            namespace=namespace,
            release_name=release_name,
        )
    detected_namespace = str(release.get("namespace", "") or namespace or "").strip()
    detected_release_name = str(release.get("name", "") or release_name or "").strip()
    detected_chart_version = _release_chart_version(release)
    detected_app_version = str(
        release.get("app_version", "") or release.get("appVersion", "") or ""
    ).strip()
    detected_source_version = (
        str(report.get("source_version", "") or "").strip()
        or detected_app_version
        or detected_chart_version
    )
    current_k8s_version, target_k8s_version = soperator_discovery_report_k8s_versions(report)
    current_k8s_version = (
        current_k8s_version or soperator_discovery_snapshot_control_plane_k8s_version(snapshot)
    )
    target_k8s_version = _target_versions_k8s_version(target_versions) or target_k8s_version
    identity = {
        "target_ref": _normalized_token(target_ref, "mk8s"),
        "source_kind": source_kind,
        "cluster_id": str(cluster_id or "").strip(),
        "cluster_name": str(cluster_name or "").strip(),
        "kube_context": str(kube_context or "").strip(),
        "namespace": detected_namespace,
        "release_name": detected_release_name,
        "chart_version": detected_chart_version,
        "app_version": detected_app_version,
        "source_version": detected_source_version,
        "target_version": str(report.get("target_version", "") or "").strip(),
        "current_k8s_version": current_k8s_version,
        "target_k8s_version": target_k8s_version,
        "state": str(report.get("state", "") or "").strip(),
        "fingerprint": str(report.get("fingerprint", "") or "").strip(),
        "helm_release": _redact(release),
        "crd_versions": _redact(snapshot.get("crds", [])),
        "source_slurmcluster_ref": _source_slurmcluster_ref(
            snapshot,
            target_ref=target_ref,
        ),
    }
    jail_rootfs = report.get("jail_rootfs")
    if isinstance(jail_rootfs, Mapping):
        identity["jail_rootfs"] = _redact(jail_rootfs)
    soperator_app = report.get("soperator_app")
    if isinstance(soperator_app, Mapping):
        identity["soperator_app"] = _redact(soperator_app)
    soperator_chart = report.get("soperator_chart")
    if isinstance(soperator_chart, Mapping):
        identity["soperator_chart"] = _redact(soperator_chart)
    identity["soperator_status"] = _soperator_status(identity)
    return identity


def _kubernetes_section(snapshot: Mapping[str, Any], *, redaction: str) -> dict[str, Any]:
    sanitized_snapshot = _sanitize_kubernetes_snapshot(snapshot, redaction=redaction)
    namespace_resources = sanitized_snapshot.get("namespace_resources")
    resource_counts: dict[str, Any] = {}
    if isinstance(namespace_resources, Mapping):
        for namespace, resources in namespace_resources.items():
            if not isinstance(resources, Mapping):
                continue
            resource_counts[str(namespace)] = {
                str(key): len(value) if isinstance(value, list) else 0
                for key, value in resources.items()
            }
    return {
        "snapshot": sanitized_snapshot,
        "resource_counts": resource_counts,
        "nodesets": [
            _redact(item, redaction=redaction) for item in _resource_items(snapshot, "nodesets")
        ],
        "slurmclusters": [
            _redact(item, redaction=redaction)
            for item in _resource_items(snapshot, "slurmclusters")
        ],
        "activechecks": [
            _redact(item, redaction=redaction) for item in _resource_items(snapshot, "activechecks")
        ],
        "nodeconfigurators": [
            _redact(item, redaction=redaction)
            for item in _resource_items(snapshot, "nodeconfigurators")
        ],
    }


def _slurm_section(slurm_snapshot: Mapping[str, Any] | None, *, redaction: str) -> dict[str, Any]:
    if not isinstance(slurm_snapshot, Mapping):
        return {
            "available": False,
            "collection_errors": [
                {
                    "severity": "recommended",
                    "message": "Slurm runtime discovery was not collected.",
                }
            ],
        }
    return _redact(slurm_snapshot, redaction=redaction)


def _accounting_section(
    accounting_snapshot: Mapping[str, Any] | None,
    *,
    chart_values: Mapping[str, Any] | None,
    redaction: str,
) -> dict[str, Any]:
    values = chart_values if isinstance(chart_values, Mapping) else {}
    external_db = _nested_bool(values, ("slurmNodes", "accounting", "externalDB", "enabled"))
    managed_mariadb = _nested_bool(
        values, ("slurmNodes", "accounting", "mariadbOperator", "enabled")
    )
    section: dict[str, Any] = {
        "external_db_enabled": external_db,
        "chart_managed_mariadb": bool(managed_mariadb and not external_db),
        "db_dump_included": False,
        "redaction": redaction,
    }
    if isinstance(accounting_snapshot, Mapping):
        section.update(_redact(accounting_snapshot, redaction=redaction))
    else:
        section["collection_errors"] = [
            {
                "severity": "recommended",
                "message": "Slurm accounting discovery was not collected.",
            }
        ]
    return section


def _nested_bool(mapping: Mapping[str, Any], path: Sequence[str]) -> bool:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return current is True or str(current).strip().lower() == "true"


def _customizations_section(
    *,
    snapshot: Mapping[str, Any],
    chart_values: Mapping[str, Any] | None,
    redaction: str,
) -> dict[str, Any]:
    values = chart_values if isinstance(chart_values, Mapping) else {}
    slurmclusters = _resource_items(snapshot, "slurmclusters")
    nodesets = _resource_items(snapshot, "nodesets")
    return {
        "chart_values": _redact(values, redaction=redaction),
        "slurmclusters": [
            {
                "name": _object_name(item),
                "spec": _redact(item.get("spec", {}), redaction=redaction),
            }
            for item in slurmclusters
        ],
        "nodesets": [
            {
                "name": _object_name(item),
                "spec": _redact(item.get("spec", {}), redaction=redaction),
            }
            for item in nodesets
        ],
        "node_groups": _redact(snapshot.get("node_groups", {}), redaction=redaction),
        "storage": _redact(snapshot.get("storage", {}), redaction=redaction),
        "configmaps": _configmap_refs(snapshot),
        "secret_references": _secret_refs(snapshot),
    }


def _configmap_refs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    namespace_resources = snapshot.get("namespace_resources")
    if not isinstance(namespace_resources, Mapping):
        return refs
    for namespace, resources in namespace_resources.items():
        if not isinstance(resources, Mapping):
            continue
        for item in (
            resources.get("configmaps", []) if isinstance(resources.get("configmaps"), list) else []
        ):
            if not isinstance(item, Mapping):
                continue
            metadata = _mapping(item.get("metadata"))
            refs.append(
                {
                    "namespace": str(namespace),
                    "name": str(metadata.get("name", "") or "").strip(),
                    "data_keys": sorted(str(key) for key in (item.get("data_keys") or [])),
                }
            )
    return refs


def _secret_refs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    namespace_resources = snapshot.get("namespace_resources")
    if not isinstance(namespace_resources, Mapping):
        return refs
    for namespace, resources in namespace_resources.items():
        if not isinstance(resources, Mapping):
            continue
        for item in (
            resources.get("secrets", []) if isinstance(resources.get("secrets"), list) else []
        ):
            if not isinstance(item, Mapping):
                continue
            metadata = _mapping(item.get("metadata"))
            refs.append(
                {
                    "namespace": str(namespace),
                    "name": str(metadata.get("name", "") or "").strip(),
                    "type": str(item.get("type", "") or "").strip(),
                    "data_keys": sorted(str(key) for key in (item.get("data_keys") or [])),
                    "data_redacted": True,
                }
            )
    return refs


def _fingerprints_section(
    *,
    report: Mapping[str, Any],
    kubernetes: Mapping[str, Any],
    slurm: Mapping[str, Any],
    accounting: Mapping[str, Any],
    customizations: Mapping[str, Any],
) -> dict[str, Any]:
    protected = {
        "slurm": slurm,
        "accounting": accounting,
        "customizations": customizations,
        "nodesets": kubernetes.get("nodesets", []),
        "slurmclusters": kubernetes.get("slurmclusters", []),
    }
    return {
        "onboarding_fingerprint": str(report.get("fingerprint", "") or "").strip(),
        "protected_config_hash": _sha256_text(_stable_json(protected)),
        "slurm_hash": _sha256_text(_stable_json(slurm)),
        "accounting_hash": _sha256_text(_stable_json(accounting)),
        "customizations_hash": _sha256_text(_stable_json(customizations)),
        "kubernetes_policy_hash": _sha256_text(
            _stable_json(
                {
                    "nodesets": kubernetes.get("nodesets", []),
                    "slurmclusters": kubernetes.get("slurmclusters", []),
                    "activechecks": kubernetes.get("activechecks", []),
                    "nodeconfigurators": kubernetes.get("nodeconfigurators", []),
                }
            )
        ),
    }


def _finding_classification(severity: str) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized in {"blocking", "blocker", "fatal"}:
        return "blocking"
    if normalized in {"required", "error", "failed", "fail"}:
        return "required"
    if normalized in {"recommended", "warning", "warn"}:
        return "recommended"
    return "info"


def _findings_section(
    report: Mapping[str, Any], *, slurm: Mapping[str, Any], accounting: Mapping[str, Any]
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for item in report.get("findings", []) if isinstance(report.get("findings"), list) else []:
        if not isinstance(item, Mapping):
            continue
        severity = str(item.get("severity", "") or "").strip()
        row = dict(item)
        row["classification"] = _finding_classification(severity)
        row["requires_customer_approval"] = row["classification"] in {"required", "blocking"}
        findings.append(row)
    for source, section in (("slurm", slurm), ("accounting", accounting)):
        errors = section.get("collection_errors") if isinstance(section, Mapping) else None
        if not isinstance(errors, list):
            continue
        for error in errors:
            if not isinstance(error, Mapping):
                continue
            severity = str(error.get("severity", "") or "recommended")
            findings.append(
                {
                    "layer": source,
                    "status": "partial-discovery",
                    "severity": severity,
                    "classification": _finding_classification(severity),
                    "message": str(
                        error.get("message", "") or "Optional discovery collector failed."
                    ),
                    "requires_customer_approval": False,
                    "evidence": _redact(error),
                }
            )
    actions: list[dict[str, Any]] = []
    for item in report.get("actions", []) if isinstance(report.get("actions"), list) else []:
        if not isinstance(item, Mapping):
            continue
        action = dict(item)
        action["requires_customer_approval"] = bool(
            action.get("required") or action.get("selected") or action.get("disruptive")
        )
        actions.append(action)
    remediation = [
        dict(item) for item in report.get("remediation", []) if isinstance(item, Mapping)
    ]
    return {
        "onboarding_report": copy.deepcopy(dict(report)),
        "findings": findings,
        "actions": actions,
        "remediation": remediation,
        "blocking_count": sum(1 for item in findings if item.get("classification") == "blocking"),
        "required_count": sum(1 for item in findings if item.get("classification") == "required"),
        "recommended_count": sum(
            1 for item in findings if item.get("classification") == "recommended"
        ),
    }


def _summary_markdown(
    *,
    identity: Mapping[str, Any],
    findings: Mapping[str, Any],
    bundle_dir: Path,
    guidance_lines: Sequence[str] | None = None,
) -> str:
    target_ref = str(identity.get("target_ref", "") or "").strip()
    state = str(identity.get("state", "") or "").strip() or "unknown"
    source_version = str(identity.get("source_version", "") or "").strip() or "unknown"
    target_version = str(identity.get("target_version", "") or "").strip() or "unknown"
    current_k8s_version = str(identity.get("current_k8s_version", "") or "").strip()
    target_k8s_version = str(identity.get("target_k8s_version", "") or "").strip()
    namespace = str(identity.get("namespace", "") or "").strip() or "unknown"
    release_name = str(identity.get("release_name", "") or "").strip() or "unknown"
    soperator_status = str(identity.get("soperator_status", "") or "").strip() or "unknown"
    chart_version = str(identity.get("chart_version", "") or "").strip()
    app_version = str(identity.get("app_version", "") or "").strip()
    jail_rootfs = identity.get("jail_rootfs")
    jail_rootfs_version = (
        str(jail_rootfs.get("current_version", "") or "").strip()
        if isinstance(jail_rootfs, Mapping)
        else ""
    )
    target_jail_rootfs_version = (
        str(jail_rootfs.get("target_version", "") or "").strip()
        if isinstance(jail_rootfs, Mapping)
        else ""
    )
    lines = [
        "# Soperator Discovery Summary",
        "",
        f"- Target: `{target_ref}`",
        f"- Bundle: `{bundle_dir}`",
        f"- State: `{state}`",
        f"- Soperator status: `{soperator_status}`",
        f"- Namespace: `{namespace}`",
        f"- Release: `{release_name}`",
        f"- Source version: `{source_version}`",
        f"- Target version: `{target_version}`",
    ]
    if chart_version:
        lines.append(f"- Soperator chart version: `{chart_version}`")
    if app_version:
        lines.append(f"- Soperator app version: `{app_version}`")
    if jail_rootfs_version:
        lines.append(f"- Jail rootfs version: `{jail_rootfs_version}`")
    if target_jail_rootfs_version and target_jail_rootfs_version != jail_rootfs_version:
        lines.append(f"- Target Jail rootfs version: `{target_jail_rootfs_version}`")
    if current_k8s_version:
        lines.append(f"- Current Kubernetes version: `{current_k8s_version}`")
    if target_k8s_version:
        lines.append(f"- Target Kubernetes version: `{target_k8s_version}`")
    lines.extend(
        [
            f"- Blocking findings: `{findings.get('blocking_count', 0)}`",
            f"- Required findings: `{findings.get('required_count', 0)}`",
            f"- Recommended findings: `{findings.get('recommended_count', 0)}`",
            "",
            "Discovery is not a backup. Raw Secret values, DB dumps, SQL, tokens, and cert material are not included.",
            "",
        ]
    )
    guidance = [str(line).rstrip() for line in guidance_lines or () if str(line).strip()]
    if guidance:
        lines.extend(["## Upgrade Guidance", "", *guidance, ""])
    return "\n".join(lines)


def _write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with suppress(OSError):
            if path.read_text(encoding="utf-8") == text:
                return
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            with suppress(OSError):
                tmp_path.unlink()


def _write_json_if_changed(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_if_changed(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _stable_findings_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(to_plain_data(payload))
    report = stable.get("onboarding_report") if isinstance(stable, dict) else None
    if isinstance(report, dict):
        report.pop("analyzed_at", None)
    return stable if isinstance(stable, dict) else {}


def _preserve_stable_report_timestamp(path: Path, payload: dict[str, Any]) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, Mapping):
        return
    if _stable_findings_payload(existing) != _stable_findings_payload(payload):
        return
    existing_report = existing.get("onboarding_report")
    report = payload.get("onboarding_report")
    if not isinstance(existing_report, Mapping) or not isinstance(report, dict):
        return
    analyzed_at = str(existing_report.get("analyzed_at", "") or "").strip()
    if analyzed_at:
        report["analyzed_at"] = analyzed_at


def _stable_manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(to_plain_data(payload))
    if isinstance(stable, dict):
        stable.pop("generated_at", None)
    return stable if isinstance(stable, dict) else {}


def _preserve_stable_manifest_timestamp(path: Path, payload: dict[str, Any]) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, Mapping):
        return
    if _stable_manifest_payload(existing) != _stable_manifest_payload(payload):
        return
    generated_at = str(existing.get("generated_at", "") or "").strip()
    if generated_at:
        payload["generated_at"] = generated_at


def soperator_discovery_bundle_dir(
    project_dir: Path,
    target_ref: str,
    *,
    output_dir: Path | None = None,
    cluster_id: str = "",
    cluster_name: str = "",
    kube_context: str = "",
    artifact_identity: SoperatorClusterArtifactIdentity | None = None,
) -> Path:
    root = output_dir if output_dir is not None else project_dir
    identity = artifact_identity or soperator_cluster_artifact_identity(
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    return soperator_cluster_report_dir(root, identity, "discovery")


def write_soperator_discovery_bundle(
    project_dir: Path,
    *,
    target_ref: str,
    snapshot: Mapping[str, Any],
    report: Any,
    source_kind: str,
    command: Sequence[str] | None = None,
    cluster_id: str = "",
    cluster_name: str = "",
    artifact_identity: SoperatorClusterArtifactIdentity | None = None,
    namespace: str = "",
    release_name: str = "",
    kube_context: str = "",
    chart_values: Mapping[str, Any] | None = None,
    slurm_snapshot: Mapping[str, Any] | None = None,
    accounting_snapshot: Mapping[str, Any] | None = None,
    target_versions: Mapping[str, Any] | None = None,
    guidance_lines: Sequence[str] | None = None,
    output_dir: Path | None = None,
    redaction: str = "support",
) -> Path:
    normalized_target = _normalized_token(target_ref, "mk8s")
    cluster_identity = artifact_identity or soperator_cluster_artifact_identity_from_snapshot(
        target_ref=normalized_target,
        snapshot=snapshot,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        kube_context=kube_context,
    )
    bundle_dir = soperator_discovery_bundle_dir(
        project_dir,
        normalized_target,
        output_dir=output_dir,
        artifact_identity=cluster_identity,
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)
    report_payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    plain_snapshot = to_plain_data(snapshot)
    if not isinstance(plain_snapshot, Mapping):
        plain_snapshot = {}
    report_payload["jail_rootfs"] = _jail_rootfs_record(
        snapshot=plain_snapshot,
        report=report_payload,
        target_versions=target_versions,
    )
    identity = _identity_section(
        target_ref=normalized_target,
        source_kind=source_kind,
        snapshot=plain_snapshot,
        report=report_payload,
        target_versions=target_versions,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        namespace=namespace,
        release_name=release_name,
        kube_context=kube_context,
    )
    kubernetes = _kubernetes_section(plain_snapshot, redaction=redaction)
    slurm = _slurm_section(slurm_snapshot, redaction=redaction)
    accounting = _accounting_section(
        accounting_snapshot,
        chart_values=chart_values,
        redaction=redaction,
    )
    customizations = _customizations_section(
        snapshot=plain_snapshot,
        chart_values=chart_values,
        redaction=redaction,
    )
    fingerprints = _fingerprints_section(
        report=report_payload,
        kubernetes=kubernetes,
        slurm=slurm,
        accounting=accounting,
        customizations=customizations,
    )
    findings = _findings_section(report_payload, slurm=slurm, accounting=accounting)
    sections: dict[str, Any] = {
        "identity.json": identity,
        "kubernetes.json": kubernetes,
        "slurm.json": slurm,
        "accounting.json": accounting,
        "customizations.json": customizations,
        "fingerprints.json": fingerprints,
        "findings.json": findings,
    }
    findings_path = bundle_dir / "findings.json"
    _preserve_stable_report_timestamp(findings_path, findings)
    for filename, payload in sections.items():
        _write_json_if_changed(bundle_dir / filename, payload)
    summary_text = _summary_markdown(
        identity=identity,
        findings=findings,
        bundle_dir=bundle_dir,
        guidance_lines=guidance_lines,
    )
    _write_text_if_changed(bundle_dir / "summary.md", summary_text)
    checksums = {
        filename: _sha256_file(bundle_dir / filename)
        for filename in SOPERATOR_DISCOVERY_SECTION_FILES
        if (bundle_dir / filename).is_file()
    }
    manifest: dict[str, Any] = {
        "schema": SOPERATOR_DISCOVERY_SCHEMA,
        "generated_at": _now_z(),
        "target_ref": normalized_target,
        **cluster_identity.as_metadata(),
        "source_kind": source_kind,
        "command": [str(item) for item in command or ()],
        "redaction": redaction,
        "bundle_dir": str(bundle_dir),
        "sections": {filename: filename for filename in SOPERATOR_DISCOVERY_SECTION_FILES},
        "checksums": checksums,
        "cxcli_version": "",
        "target_versions": _redact(target_versions or {}, redaction=redaction),
    }
    manifest_path = bundle_dir / SOPERATOR_DISCOVERY_MANIFEST_NAME
    _preserve_stable_manifest_timestamp(manifest_path, manifest)
    _write_json_if_changed(manifest_path, manifest)
    return manifest_path


def load_soperator_discovery_bundle(path: Path) -> dict[str, Any]:
    manifest_path = path / SOPERATOR_DISCOVERY_MANIFEST_NAME if path.is_dir() else path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError(f"Soperator discovery manifest is not a JSON object: {manifest_path}")
    if manifest.get("schema") != SOPERATOR_DISCOVERY_SCHEMA:
        raise ValueError(
            f"Unsupported Soperator discovery schema in {manifest_path}: {manifest.get('schema')}"
        )
    bundle_dir = manifest_path.parent
    checksums = manifest.get("checksums")
    if isinstance(checksums, Mapping):
        for filename, expected in checksums.items():
            section_path = bundle_dir / str(filename)
            if not section_path.is_file():
                raise ValueError(f"Soperator discovery section is missing: {section_path}")
            actual = _sha256_file(section_path)
            if str(expected) != actual:
                raise ValueError(f"Soperator discovery checksum mismatch for {section_path}")
    sections: dict[str, Any] = {}
    for filename in SOPERATOR_DISCOVERY_SECTION_FILES:
        section_path = bundle_dir / filename
        if not section_path.is_file():
            continue
        if filename.endswith(".json"):
            sections[filename] = json.loads(section_path.read_text(encoding="utf-8"))
        else:
            sections[filename] = section_path.read_text(encoding="utf-8")
    findings = sections.get("findings.json")
    kubernetes = sections.get("kubernetes.json")
    identity = sections.get("identity.json")
    report = findings.get("onboarding_report") if isinstance(findings, Mapping) else None
    snapshot = kubernetes.get("snapshot") if isinstance(kubernetes, Mapping) else None
    payload = copy.deepcopy(dict(manifest))
    if isinstance(identity, Mapping):
        for key in (
            "cluster_id",
            "cluster_name",
            "kube_context",
            "namespace",
            "release_name",
            "chart_version",
            "app_version",
            "source_version",
            "target_version",
            "current_k8s_version",
            "target_k8s_version",
            "state",
            "soperator_status",
            "jail_rootfs",
            "source_slurmcluster_ref",
        ):
            payload[key] = identity.get(key, "")
    payload.update(
        {
            "bundle_dir": str(bundle_dir),
            "manifest_path": str(manifest_path),
            "sections_payload": sections,
            "report": report if isinstance(report, Mapping) else {},
            "snapshot": snapshot if isinstance(snapshot, Mapping) else {},
        }
    )
    return payload
