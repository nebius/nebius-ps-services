"""Deterministic renderers for Terraform and Flux artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import InstancePaths
from .schema import ConfigV1, HelmComponentConfig
from .templates import (
    DEFAULT_PLATFORM_INFRA_SOURCE,
    NEBIUS_PROVIDER_SOURCE,
    NEBIUS_PROVIDER_VERSION,
)

DEFAULT_APP_VALUES: dict[str, dict[str, Any]] = {
    "envoy_gateway": {
        "service": {
            "type": "LoadBalancer",
            "annotations": {"nebius.com/load-balancer-type": "internal"},
        }
    },
    "cert_manager": {"installCRDs": True},
    "external_dns": {"provider": "nebius"},
    "observability": {"enable_nebius_o11y_agent": True, "enable_grafana": True},
    "external_secrets": {},
    "n8n": {},
}


@dataclass(frozen=True)
class RenderResult:
    files_written: list[Path]


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _join_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
            continue
        result[key] = value
    return result


def _terraform_backend_block(config: ConfigV1, paths: InstancePaths) -> str:
    state_prefix = config.infra.object_storage.state_bucket.prefix
    state_key = _join_key(
        state_prefix,
        paths.instance_slug,
        config.client_info.env.value,
        config.client_info.cluster_name,
        "platform.tfstate",
    )
    endpoint = f"https://storage.{config.client_info.nebius.region_id}.nebius.cloud"

    return (
        "terraform {\n"
        "  required_providers {\n"
        "    nebius = {\n"
        f'      source  = "{NEBIUS_PROVIDER_SOURCE}"\n'
        f'      version = "{NEBIUS_PROVIDER_VERSION}"\n'
        "    }\n"
        "  }\n\n"
        '  backend "s3" {\n'
        f'    bucket  = "{config.infra.object_storage.state_bucket.name}"\n'
        f'    key     = "{state_key}"\n'
        f'    region  = "{config.client_info.nebius.region_id}"\n\n'
        "    endpoints = {\n"
        f'      s3 = "{endpoint}"\n'
        "    }\n\n"
        f"    use_lockfile = {'true' if config.infra.object_storage.state_bucket.use_lockfile else 'false'}\n\n"
        f"    encrypt = {'true' if config.infra.object_storage.state_bucket.encryption else 'false'}\n\n"
        "    skip_credentials_validation = true\n"
        "    skip_region_validation      = true\n"
        "    skip_requesting_account_id  = true\n"
        "    skip_metadata_api_check     = true\n"
        "  }\n"
        "}\n\n"
        'provider "nebius" {\n'
        "  service_account = {\n"
        '    account_id_env       = "NEBIUS_SA_ID"\n'
        '    public_key_id_env    = "NEBIUS_AUTH_PUBLIC_KEY_ID"\n'
        '    private_key_file_env = "NEBIUS_AUTH_PRIVATE_KEY_FILE"\n'
        "  }\n"
        "}\n"
    )


def _terraform_main_block(tfvars_payload: dict[str, Any]) -> str:
    module_source = os.environ.get(
        "NEBIUS_CXCLI_PLATFORM_INFRA_SOURCE", DEFAULT_PLATFORM_INFRA_SOURCE
    )
    lines = [
        "locals {",
        '  rendered_inputs = jsondecode(file("${path.module}/terraform.auto.tfvars.json"))',
        "}",
        "",
        'module "customer_platform" {',
        f'  source = "{module_source}"',
    ]

    for key in sorted(tfvars_payload.keys()):
        lines.append(f"  {key} = local.rendered_inputs.{key}")

    lines.extend(["}", ""])
    return "\n".join(lines)


def _cluster_overrides_payload(config: ConfigV1) -> dict[str, Any] | None:
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
                condition.model_dump(exclude_none=True)
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
            resources = config.template.resources.model_dump(exclude_none=True)
            if resources:
                template["resources"] = resources
        if config.template.boot_disk is not None:
            boot_disk = config.template.boot_disk.model_dump(exclude_none=True)
            if boot_disk:
                template["boot_disk"] = boot_disk
        if config.template.cloud_init_user_data:
            template["cloud_init_user_data"] = config.template.cloud_init_user_data
        if config.template.filesystems:
            template["filesystems"] = [
                filesystem.model_dump(exclude_none=True)
                for filesystem in config.template.filesystems
            ]
        if config.template.gpu_cluster is not None:
            template["gpu_cluster"] = config.template.gpu_cluster.model_dump(exclude_none=True)
        if config.template.gpu_settings is not None:
            template["gpu_settings"] = config.template.gpu_settings.model_dump(exclude_none=True)
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
            reservation_policy = config.template.reservation_policy.model_dump(exclude_none=True)
            if reservation_policy:
                template["reservation_policy"] = reservation_policy
        if config.template.service_account_id:
            template["service_account_id"] = config.template.service_account_id
        if config.template.taints:
            template["taints"] = [
                taint.model_dump(exclude_none=True) for taint in config.template.taints
            ]
        if template:
            payload["template"] = template

    return payload or None


def _terraform_tfvars(config: ConfigV1) -> dict[str, Any]:
    cluster_overrides = config.infra.mk8s.cluster_overrides
    cluster_public_endpoint = config.infra.mk8s.api_endpoint.public
    if (
        cluster_overrides is not None
        and cluster_overrides.control_plane is not None
        and cluster_overrides.control_plane.endpoints is not None
        and cluster_overrides.control_plane.endpoints.public_endpoint is not None
    ):
        cluster_public_endpoint = cluster_overrides.control_plane.endpoints.public_endpoint

    gpu_nodes = config.infra.mk8s.gpu_nodes
    ssh_key = config.infra.ssh_public_key
    if not ssh_key:
        raise ValueError("infra.ssh_public_key is empty; provide an inline public key")
    mysterybox = config.infra.mysterybox
    wireguard = config.infra.wireguard_jumphost
    ssh_jump_host = config.infra.ssh_jumphost
    tfvars: dict[str, Any] = {
        "tenant_id": config.client_info.nebius.tenant_id,
        "parent_id": config.client_info.nebius.project_id,
        "region": config.client_info.nebius.region_id,
        "cluster_name": config.client_info.cluster_name,
        "subnet_id": config.infra.mk8s.subnet_id,
        "ssh_user_name": config.infra.ssh_user_name,
        "ssh_public_key": ssh_key,
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
        "managed_postgresql_enabled": config.infra.managed_postgresql.enabled,
        "managed_postgresql_name": config.infra.managed_postgresql.name,
        "managed_postgresql_tier": config.infra.managed_postgresql.tier,
        "managed_postgresql_storage_gib": config.infra.managed_postgresql.storage_gib,
        "managed_postgresql_postgresql_version": (
            config.infra.managed_postgresql.postgresql_version
        ),
        "managed_postgresql_public_access": config.infra.managed_postgresql.public_access,
        "sfs_enabled": config.infra.sfs.enabled,
        "sfs_name": config.infra.sfs.name,
        "sfs_size_gib": config.infra.sfs.size_gib,
        "sfs_block_size_kib": config.infra.sfs.block_size_kib,
        "sfs_type": config.infra.sfs.type,
        "state_bucket_manage": config.infra.object_storage.state_bucket.manage,
        "state_bucket_name": config.infra.object_storage.state_bucket.name,
        "state_bucket_prefix": config.infra.object_storage.state_bucket.prefix,
        "state_bucket_use_lockfile": config.infra.object_storage.state_bucket.use_lockfile,
        "state_bucket_versioning_policy": config.infra.object_storage.state_bucket.versioning_policy,
        "state_bucket_object_audit_logging": config.infra.object_storage.state_bucket.object_audit_logging,
        "state_bucket_protect_from_destroy": (
            config.infra.object_storage.state_bucket.protect_from_destroy
        ),
        "inventory_bucket_manage": config.infra.object_storage.inventory_bucket.manage,
        "inventory_bucket_name": config.infra.object_storage.inventory_bucket.name,
        "inventory_bucket_prefix": config.infra.object_storage.inventory_bucket.prefix,
        "inventory_bucket_versioning_policy": (
            config.infra.object_storage.inventory_bucket.versioning_policy
        ),
        "inventory_bucket_object_audit_logging": (
            config.infra.object_storage.inventory_bucket.object_audit_logging
        ),
        "inventory_bucket_protect_from_destroy": (
            config.infra.object_storage.inventory_bucket.protect_from_destroy
        ),
        "mysterybox_enabled": mysterybox.enabled,
        "wireguard_enabled": wireguard.enabled,
        "wireguard_name": wireguard.name,
        "wireguard_platform": wireguard.platform,
        "wireguard_preset": wireguard.preset,
        "wireguard_create_public_ip_allocation": wireguard.create_public_ip_allocation,
        "wireguard_public_ip_allocation_id": wireguard.public_ip_allocation_id,
        "wireguard_public_ip_allocation_name": wireguard.public_ip_allocation_name,
        "wireguard_boot_disk_size_gib": wireguard.boot_disk_size_gib,
        "wireguard_boot_disk_block_size_bytes": wireguard.boot_disk_block_size_bytes,
        "wireguard_boot_disk_type": wireguard.boot_disk_type,
        "wireguard_source_image_family": wireguard.source_image_family,
        "wireguard_tunnel_cidr": wireguard.tunnel_cidr,
        "wireguard_listen_port": wireguard.listen_port,
        "wireguard_nat_mode": wireguard.nat_mode,
        "wireguard_endpoint_host": wireguard.endpoint_host,
        "wireguard_clients": [client.model_dump() for client in wireguard.clients],
        "ssh_jumphost_enabled": ssh_jump_host.enabled,
        "ssh_jumphost_name": ssh_jump_host.name,
        "ssh_jumphost_platform": ssh_jump_host.platform,
        "ssh_jumphost_preset": ssh_jump_host.preset,
        "ssh_jumphost_create_public_ip_allocation": ssh_jump_host.create_public_ip_allocation,
        "ssh_jumphost_public_ip_allocation_id": ssh_jump_host.public_ip_allocation_id,
        "ssh_jumphost_public_ip_allocation_name": ssh_jump_host.public_ip_allocation_name,
        "ssh_jumphost_allowed_cidrs": ssh_jump_host.allowed_cidrs,
        "ssh_jumphost_boot_disk_size_gib": ssh_jump_host.boot_disk_size_gib,
        "ssh_jumphost_boot_disk_block_size_bytes": ssh_jump_host.boot_disk_block_size_bytes,
        "ssh_jumphost_boot_disk_type": ssh_jump_host.boot_disk_type,
        "ssh_jumphost_source_image_family": ssh_jump_host.source_image_family,
    }

    if gpu_nodes.mig.enabled:
        tfvars["mig_strategy"] = gpu_nodes.mig.strategy
        if gpu_nodes.mig.parted_config:
            tfvars["mig_parted_config"] = gpu_nodes.mig.parted_config

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

    return tfvars


def _helm_repository_doc(name: str, url: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "30m", "url": url},
    }


def _oci_repository_doc(name: str, url: str, tag: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "30m", "url": url, "ref": {"tag": tag}},
    }


def _namespace_doc(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
    }


def _cilium_config_enable_egress_doc() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "cilium-config", "namespace": "kube-system"},
        "data": {"enable-ipv4-egress-gateway": "true"},
    }


def _cilium_daemonset_restart_doc() -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": "cilium", "namespace": "kube-system"},
        "spec": {
            "template": {
                "metadata": {"annotations": {"nebius-cxcli.io/restarted-for": "egress-gateway"}}
            }
        },
    }


def _cilium_operator_restart_doc() -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "cilium-operator", "namespace": "kube-system"},
        "spec": {
            "template": {
                "metadata": {"annotations": {"nebius-cxcli.io/restarted-for": "egress-gateway"}}
            }
        },
    }


def _cilium_egress_nodes_policy_doc() -> dict[str, Any]:
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumClusterwideNetworkPolicy",
        "metadata": {"name": "restrict-nodeports-and-kubelet-egress-nodes"},
        "spec": {
            "nodeSelector": {"matchLabels": {"io.cilium/egress-gateway": "true"}},
            "ingress": [
                {
                    "fromCIDR": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
                    "toPorts": [
                        {
                            "ports": [
                                {"port": "10250", "protocol": "TCP"},
                                {"port": "30000", "endPort": 32767, "protocol": "TCP"},
                            ]
                        }
                    ],
                },
                {"fromEntities": ["cluster", "kube-apiserver"]},
            ],
            "egress": [{"toEntities": ["all"]}],
        },
    }


def _file_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in value)


def _join_posix_path(base: str, suffix: str | None) -> str:
    if not suffix:
        return base.rstrip("/")
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def _helm_release_doc(
    *,
    release_name: str,
    namespace: str,
    repo_name: str,
    component: HelmComponentConfig,
    values: dict[str, Any],
) -> dict[str, Any]:
    chart_spec: dict[str, Any] = {
        "chart": component.chart.name,
        "version": component.chart.version,
        "sourceRef": {
            "kind": "HelmRepository",
            "name": repo_name,
            "namespace": "flux-system",
        },
    }
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": {
            "interval": "5m",
            "chart": {"spec": chart_spec},
            "values": values,
        },
    }


def _helm_release_doc_with_chart_ref(
    *,
    release_name: str,
    namespace: str,
    source_name: str,
    source_kind: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": {
            "interval": "5m",
            "chartRef": {
                "kind": source_kind,
                "name": source_name,
                "namespace": "flux-system",
            },
            "values": values,
        },
    }


def _mysterybox_bridge_deployment_doc(
    *,
    namespace: str,
    service_name: str,
    service_port: int,
    image: str,
    auth_secret_name: str,
    bridge_auth_enabled: bool,
    bridge_auth_header_name: str,
    bridge_auth_secret_name: str,
    bridge_auth_secret_key: str,
) -> dict[str, Any]:
    env_from: list[dict[str, Any]] = [{"secretRef": {"name": auth_secret_name}}]
    env: list[dict[str, Any]] = [{"name": "LISTEN_PORT", "value": str(service_port)}]
    if bridge_auth_enabled:
        env.append({"name": "MYSTERYBOX_WEBHOOK_AUTH_HEADER", "value": bridge_auth_header_name})
        env.append(
            {
                "name": "MYSTERYBOX_WEBHOOK_AUTH_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": bridge_auth_secret_name,
                        "key": bridge_auth_secret_key,
                    }
                },
            }
        )

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/name": service_name},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app.kubernetes.io/name": service_name}},
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": service_name}},
                "spec": {
                    "containers": [
                        {
                            "name": "bridge",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "/bin/sh",
                                "-ec",
                                (
                                    "exec gunicorn --workers=${BRIDGE_GUNICORN_WORKERS:-2} "
                                    "--bind 0.0.0.0:${LISTEN_PORT:-8080} app:create_app()"
                                ),
                            ],
                            "envFrom": env_from,
                            "env": env,
                            "ports": [{"containerPort": service_port, "name": "http"}],
                            "readinessProbe": {
                                "httpGet": {"path": "/healthz", "port": "http"},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 15,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/healthz", "port": "http"},
                                "initialDelaySeconds": 30,
                                "periodSeconds": 20,
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                        }
                    ],
                },
            },
        },
    }


def _mysterybox_bridge_service_doc(
    *,
    namespace: str,
    service_name: str,
    service_port: int,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
        },
        "spec": {
            "selector": {"app.kubernetes.io/name": service_name},
            "ports": [{"name": "http", "port": service_port, "targetPort": "http"}],
        },
    }


def _mysterybox_cluster_secret_store_doc(
    *,
    store_name: str,
    bridge_service_name: str,
    bridge_namespace: str,
    bridge_service_port: int,
    bridge_auth_enabled: bool,
    bridge_auth_header_name: str,
    bridge_auth_secret_name: str,
    bridge_auth_secret_namespace: str,
    bridge_auth_secret_key: str,
) -> dict[str, Any]:
    webhook_provider: dict[str, Any] = {
        "url": (
            "http://"
            f"{bridge_service_name}.{bridge_namespace}.svc.cluster.local:{bridge_service_port}"
            "/v1/secret?secret={{ .remoteRef.key }}&key={{ .remoteRef.property }}"
            "&version={{ .remoteRef.version }}"
        ),
        "method": "GET",
        "result": {"jsonPath": "$.value"},
    }
    if bridge_auth_enabled:
        webhook_provider["headers"] = {
            bridge_auth_header_name: f"{{{{ .bridgeAuth.{bridge_auth_secret_key} }}}}"
        }
        secret_ref: dict[str, Any] = {"name": bridge_auth_secret_name}
        if bridge_auth_secret_namespace:
            secret_ref["namespace"] = bridge_auth_secret_namespace
        webhook_provider["secrets"] = [{"name": "bridgeAuth", "secretRef": secret_ref}]

    return {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ClusterSecretStore",
        "metadata": {"name": store_name},
        "spec": {"provider": {"webhook": webhook_provider}},
    }


def _mysterybox_external_secret_doc(
    *,
    name: str,
    namespace: str,
    refresh_interval: str,
    store_name: str,
    target_secret_name: str,
    creation_policy: str,
    deletion_policy: str,
    data_items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ExternalSecret",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "refreshInterval": refresh_interval,
            "secretStoreRef": {"name": store_name, "kind": "ClusterSecretStore"},
            "target": {
                "name": target_secret_name,
                "creationPolicy": creation_policy,
                "deletionPolicy": deletion_policy,
            },
            "data": data_items,
        },
    }


def _render_flux(config: ConfigV1, paths: InstancePaths) -> list[Path]:
    files: list[Path] = []

    platform = config.apps.platform
    workloads = config.apps.workloads

    enabled_components = [
        ("platform", "envoy-gateway", "envoy", "envoy_gateway", platform.envoy_gateway),
        ("platform", "cert-manager", "cert-manager", "cert_manager", platform.cert_manager),
        (
            "platform",
            "external-dns",
            "external-dns",
            "external_dns",
            platform.external_dns,
        ),
        (
            "platform",
            "observability",
            "observability",
            "observability",
            platform.observability,
        ),
        (
            "platform",
            "external-secrets",
            "external-secrets",
            "external_secrets",
            platform.external_secrets,
        ),
        ("workloads", "n8n", "n8n", "n8n", workloads.n8n),
    ]

    resources = ["./sources/helm-repositories.yaml"]
    repositories: list[dict[str, Any]] = []
    seen_repo_names: set[str] = set()
    namespace_files_written: set[str] = set()

    for scope, release_name, repo_name, defaults_key, component in enabled_components:
        if not component.enabled:
            continue
        if repo_name not in seen_repo_names:
            repositories.append(_helm_repository_doc(repo_name, component.chart.repo))
            seen_repo_names.add(repo_name)

        values = _deep_merge(DEFAULT_APP_VALUES[defaults_key], component.values)
        release_doc = _helm_release_doc(
            release_name=release_name,
            namespace=component.namespace,
            repo_name=repo_name,
            component=component,
            values=values,
        )
        relative_file = Path("apps") / scope / f"{release_name}-helmrelease.yaml"
        absolute_file = paths.flux_dir / relative_file
        _write_text(absolute_file, yaml.safe_dump(release_doc, sort_keys=False))
        files.append(absolute_file)
        resources.append(f"./{relative_file.as_posix()}")

    if config.infra.mk8s.egress_gateway.enabled:
        egress_files: list[tuple[Path, dict[str, Any]]] = [
            (
                Path("apps/platform/cilium-config-egress-gateway.yaml"),
                _cilium_config_enable_egress_doc(),
            ),
            (
                Path("apps/platform/cilium-daemonset-restart-egress-gateway.yaml"),
                _cilium_daemonset_restart_doc(),
            ),
            (
                Path("apps/platform/cilium-operator-restart-egress-gateway.yaml"),
                _cilium_operator_restart_doc(),
            ),
            (
                Path("apps/platform/cilium-egress-nodes-network-policy.yaml"),
                _cilium_egress_nodes_policy_doc(),
            ),
        ]
        for relative_file, doc in egress_files:
            absolute_file = paths.flux_dir / relative_file
            _write_text(absolute_file, yaml.safe_dump(doc, sort_keys=False))
            files.append(absolute_file)
            resources.append(f"./{relative_file.as_posix()}")

    if config.infra.sfs.csi.enabled:
        source_name = "csi-mounted-fs-path"
        csi_storage_class_name = f"{source_name}-sc"
        if source_name not in seen_repo_names:
            repositories.append(
                _oci_repository_doc(
                    source_name,
                    config.infra.sfs.csi.chart_url,
                    config.infra.sfs.csi.chart_version,
                )
            )
            seen_repo_names.add(source_name)

        if config.infra.sfs.csi.create_namespace:
            namespace_relative = Path(
                f"apps/platform/namespace-{_file_slug(config.infra.sfs.csi.namespace)}.yaml"
            )
            namespace_absolute = paths.flux_dir / namespace_relative
            _write_text(
                namespace_absolute,
                yaml.safe_dump(_namespace_doc(config.infra.sfs.csi.namespace), sort_keys=False),
            )
            files.append(namespace_absolute)
            resources.append(f"./{namespace_relative.as_posix()}")
            namespace_files_written.add(config.infra.sfs.csi.namespace)

        csi_release_doc = _helm_release_doc_with_chart_ref(
            release_name=source_name,
            namespace=config.infra.sfs.csi.namespace,
            source_name=source_name,
            source_kind="OCIRepository",
            values={"dataDir": config.infra.sfs.csi.data_dir},
        )
        csi_release_relative = Path("apps/platform/csi-mounted-fs-path-helmrelease.yaml")
        csi_release_absolute = paths.flux_dir / csi_release_relative
        _write_text(csi_release_absolute, yaml.safe_dump(csi_release_doc, sort_keys=False))
        files.append(csi_release_absolute)
        resources.append(f"./{csi_release_relative.as_posix()}")

        if config.infra.sfs.csi.mode == "dynamic":
            for pvc in config.infra.sfs.csi.pvcs:
                if pvc.create_namespace and pvc.namespace not in namespace_files_written:
                    namespace_relative = Path(
                        f"apps/workloads/namespace-{_file_slug(pvc.namespace)}.yaml"
                    )
                    namespace_absolute = paths.flux_dir / namespace_relative
                    _write_text(
                        namespace_absolute,
                        yaml.safe_dump(_namespace_doc(pvc.namespace), sort_keys=False),
                    )
                    files.append(namespace_absolute)
                    resources.append(f"./{namespace_relative.as_posix()}")
                    namespace_files_written.add(pvc.namespace)

                pvc_doc: dict[str, Any] = {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": pvc.name,
                        "namespace": pvc.namespace,
                    },
                    "spec": {
                        "accessModes": pvc.access_modes,
                        "resources": {"requests": {"storage": pvc.size}},
                        "storageClassName": csi_storage_class_name,
                    },
                }
                pvc_relative = Path(
                    f"apps/workloads/pvc-{_file_slug(pvc.namespace)}-{_file_slug(pvc.name)}.yaml"
                )
                pvc_absolute = paths.flux_dir / pvc_relative
                _write_text(pvc_absolute, yaml.safe_dump(pvc_doc, sort_keys=False))
                files.append(pvc_absolute)
                resources.append(f"./{pvc_relative.as_posix()}")
        else:
            for pvc in config.infra.sfs.csi.pvcs:
                if pvc.create_namespace and pvc.namespace not in namespace_files_written:
                    namespace_relative = Path(
                        f"apps/workloads/namespace-{_file_slug(pvc.namespace)}.yaml"
                    )
                    namespace_absolute = paths.flux_dir / namespace_relative
                    _write_text(
                        namespace_absolute,
                        yaml.safe_dump(_namespace_doc(pvc.namespace), sort_keys=False),
                    )
                    files.append(namespace_absolute)
                    resources.append(f"./{namespace_relative.as_posix()}")
                    namespace_files_written.add(pvc.namespace)

                pv_name = (
                    pvc.static_pv_name
                    if pvc.static_pv_name
                    else f"{source_name}-pv-{_file_slug(pvc.namespace)}-{_file_slug(pvc.name)}"
                )
                static_path = _join_posix_path(
                    config.infra.sfs.csi.static.shared_path, pvc.static_sub_path
                )
                pv_doc: dict[str, Any] = {
                    "apiVersion": "v1",
                    "kind": "PersistentVolume",
                    "metadata": {"name": pv_name},
                    "spec": {
                        "capacity": {"storage": pvc.size},
                        "accessModes": pvc.access_modes,
                        "persistentVolumeReclaimPolicy": config.infra.sfs.csi.static.reclaim_policy,
                        "storageClassName": csi_storage_class_name,
                        "claimRef": {"namespace": pvc.namespace, "name": pvc.name},
                        "csi": {
                            "driver": config.infra.sfs.csi.static.driver_name,
                            "volumeHandle": f"{pv_name}-handle",
                            "volumeAttributes": {"path": static_path},
                        },
                    },
                }
                pv_relative = Path(f"apps/workloads/pv-{_file_slug(pv_name)}.yaml")
                pv_absolute = paths.flux_dir / pv_relative
                _write_text(pv_absolute, yaml.safe_dump(pv_doc, sort_keys=False))
                files.append(pv_absolute)
                resources.append(f"./{pv_relative.as_posix()}")

                pvc_doc: dict[str, Any] = {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": pvc.name, "namespace": pvc.namespace},
                    "spec": {
                        "accessModes": pvc.access_modes,
                        "resources": {"requests": {"storage": pvc.size}},
                        "storageClassName": csi_storage_class_name,
                        "volumeName": pv_name,
                    },
                }
                pvc_relative = Path(
                    f"apps/workloads/pvc-{_file_slug(pvc.namespace)}-{_file_slug(pvc.name)}.yaml"
                )
                pvc_absolute = paths.flux_dir / pvc_relative
                _write_text(pvc_absolute, yaml.safe_dump(pvc_doc, sort_keys=False))
                files.append(pvc_absolute)
                resources.append(f"./{pvc_relative.as_posix()}")

    external_secrets = platform.external_secrets
    if external_secrets.enabled and external_secrets.mysterybox.enabled:
        mysterybox_sync = external_secrets.mysterybox
        bridge_namespace = external_secrets.namespace
        bridge_config = mysterybox_sync.bridge
        auth_secret_namespace = mysterybox_sync.auth_secret_namespace or bridge_namespace
        bridge_auth_secret_namespace = bridge_config.auth.secret_namespace or bridge_namespace

        if external_secrets.create_namespace and bridge_namespace not in namespace_files_written:
            namespace_relative = Path(
                f"apps/platform/namespace-{_file_slug(bridge_namespace)}.yaml"
            )
            namespace_absolute = paths.flux_dir / namespace_relative
            _write_text(
                namespace_absolute,
                yaml.safe_dump(_namespace_doc(bridge_namespace), sort_keys=False),
            )
            files.append(namespace_absolute)
            resources.append(f"./{namespace_relative.as_posix()}")
            namespace_files_written.add(bridge_namespace)

        if bridge_config.enabled:
            bridge_files: list[tuple[Path, dict[str, Any]]] = [
                (
                    Path("apps/platform/mysterybox-bridge-deployment.yaml"),
                    _mysterybox_bridge_deployment_doc(
                        namespace=bridge_namespace,
                        service_name=bridge_config.service_name,
                        service_port=bridge_config.service_port,
                        image=bridge_config.image,
                        auth_secret_name=mysterybox_sync.auth_secret_name,
                        bridge_auth_enabled=bridge_config.auth.enabled,
                        bridge_auth_header_name=bridge_config.auth.header_name,
                        bridge_auth_secret_name=bridge_config.auth.secret_name,
                        bridge_auth_secret_key=bridge_config.auth.secret_key,
                    ),
                ),
                (
                    Path("apps/platform/mysterybox-bridge-service.yaml"),
                    _mysterybox_bridge_service_doc(
                        namespace=bridge_namespace,
                        service_name=bridge_config.service_name,
                        service_port=bridge_config.service_port,
                    ),
                ),
            ]
            for relative_file, doc in bridge_files:
                absolute_file = paths.flux_dir / relative_file
                _write_text(absolute_file, yaml.safe_dump(doc, sort_keys=False))
                files.append(absolute_file)
                resources.append(f"./{relative_file.as_posix()}")

        store_relative = Path("apps/platform/mysterybox-clustersecretstore.yaml")
        store_absolute = paths.flux_dir / store_relative
        _write_text(
            store_absolute,
            yaml.safe_dump(
                _mysterybox_cluster_secret_store_doc(
                    store_name=mysterybox_sync.secret_store_name,
                    bridge_service_name=bridge_config.service_name,
                    bridge_namespace=bridge_namespace,
                    bridge_service_port=bridge_config.service_port,
                    bridge_auth_enabled=bridge_config.auth.enabled,
                    bridge_auth_header_name=bridge_config.auth.header_name,
                    bridge_auth_secret_name=bridge_config.auth.secret_name,
                    bridge_auth_secret_namespace=bridge_auth_secret_namespace,
                    bridge_auth_secret_key=bridge_config.auth.secret_key,
                ),
                sort_keys=False,
            ),
        )
        files.append(store_absolute)
        resources.append(f"./{store_relative.as_posix()}")

        for secret in config.infra.mysterybox.secrets:
            if not secret.k8s_sync.enabled:
                continue
            target_namespace = secret.k8s_sync.namespace
            target_secret_name = secret.k8s_sync.target_secret_name or secret.name
            refresh_interval = (
                secret.k8s_sync.refresh_interval or mysterybox_sync.refresh_interval_default
            )

            if target_namespace not in namespace_files_written:
                namespace_relative = Path(
                    f"apps/workloads/namespace-{_file_slug(target_namespace)}.yaml"
                )
                namespace_absolute = paths.flux_dir / namespace_relative
                _write_text(
                    namespace_absolute,
                    yaml.safe_dump(_namespace_doc(target_namespace), sort_keys=False),
                )
                files.append(namespace_absolute)
                resources.append(f"./{namespace_relative.as_posix()}")
                namespace_files_written.add(target_namespace)

            external_secret_name = f"mb-{_file_slug(secret.id)}"
            external_secret_doc = _mysterybox_external_secret_doc(
                name=external_secret_name,
                namespace=target_namespace,
                refresh_interval=refresh_interval,
                store_name=mysterybox_sync.secret_store_name,
                target_secret_name=target_secret_name,
                creation_policy=secret.k8s_sync.creation_policy,
                deletion_policy=secret.k8s_sync.deletion_policy,
                data_items=[
                    {
                        "secretKey": entry.key,
                        "remoteRef": {
                            "key": secret.name,
                            "property": entry.key,
                        },
                    }
                    for entry in secret.entries
                ],
            )
            external_secret_relative = Path(
                f"apps/workloads/externalsecret-{_file_slug(target_namespace)}-{_file_slug(secret.id)}.yaml"
            )
            external_secret_absolute = paths.flux_dir / external_secret_relative
            _write_text(
                external_secret_absolute,
                yaml.safe_dump(external_secret_doc, sort_keys=False),
            )
            files.append(external_secret_absolute)
            resources.append(f"./{external_secret_relative.as_posix()}")

        # Keep auth secret namespace visible for operators; secret values are seeded out-of-band.
        if (
            auth_secret_namespace != bridge_namespace
            and auth_secret_namespace not in namespace_files_written
        ):
            namespace_relative = Path(
                f"apps/platform/namespace-{_file_slug(auth_secret_namespace)}.yaml"
            )
            namespace_absolute = paths.flux_dir / namespace_relative
            _write_text(
                namespace_absolute,
                yaml.safe_dump(_namespace_doc(auth_secret_namespace), sort_keys=False),
            )
            files.append(namespace_absolute)
            resources.append(f"./{namespace_relative.as_posix()}")
            namespace_files_written.add(auth_secret_namespace)

        # Keep bridge auth secret namespace visible for operators as well.
        if (
            bridge_config.auth.enabled
            and bridge_auth_secret_namespace != bridge_namespace
            and bridge_auth_secret_namespace not in namespace_files_written
        ):
            namespace_relative = Path(
                f"apps/platform/namespace-{_file_slug(bridge_auth_secret_namespace)}.yaml"
            )
            namespace_absolute = paths.flux_dir / namespace_relative
            _write_text(
                namespace_absolute,
                yaml.safe_dump(_namespace_doc(bridge_auth_secret_namespace), sort_keys=False),
            )
            files.append(namespace_absolute)
            resources.append(f"./{namespace_relative.as_posix()}")
            namespace_files_written.add(bridge_auth_secret_namespace)

    if workloads.n8n.enabled:
        route_doc: dict[str, Any] = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {"name": "n8n", "namespace": workloads.n8n.namespace},
            "spec": {
                "parentRefs": [
                    {
                        "name": "envoy-gateway",
                        "namespace": platform.envoy_gateway.namespace,
                    }
                ],
                "hostnames": [workloads.n8n.route.hostname],
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "backendRefs": [{"name": "n8n", "port": 80}],
                    }
                ],
            },
        }
        if workloads.n8n.route.tls.enabled:
            route_doc["metadata"]["annotations"] = {
                "nebius-cxcli.io/tls-enabled": "true",
                "nebius-cxcli.io/tls-issuer-ref": workloads.n8n.route.tls.issuer_ref,
            }

        route_relative = Path("apps/workloads/n8n-httproute.yaml")
        route_absolute = paths.flux_dir / route_relative
        _write_text(route_absolute, yaml.safe_dump(route_doc, sort_keys=False))
        files.append(route_absolute)
        resources.append(f"./{route_relative.as_posix()}")

    repos_path = paths.flux_dir / "sources/helm-repositories.yaml"
    _ensure_parent(repos_path)
    if repositories:
        repo_yaml = (
            "---\n".join(yaml.safe_dump(doc, sort_keys=False).strip() for doc in repositories)
            + "\n"
        )
    else:
        repo_yaml = "# No Helm repositories are enabled for this instance\n"
    repos_path.write_text(repo_yaml, encoding="utf-8")
    files.append(repos_path)

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    }
    kustomization_path = paths.flux_dir / "kustomization.yaml"
    _write_text(kustomization_path, yaml.safe_dump(kustomization_doc, sort_keys=False))
    files.append(kustomization_path)

    return files


def render_instance(config: ConfigV1, paths: InstancePaths) -> RenderResult:
    """Render Terraform and Flux artifacts for one validated config."""
    written: list[Path] = []

    paths.infra_dir.mkdir(parents=True, exist_ok=True)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    terraform_tf_path = paths.infra_dir / "terraform.tf"
    _write_text(terraform_tf_path, _terraform_backend_block(config, paths))
    written.append(terraform_tf_path)

    tfvars_payload = _terraform_tfvars(config)

    main_tf_path = paths.infra_dir / "main.tf"
    _write_text(main_tf_path, _terraform_main_block(tfvars_payload))
    written.append(main_tf_path)

    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    _write_text(tfvars_path, json.dumps(tfvars_payload, indent=2, sort_keys=True) + "\n")
    written.append(tfvars_path)

    written.extend(_render_flux(config, paths))

    return RenderResult(files_written=sorted(written))
