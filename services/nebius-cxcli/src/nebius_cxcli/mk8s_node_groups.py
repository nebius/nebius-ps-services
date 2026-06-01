"""Canonical MK8s cluster and node-group input helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

LEGACY_MK8S_INPUT_KEYS = frozenset(
    {
        "cluster_name",
        "subnet_id",
        "k8s_version",
        "mk8s_cluster_public_endpoint",
        "kube_network_service_cidrs",
        "etcd_cluster_size",
        "cpu_nodes_count",
        "cpu_nodes_platform",
        "cpu_nodes_preset",
        "cpu_nodes_os",
        "cpu_nodes_boot_disk_size_gib",
        "cpu_nodes_boot_disk_type",
        "cpu_nodes_preemptible",
        "cpu_nodes_public_ips",
        "gpu_enabled",
        "gpu_node_groups",
        "gpu_nodes_count_per_group",
        "gpu_nodes_platform",
        "gpu_nodes_preset",
        "gpu_nodes_os",
        "gpu_nodes_boot_disk_size_gib",
        "gpu_nodes_boot_disk_type",
        "gpu_nodes_preemptible",
        "gpu_nodes_public_ips",
        "gpu_stack_source",
        "gpu_stack_preset",
        "infiniband_fabric",
        "mk8s_cluster_overrides",
        "mk8s_cpu_node_group_overrides",
        "mk8s_gpu_node_group_overrides",
    }
)


@dataclass(frozen=True)
class Mk8sNodeGroup:
    key: str
    name: str
    gpu: bool
    platform: str
    preset: str
    node_count: int | None
    autoscaling_min_node_count: int | None
    autoscaling_max_node_count: int | None
    gpu_stack_source: str
    gpu_stack_preset: str
    gpu_cluster_key: str
    gpu_cluster_id: str
    reservation_policy: str
    reservation_ids: tuple[str, ...]
    node_labels: Mapping[str, Any]
    labels: Mapping[str, Any]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, float):
            parsed = int(value) if value.is_integer() else -1
        else:
            parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _autoscaling_enabled(raw_group: Mapping[str, Any]) -> bool:
    autoscaling = _mapping(raw_group.get("autoscaling"))
    return bool(autoscaling) and _bool(autoscaling.get("enabled"), default=True)


def _infer_gpu_stack_source(raw_group: Mapping[str, Any], *, gpu: bool) -> str:
    explicit = _text(raw_group.get("gpu_stack_source")).lower()
    if explicit:
        return explicit
    if not gpu:
        return "operator_managed"
    labels = {
        **dict(_mapping(raw_group.get("labels"))),
        **dict(_mapping(raw_group.get("node_labels"))),
    }
    if (
        _bool(labels.get("nebius.com/driverful"), default=False)
        or _text(labels.get("nebius.com/drivers-preset"))
        or _text(labels.get("nebius.com/nvidia_driver_version"))
    ):
        return "nebius_image"
    return "nebius_image"


def cluster_inputs(inputs: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return _mapping(_mapping(inputs).get("cluster"))


def cluster_name(inputs: Mapping[str, Any] | None, *, fallback: str = "") -> str:
    return _text(cluster_inputs(inputs).get("cluster_name")) or fallback


def cluster_subnet_id(inputs: Mapping[str, Any] | None) -> str:
    return _text(cluster_inputs(inputs).get("subnet_id"))


def cluster_public_endpoint_enabled(inputs: Mapping[str, Any] | None) -> bool:
    return _bool(cluster_inputs(inputs).get("public_endpoint"), default=False)


def cluster_service_cidrs(inputs: Mapping[str, Any] | None) -> tuple[str, ...]:
    kube_network = _mapping(cluster_inputs(inputs).get("kube_network"))
    raw_cidrs = kube_network.get("service_cidrs", [])
    if isinstance(raw_cidrs, list):
        return tuple(_text(item) for item in raw_cidrs if _text(item))
    return ()


def legacy_mk8s_input_keys(inputs: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(inputs, Mapping):
        return ()
    return tuple(sorted(key for key in inputs if key in LEGACY_MK8S_INPUT_KEYS))


def iter_node_groups(inputs: Mapping[str, Any] | None) -> tuple[Mk8sNodeGroup, ...]:
    raw_groups = _mapping(_mapping(inputs).get("node_groups"))
    groups: list[Mk8sNodeGroup] = []
    for raw_key, raw_group in raw_groups.items():
        if not isinstance(raw_group, Mapping):
            continue
        if raw_group.get("enabled") is False:
            continue
        key = _text(raw_key)
        if not key:
            continue
        gpu = _bool(raw_group.get("gpu"), default=False)
        autoscaling = _mapping(raw_group.get("autoscaling"))
        autoscaling_enabled = _autoscaling_enabled(raw_group)
        reservation = _mapping(raw_group.get("reservation"))
        raw_reservation_ids = reservation.get("reservation_ids", [])
        reservation_ids = (
            tuple(_text(item) for item in raw_reservation_ids if _text(item))
            if isinstance(raw_reservation_ids, list)
            else ()
        )
        groups.append(
            Mk8sNodeGroup(
                key=key,
                name=_text(raw_group.get("name")) or key,
                gpu=gpu,
                platform=_text(raw_group.get("platform")),
                preset=_text(raw_group.get("preset")),
                node_count=_positive_int_or_none(raw_group.get("node_count")),
                autoscaling_min_node_count=(
                    _positive_int_or_none(autoscaling.get("min_node_count"))
                    if autoscaling_enabled
                    else None
                ),
                autoscaling_max_node_count=(
                    _positive_int_or_none(autoscaling.get("max_node_count"))
                    if autoscaling_enabled
                    else None
                ),
                gpu_stack_source=_infer_gpu_stack_source(raw_group, gpu=gpu),
                gpu_stack_preset=_text(raw_group.get("gpu_stack_preset")),
                gpu_cluster_key=_text(raw_group.get("gpu_cluster_key")),
                gpu_cluster_id=_text(raw_group.get("gpu_cluster_id")),
                reservation_policy=_text(reservation.get("policy")) or ("FORBID" if gpu else ""),
                reservation_ids=reservation_ids,
                node_labels=_mapping(raw_group.get("node_labels")),
                labels=_mapping(raw_group.get("labels")),
            )
        )
    return tuple(groups)


def gpu_node_groups(inputs: Mapping[str, Any] | None) -> tuple[Mk8sNodeGroup, ...]:
    return tuple(group for group in iter_node_groups(inputs) if group.gpu)


def gpu_enabled(inputs: Mapping[str, Any] | None) -> bool:
    return bool(gpu_node_groups(inputs))


def gpu_cluster_enabled(inputs: Mapping[str, Any] | None) -> bool:
    return any(group.gpu_cluster_key or group.gpu_cluster_id for group in gpu_node_groups(inputs))


def gpu_cluster_fabric(inputs: Mapping[str, Any] | None, group: Mk8sNodeGroup) -> str:
    if not group.gpu_cluster_key:
        return ""
    gpu_clusters = _mapping(_mapping(inputs).get("gpu_clusters"))
    return _text(_mapping(gpu_clusters.get(group.gpu_cluster_key)).get("infiniband_fabric"))


def first_gpu_node_group(inputs: Mapping[str, Any] | None) -> Mk8sNodeGroup | None:
    groups = gpu_node_groups(inputs)
    return groups[0] if groups else None
