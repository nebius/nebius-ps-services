"""Release-neutral topology projection for Soperator registration."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data
from .soperator_config_materialization import (
    _mapping_path_value,
    _non_empty_text,
    _soperator_nodeset_values_are_gpu,
    _soperator_nodesets_profiles,
)


def _live_slurm_cluster(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    resources = snapshot.get("soperator_resources")
    clusters = [
        resource
        for resource in resources or ()
        if isinstance(resource, Mapping)
        and _non_empty_text(resource.get("kind")).lower() == "slurmcluster"
    ]
    available = [
        cluster
        for cluster in clusters
        if _non_empty_text(_mapping_path_value(cluster, "status.phase")).lower() == "available"
    ]
    selected = available or clusters
    if len(selected) != 1:
        raise RuntimeError(
            "Soperator registration requires exactly one authoritative live SlurmCluster; "
            f"found {len(selected)} candidates."
        )
    return selected[0]


def _nodeset_topology(spec: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    """Project only release-neutral NodeSet topology into source config."""

    projected: dict[str, Any] = {"name": name}
    for field_name in (
        "replicas",
        "maxUnavailable",
        "maxConcurrentStartup",
        "ephemeralNodes",
        "initialNumberEphemeralNodes",
        "ephemeralTopologyWaitTimeout",
        "enableHostUserNamespace",
        "workerInitRandomDelaySeconds",
        "sssdDebugLevel",
        "sssdConfSecretRefName",
        "sssdLdapCAConfigMapRefName",
        "configMapRefSupervisord",
        "configMapRefSshd",
        "priorityClass",
    ):
        if field_name in spec:
            projected[field_name] = copy.deepcopy(to_plain_data(spec[field_name]))

    for field_name in ("nodeSelector", "affinity", "topology"):
        value = spec.get(field_name)
        if isinstance(value, Mapping):
            projected[field_name] = copy.deepcopy(to_plain_data(dict(value)))

    tolerations = spec.get("tolerations")
    if isinstance(tolerations, Sequence) and not isinstance(
        tolerations,
        (str, bytes, bytearray),
    ):
        projected["tolerations"] = [
            copy.deepcopy(to_plain_data(item)) for item in tolerations if isinstance(item, Mapping)
        ]

    gpu = spec.get("gpu")
    if isinstance(gpu, Mapping):
        projected_gpu: dict[str, Any] = {}
        if "enabled" in gpu:
            projected_gpu["enabled"] = gpu["enabled"] is True
        nvidia = gpu.get("nvidia")
        if isinstance(nvidia, Mapping) and "gdrCopyEnabled" in nvidia:
            projected_gpu["nvidia"] = {
                "gdrCopyEnabled": nvidia["gdrCopyEnabled"] is True,
            }
        if projected_gpu:
            projected["gpu"] = projected_gpu

    node_config = spec.get("nodeConfig")
    if isinstance(node_config, Mapping):
        projected_node_config = {
            field_name: copy.deepcopy(to_plain_data(node_config[field_name]))
            for field_name in ("features", "static", "dynamic", "gresConfig")
            if field_name in node_config
        }
        if projected_node_config:
            projected["nodeConfig"] = projected_node_config

    slurmd_resources = _mapping_path_value(spec, "slurmd.resources")
    if isinstance(slurmd_resources, Mapping):
        projected["slurmd"] = {
            "resources": copy.deepcopy(to_plain_data(dict(slurmd_resources))),
        }

    return projected


def _partition_topology(value: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    if "configType" in value:
        projected["configType"] = copy.deepcopy(to_plain_data(value["configType"]))
    partitions = value.get("partitions")
    if not isinstance(partitions, Sequence) or isinstance(
        partitions,
        (str, bytes, bytearray),
    ):
        return projected
    projected_partitions: list[dict[str, Any]] = []
    for partition in partitions:
        if not isinstance(partition, Mapping):
            continue
        projected_partition = {
            field_name: copy.deepcopy(to_plain_data(partition[field_name]))
            for field_name in ("name", "isAll", "nodeSetRefs", "config", "policy")
            if field_name in partition
        }
        if projected_partition:
            projected_partitions.append(projected_partition)
    projected["partitions"] = projected_partitions
    return projected


def _optional_service_topology(cluster: Mapping[str, Any]) -> dict[str, Any]:
    """Project optional service presence without carrying source-release payloads."""

    slurm_nodes = _mapping_path_value(cluster, "spec.slurmNodes")
    if not isinstance(slurm_nodes, Mapping):
        return {}
    fields_by_role = {
        "accounting": ("enabled",),
        "exporter": ("enabled", "size", "k8sNodeFilterName", "collectionInterval"),
        "rest": (
            "enabled",
            "size",
            "k8sNodeFilterName",
            "threadCount",
            "maxConnections",
        ),
    }
    projected: dict[str, Any] = {}
    for role, field_names in fields_by_role.items():
        role_spec = slurm_nodes.get(role)
        if not isinstance(role_spec, Mapping):
            continue
        role_values = {
            field_name: copy.deepcopy(to_plain_data(role_spec[field_name]))
            for field_name in field_names
            if field_name in role_spec
        }
        if role_values:
            projected[role] = role_values
    return projected


def soperator_registration_optional_service_topology_from_discovery(
    kubernetes: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the one authoritative discovery-time optional-service projection."""

    raw_clusters = kubernetes.get("slurmclusters")
    clusters = [
        item
        for item in raw_clusters or ()
        if isinstance(item, Mapping)
    ]
    available = [
        item
        for item in clusters
        if _non_empty_text(_mapping_path_value(item, "status.phase")).lower()
        == "available"
    ]
    selected = available or clusters
    if len(selected) != 1:
        raise RuntimeError(
            "Soperator discovery topology repair requires exactly one authoritative "
            f"SlurmCluster; found {len(selected)} candidates."
        )
    return _optional_service_topology(selected[0])


