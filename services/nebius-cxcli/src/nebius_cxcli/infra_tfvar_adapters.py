"""Component-specific Terraform tfvar adapter helpers."""

from __future__ import annotations

from typing import Any

from .runtime_config import to_plain_data


def _cluster_overrides_payload(config: Any) -> dict[str, Any] | None:
    overrides = config.infra.mk8s.cluster_overrides
    if overrides is None:
        return None

    payload: dict[str, Any] = {}
    if overrides.name:
        payload["name"] = overrides.name
    if overrides.labels:
        payload["labels"] = overrides.labels
    if overrides.parent_id:
        payload["parent_id"] = overrides.parent_id
    if overrides.resource_version is not None:
        payload["resource_version"] = overrides.resource_version

    if overrides.control_plane is not None:
        control_plane: dict[str, Any] = {}
        if overrides.control_plane.subnet_id:
            control_plane["subnet_id"] = overrides.control_plane.subnet_id
        if overrides.control_plane.audit_logs is True:
            control_plane["audit_logs"] = {}
        if overrides.control_plane.etcd_cluster_size is not None:
            control_plane["etcd_cluster_size"] = overrides.control_plane.etcd_cluster_size
        if overrides.control_plane.version:
            control_plane["version"] = overrides.control_plane.version
        if (
            overrides.control_plane.endpoints is not None
            and overrides.control_plane.endpoints.public_endpoint is True
        ):
            control_plane["endpoints"] = {"public_endpoint": {}}
        if control_plane:
            payload["control_plane"] = control_plane

    if overrides.kube_network is not None and overrides.kube_network.service_cidrs:
        payload["kube_network"] = {"service_cidrs": overrides.kube_network.service_cidrs}

    return payload or None


def _count_or_percent_payload(config: Any) -> dict[str, int] | None:
    payload: dict[str, int] = {}
    if config.count is not None:
        payload["count"] = config.count
    if config.percent is not None:
        payload["percent"] = config.percent
    return payload or None


def _node_group_overrides_payload(config: Any) -> dict[str, Any] | None:
    if config is None:
        return None

    payload: dict[str, Any] = {}
    if config.name:
        payload["name"] = config.name
    if config.labels:
        payload["labels"] = config.labels
    if config.parent_id:
        payload["parent_id"] = config.parent_id
    if config.resource_version is not None:
        payload["resource_version"] = config.resource_version
    if config.version:
        payload["version"] = config.version
    if config.fixed_node_count is not None:
        payload["fixed_node_count"] = config.fixed_node_count
    if config.autoscaling is not None:
        autoscaling: dict[str, Any] = {}
        if config.autoscaling.min_node_count is not None:
            autoscaling["min_node_count"] = config.autoscaling.min_node_count
        if config.autoscaling.max_node_count is not None:
            autoscaling["max_node_count"] = config.autoscaling.max_node_count
        if autoscaling:
            payload["autoscaling"] = autoscaling
    if config.auto_repair is not None and config.auto_repair.conditions:
        payload["auto_repair"] = {
            "conditions": [
                to_plain_data(condition)
                for condition in config.auto_repair.conditions
            ]
        }
    if config.strategy is not None:
        strategy: dict[str, Any] = {}
        if config.strategy.drain_timeout:
            strategy["drain_timeout"] = config.strategy.drain_timeout
        if config.strategy.max_surge is not None:
            max_surge = _count_or_percent_payload(config.strategy.max_surge)
            if max_surge:
                strategy["max_surge"] = max_surge
        if config.strategy.max_unavailable is not None:
            max_unavailable = _count_or_percent_payload(config.strategy.max_unavailable)
            if max_unavailable:
                strategy["max_unavailable"] = max_unavailable
        if strategy:
            payload["strategy"] = strategy
    if config.template is not None:
        template: dict[str, Any] = {}
        if config.template.resources is not None:
            resources = to_plain_data(config.template.resources)
            if resources:
                template["resources"] = resources
        if config.template.boot_disk is not None:
            boot_disk = to_plain_data(config.template.boot_disk)
            if boot_disk:
                template["boot_disk"] = boot_disk
        if config.template.cloud_init_user_data:
            template["cloud_init_user_data"] = config.template.cloud_init_user_data
        if config.template.filesystems:
            template["filesystems"] = [
                to_plain_data(filesystem)
                for filesystem in config.template.filesystems
            ]
        if config.template.gpu_cluster is not None:
            template["gpu_cluster"] = to_plain_data(config.template.gpu_cluster)
        if config.template.gpu_settings is not None:
            template["gpu_settings"] = to_plain_data(config.template.gpu_settings)
        if config.template.metadata is not None and config.template.metadata.labels:
            template["metadata"] = {"labels": config.template.metadata.labels}
        if config.template.network_interfaces:
            network_interfaces: list[dict[str, Any]] = []
            for interface in config.template.network_interfaces:
                entry: dict[str, Any] = {}
                if interface.subnet_id:
                    entry["subnet_id"] = interface.subnet_id
                if interface.public_ip_address is True:
                    entry["public_ip_address"] = {}
                if entry:
                    network_interfaces.append(entry)
            if network_interfaces:
                template["network_interfaces"] = network_interfaces
        if config.template.os:
            template["os"] = config.template.os
        if config.template.preemptible is True:
            template["preemptible"] = {}
        if config.template.reservation_policy is not None:
            reservation_policy = to_plain_data(config.template.reservation_policy)
            if reservation_policy:
                template["reservation_policy"] = reservation_policy
        if config.template.service_account_id:
            template["service_account_id"] = config.template.service_account_id
        if config.template.taints:
            template["taints"] = [
                to_plain_data(taint) for taint in config.template.taints
            ]
        if template:
            payload["template"] = template

    return payload or None


