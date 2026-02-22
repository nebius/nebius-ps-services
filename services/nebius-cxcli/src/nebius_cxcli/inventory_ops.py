"""Inventory generation and Object Storage upload support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import boto3

from .paths import InstancePaths
from .schema import ConfigV1


@dataclass(frozen=True)
class InventoryArtifacts:
    infra_json: Path
    mk8s_json: Path
    postgresql_json: Path
    sfs_json: Path
    apps_json: Path
    markdown: Path


def _build_payload(config: ConfigV1, paths: InstancePaths) -> dict[str, dict]:
    now = datetime.now(UTC).isoformat()
    return {
        "infra": {
            "generated_at": now,
            "instance": paths.instance_slug,
            "env": config.client_info.env.value,
            "cluster": config.client_info.cluster_name,
            "region": config.client_info.nebius.region_id,
            "mk8s_enabled": config.infra.mk8s.enabled,
            "wireguard_enabled": config.infra.wireguard_jumphost.enabled,
        },
        "mk8s": {
            "cluster_name": config.client_info.cluster_name,
            "cpu_nodes": {
                "count": config.infra.mk8s.cpu_nodes.count,
                "platform": config.infra.mk8s.cpu_nodes.platform,
                "preset": config.infra.mk8s.cpu_nodes.preset,
            },
            "gpu_nodes": {
                "enabled": config.infra.mk8s.gpu_nodes.enabled,
                "groups": config.infra.mk8s.gpu_nodes.node_groups,
                "nodes_per_group": config.infra.mk8s.gpu_nodes.nodes_per_group,
                "platform": config.infra.mk8s.gpu_nodes.platform,
                "preset": config.infra.mk8s.gpu_nodes.preset,
            },
            "api_public": config.infra.mk8s.api_endpoint.public,
            "infiniband_fabric": config.infra.mk8s.infiniband_fabric,
        },
        "postgresql": {
            "enabled": config.infra.managed_postgresql.enabled,
            "name": config.infra.managed_postgresql.name,
            "tier": config.infra.managed_postgresql.tier,
            "storage_gib": config.infra.managed_postgresql.storage_gib,
        },
        "sfs": {
            "enabled": config.infra.sfs.enabled,
            "name": config.infra.sfs.name,
            "size_gib": config.infra.sfs.size_gib,
            "block_size_kib": config.infra.sfs.block_size_kib,
        },
        "apps": {
            "envoy_gateway": config.apps.platform.envoy_gateway.enabled,
            "cert_manager": config.apps.platform.cert_manager.enabled,
            "external_dns": config.apps.platform.external_dns.enabled,
            "observability": config.apps.platform.observability.enabled,
            "n8n": {
                "enabled": config.apps.workloads.n8n.enabled,
                "hostname": config.apps.workloads.n8n.route.hostname,
            },
        },
    }


def write_inventory(config: ConfigV1, paths: InstancePaths) -> InventoryArtifacts:
    """Write non-sensitive inventory artifacts to disk."""
    payload = _build_payload(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    infra_path = paths.inventory_dir / "infra.json"
    mk8s_path = paths.inventory_dir / "mk8s.json"
    pg_path = paths.inventory_dir / "postgresql.json"
    sfs_path = paths.inventory_dir / "sfs.json"
    apps_path = paths.inventory_dir / "apps.json"

    infra_path.write_text(
        json.dumps(payload["infra"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    mk8s_path.write_text(
        json.dumps(payload["mk8s"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pg_path.write_text(
        json.dumps(payload["postgresql"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sfs_path.write_text(
        json.dumps(payload["sfs"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    apps_path.write_text(
        json.dumps(payload["apps"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    markdown_path = paths.inventory_dir / "inventory.md"
    lines = [
        f"# Inventory: {config.client_info.cluster_name}",
        "",
        f"- Client: `{config.client_info.client_name}`",
        f"- Tenant: `{config.client_info.nebius.tenant_id}`",
        f"- Project: `{config.client_info.nebius.project_id}`",
        f"- Environment: `{config.client_info.env.value}`",
        f"- Region: `{config.client_info.nebius.region_id}`",
        f"- Generated: `{payload['infra']['generated_at']}`",
        "",
        "## Components",
        f"- MK8s: `{config.infra.mk8s.enabled}`",
        f"- Managed PostgreSQL: `{config.infra.managed_postgresql.enabled}`",
        f"- SFS: `{config.infra.sfs.enabled}`",
        f"- WireGuard Jump Host: `{config.infra.wireguard_jumphost.enabled}`",
        "",
        "## Apps",
        f"- Envoy Gateway: `{config.apps.platform.envoy_gateway.enabled}`",
        f"- cert-manager: `{config.apps.platform.cert_manager.enabled}`",
        f"- ExternalDNS: `{config.apps.platform.external_dns.enabled}`",
        f"- Observability: `{config.apps.platform.observability.enabled}`",
        f"- n8n: `{config.apps.workloads.n8n.enabled}` ({config.apps.workloads.n8n.route.hostname})",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return InventoryArtifacts(
        infra_json=infra_path,
        mk8s_json=mk8s_path,
        postgresql_json=pg_path,
        sfs_json=sfs_path,
        apps_json=apps_path,
        markdown=markdown_path,
    )


def upload_inventory(config: ConfigV1, paths: InstancePaths) -> list[str]:
    """Upload inventory artifacts to Nebius Object Storage."""
    artifacts = write_inventory(config, paths)

    endpoint_url = f"https://storage.{config.client_info.nebius.region_id}.nebius.cloud"
    bucket_name = config.infra.object_storage.inventory_bucket.name
    key_prefix = config.infra.object_storage.inventory_bucket.prefix.strip("/")
    object_prefix = "/".join(
        [
            key_prefix,
            paths.instance_slug,
            config.client_info.env.value,
            config.client_info.cluster_name,
        ]
    )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=config.client_info.nebius.region_id,
    )

    uploads = {
        "infra.json": artifacts.infra_json,
        "mk8s.json": artifacts.mk8s_json,
        "postgresql.json": artifacts.postgresql_json,
        "sfs.json": artifacts.sfs_json,
        "apps.json": artifacts.apps_json,
        "inventory.md": artifacts.markdown,
    }

    uploaded_keys: list[str] = []
    for filename, file_path in uploads.items():
        object_key = "/".join([object_prefix, filename])
        client.upload_file(str(file_path), bucket_name, object_key)
        uploaded_keys.append(object_key)

    return uploaded_keys