def _placements_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, list[str]]:
    placements: dict[str, list[str]] = {}
    node_groups = snapshot.get("node_groups")
    if not isinstance(node_groups, Mapping):
        return placements
    for raw_group_id, raw_group in node_groups.items():
        if not isinstance(raw_group, Mapping):
            continue
        group_id = normalize_component_token(raw_group_id)
        labels = raw_group.get("labels")
        labels = labels if isinstance(labels, Mapping) else {}
        role = normalize_component_token(
            labels.get("slurm.nebius.ai/nodeset")
            or labels.get("slurm.nebius.ai/nodeset-name")
            or raw_group.get("nodeset_name")
            or raw_group.get("placement_name")
        )
        if group_id and (
            role in {"system", "controller", "login", "accounting"} or role.startswith("worker")
        ):
            placements.setdefault(role, []).append(group_id)
    return {role: sorted(set(group_ids)) for role, group_ids in sorted(placements.items())}


def _pre_nodeset_worker_topology(
    cluster: Mapping[str, Any],
    *,
    placements: Mapping[str, Sequence[str]],
) -> dict[str, Any] | None:
    """Normalize the protected-data-plane worker shape into one canonical NodeSet."""

    worker_spec = _mapping_path_value(cluster, "spec.slurmNodes.worker")
    if not isinstance(worker_spec, Mapping):
        return None
    worker_placements = sorted(
        role for role in placements if normalize_component_token(role).startswith("worker")
    )
    if len(worker_placements) != 1:
        raise RuntimeError(
            "Soperator registration requires exactly one live worker placement to normalize "
            "an installed pre-NodeSet worker specification; "
            f"found {len(worker_placements)}."
        )
    nodeset = _nodeset_topology(worker_spec, name=worker_placements[0])
    size = worker_spec.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise RuntimeError(
            "Soperator registration requires a positive installed worker size before "
            "normalizing a pre-NodeSet worker specification."
        )
    nodeset["replicas"] = size
    is_gpu = _soperator_nodeset_values_are_gpu(nodeset)
    gdr_copy = worker_spec.get("enableGDRCopy")
    if gdr_copy is True and not is_gpu:
        raise RuntimeError(
            "Soperator registration found enableGDRCopy=true without a declared GPU worker "
            "resource and cannot normalize the installed topology safely."
        )
    if is_gpu:
        gpu: dict[str, Any] = {"enabled": True}
        if isinstance(gdr_copy, bool):
            gpu["nvidia"] = {"gdrCopyEnabled": gdr_copy}
        nodeset["gpu"] = gpu
    return nodeset


def soperator_registration_adoption_values_from_snapshot(
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Project non-secret live topology into the thin declarative adapter."""

    cluster = _live_slurm_cluster(snapshot)
    cluster_name = _non_empty_text(_mapping_path_value(cluster, "metadata.name"))
    if not cluster_name:
        raise RuntimeError("The authoritative live SlurmCluster has no name.")
    values: dict[str, Any] = {"clusterName": cluster_name}
    placements = _placements_from_snapshot(snapshot)

    resources = snapshot.get("soperator_resources")
    worker_nodesets: list[dict[str, Any]] = []
    has_cpu_worker = False
    has_gpu_worker = False
    for resource in resources or ():
        if not isinstance(resource, Mapping):
            continue
        if _non_empty_text(resource.get("kind")).lower() != "nodeset":
            continue
        name = normalize_component_token(_mapping_path_value(resource, "metadata.name"))
        if not name.startswith("worker"):
            continue
        spec = resource.get("spec")
        if not isinstance(spec, Mapping):
            raise RuntimeError(f"Live worker NodeSet '{name}' has no declarative spec.")
        nodeset = _nodeset_topology(spec, name=name)
        worker_nodesets.append(nodeset)
        if _soperator_nodeset_values_are_gpu(nodeset):
            has_gpu_worker = True
        else:
            has_cpu_worker = True
    if not worker_nodesets:
        normalized_worker = _pre_nodeset_worker_topology(
            cluster,
            placements=placements,
        )
        if normalized_worker is None:
            raise RuntimeError(
                "Soperator registration found no live worker NodeSet specifications."
            )
        worker_nodesets.append(normalized_worker)
        if _soperator_nodeset_values_are_gpu(normalized_worker):
            has_gpu_worker = True
        else:
            has_cpu_worker = True
    values["nodesets"] = sorted(worker_nodesets, key=lambda item: str(item["name"]))

    partition_configuration = _mapping_path_value(cluster, "spec.partitionConfiguration")
    if isinstance(partition_configuration, Mapping):
        values["partitionConfiguration"] = _partition_topology(partition_configuration)

    service_topology = _optional_service_topology(cluster)
    if service_topology:
        values["slurmNodes"] = service_topology

    if placements:
        values["placements"] = copy.deepcopy(to_plain_data(placements))

    profile = ""
    if has_cpu_worker and has_gpu_worker:
        profile = "nebius-mixed-v1"
    elif has_gpu_worker:
        profile = "nebius-gpu-v1"
    elif has_cpu_worker:
        profile = "nebius-cpu-v1"
    _default_profile, profiles = _soperator_nodesets_profiles()
    return values, profile if profile in profiles else ""


__all__ = ["soperator_registration_adoption_values_from_snapshot"]