def _mk8s_tfvar_adapter_payload(config: Any, *, cluster_public_endpoint: bool) -> dict[str, Any]:
    gpu_nodes = config.infra.mk8s.gpu_nodes
    payload: dict[str, Any] = {
        "subnet_id": config.infra.mk8s.subnet_id,
        "cpu_nodes_count": config.infra.mk8s.cpu_nodes.count,
        "cpu_nodes_platform": config.infra.mk8s.cpu_nodes.platform,
        "cpu_nodes_preset": config.infra.mk8s.cpu_nodes.preset,
        "cpu_nodes_preemptible": config.infra.mk8s.cpu_nodes.preemptible,
        "cpu_nodes_public_ips": config.infra.mk8s.cpu_nodes.public_ips,
        "gpu_enabled": gpu_nodes.enabled,
        "gpu_node_groups": gpu_nodes.node_groups,
        "gpu_nodes_count_per_group": gpu_nodes.nodes_per_group,
        "gpu_nodes_platform": gpu_nodes.platform,
        "gpu_nodes_preset": gpu_nodes.preset,
        "gpu_nodes_preemptible": gpu_nodes.preemptible,
        "gpu_nodes_public_ips": gpu_nodes.public_ips,
        "gpu_driverfull_image": gpu_nodes.driverfull_image,
        "infiniband_fabric": config.infra.mk8s.infiniband_fabric,
        "mk8s_cluster_public_endpoint": cluster_public_endpoint,
    }
    if gpu_nodes.mig.enabled:
        payload["mig_strategy"] = gpu_nodes.mig.strategy
        if gpu_nodes.mig.parted_config:
            payload["mig_parted_config"] = gpu_nodes.mig.parted_config
    return payload


def apply_mk8s_tfvar_adapters(
    *,
    config: Any,
    tfvars: dict[str, Any],
    cluster_public_endpoint: bool,
) -> None:
    cluster_overrides = config.infra.mk8s.cluster_overrides
    if cluster_overrides is not None and cluster_overrides.control_plane is not None:
        if cluster_overrides.control_plane.version:
            tfvars["k8s_version"] = cluster_overrides.control_plane.version
        if cluster_overrides.control_plane.etcd_cluster_size is not None:
            tfvars["etcd_cluster_size"] = cluster_overrides.control_plane.etcd_cluster_size
        if cluster_overrides.control_plane.subnet_id:
            tfvars["subnet_id"] = cluster_overrides.control_plane.subnet_id

    cluster_overrides_payload = _cluster_overrides_payload(config)
    if cluster_overrides_payload is not None:
        tfvars["mk8s_cluster_overrides"] = cluster_overrides_payload

    cpu_node_group_overrides = _node_group_overrides_payload(
        config.infra.mk8s.cpu_node_group_overrides
    )
    if cpu_node_group_overrides is not None:
        tfvars["mk8s_cpu_node_group_overrides"] = cpu_node_group_overrides

    gpu_node_group_overrides = _node_group_overrides_payload(
        config.infra.mk8s.gpu_node_group_overrides
    )
    if gpu_node_group_overrides is not None:
        tfvars["mk8s_gpu_node_group_overrides"] = gpu_node_group_overrides

    for key, value in _mk8s_tfvar_adapter_payload(
        config,
        cluster_public_endpoint=cluster_public_endpoint,
    ).items():
        tfvars.setdefault(key, value)


def apply_wireguard_tfvar_defaults(*, config: Any, tfvars: dict[str, Any]) -> None:
    tfvars.setdefault(
        "wireguard_clients",
        [to_plain_data(client) for client in config.infra.wireguard_jumphost.clients],
    )

