"""Inventory generation and Object Storage upload support."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from .paths import InstancePaths
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class InventoryArtifacts:
    infra_json: Path
    mk8s_json: Path
    postgresql_json: Path
    sfs_json: Path
    apps_json: Path
    markdown: Path


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _lookup(node: Mapping[str, Any], key: str) -> Any:
    candidates = (key, key.replace("-", "_"), key.replace("_", "-"))
    for candidate in candidates:
        if candidate in node:
            return node[candidate]
    return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_component_id(raw_value: Any) -> str:
    return str(raw_value or "").strip().lower().replace("_", "-")


def _infra_component_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    infra = _mapping(_lookup(payload, "infra"))
    rows = _lookup(infra, "components")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        component_id = _normalize_component_id(_lookup(item, "id"))
        if not component_id:
            continue
        result[component_id] = _mapping(item)
    return result


def _app_chart_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    apps = _mapping(_lookup(payload, "apps"))
    rows = _lookup(apps, "charts")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        chart_id = _normalize_component_id(_lookup(item, "id"))
        if not chart_id:
            continue
        result[chart_id] = _mapping(item)
    return result


def _component_inputs(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return _mapping(_lookup(row, "inputs"))


def _chart_values(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return _mapping(_lookup(row, "values"))


def _chart_enabled(rows: dict[str, dict[str, Any]], *chart_ids: str) -> bool:
    for chart_id in chart_ids:
        row = rows.get(_normalize_component_id(chart_id))
        if row is None:
            continue
        return bool(_lookup(row, "enabled"))
    return False


def _build_payload(config: Any, paths: InstancePaths) -> dict[str, dict]:
    payload_data = to_plain_data(config)
    if not isinstance(payload_data, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    client_info = _mapping(_lookup(payload_data, "client_info"))
    nebius = _mapping(_lookup(client_info, "nebius"))
    project_id = str(_coalesce(_lookup(nebius, "project_id"), paths.path_project_id))
    region_id = str(_coalesce(_lookup(nebius, "region_id"), ""))

    infra_rows = _infra_component_rows(payload_data)
    app_rows = _app_chart_rows(payload_data)

    mk8s_row = infra_rows.get("mk8s")
    mk8s_inputs = _component_inputs(mk8s_row)
    cpu_nodes = _mapping(_lookup(mk8s_inputs, "cpu_nodes"))
    gpu_nodes = _mapping(_lookup(mk8s_inputs, "gpu_nodes"))
    api_endpoint = _mapping(_lookup(mk8s_inputs, "api_endpoint"))

    pg_row = infra_rows.get("managed-postgresql")
    pg_inputs = _component_inputs(pg_row)

    sfs_row = infra_rows.get("sfs")
    sfs_inputs = _component_inputs(sfs_row)

    n8n_values = _chart_values(app_rows.get("n8n"))
    n8n_chart_values = _mapping(_lookup(n8n_values, "values"))
    n8n_route = _mapping(_lookup(n8n_values, "route"))
    if not n8n_route:
        n8n_route = _mapping(_lookup(n8n_chart_values, "route"))
    cluster_name = str(
        _coalesce(
            _lookup(mk8s_inputs, "cluster_name"),
            project_id,
            paths.instance_slug,
        )
    )

    now = datetime.now(UTC).isoformat()
    return {
        "infra": {
            "generated_at": now,
            "instance": paths.instance_slug,
            "project_id": project_id,
            "region": region_id,
            "mk8s_enabled": bool(_lookup(mk8s_row or {}, "enabled")),
            "wireguard_enabled": bool(_lookup(infra_rows.get("wireguard-jumphost") or {}, "enabled")),
        },
        "mk8s": {
            "cluster_name": cluster_name,
            "cpu_nodes": {
                "count": _coalesce(_lookup(cpu_nodes, "count"), _lookup(mk8s_inputs, "cpu_nodes_count")),
                "platform": _coalesce(
                    _lookup(cpu_nodes, "platform"),
                    _lookup(mk8s_inputs, "cpu_nodes_platform"),
                ),
                "preset": _coalesce(
                    _lookup(cpu_nodes, "preset"),
                    _lookup(mk8s_inputs, "cpu_nodes_preset"),
                ),
            },
            "gpu_nodes": {
                "enabled": bool(
                    _coalesce(_lookup(gpu_nodes, "enabled"), _lookup(mk8s_inputs, "gpu_nodes_enabled"), False)
                ),
                "groups": _coalesce(
                    _lookup(gpu_nodes, "node_groups"),
                    _lookup(mk8s_inputs, "gpu_nodes_node_groups"),
                ),
                "nodes_per_group": _coalesce(
                    _lookup(gpu_nodes, "nodes_per_group"),
                    _lookup(mk8s_inputs, "gpu_nodes_nodes_per_group"),
                ),
                "platform": _coalesce(
                    _lookup(gpu_nodes, "platform"),
                    _lookup(mk8s_inputs, "gpu_nodes_platform"),
                ),
                "preset": _coalesce(
                    _lookup(gpu_nodes, "preset"),
                    _lookup(mk8s_inputs, "gpu_nodes_preset"),
                ),
            },
            "api_public": _coalesce(
                _lookup(api_endpoint, "public"),
                _lookup(mk8s_inputs, "api_endpoint_public"),
            ),
            "infiniband_fabric": _lookup(mk8s_inputs, "infiniband_fabric"),
        },
        "postgresql": {
            "enabled": bool(_lookup(pg_row or {}, "enabled")),
            "name": _lookup(pg_inputs, "name"),
            "tier": _lookup(pg_inputs, "tier"),
            "storage_gib": _lookup(pg_inputs, "storage_gib"),
        },
        "sfs": {
            "enabled": bool(_lookup(sfs_row or {}, "enabled")),
            "name": _lookup(sfs_inputs, "name"),
            "size_gib": _lookup(sfs_inputs, "size_gib"),
            "block_size_kib": _lookup(sfs_inputs, "block_size_kib"),
        },
        "apps": {
            "envoy_gateway": _chart_enabled(app_rows, "gateway-helm", "envoy-gateway", "envoy_gateway"),
            "cert_manager": _chart_enabled(app_rows, "cert-manager", "cert_manager"),
            "external_dns": _chart_enabled(app_rows, "external-dns", "external_dns"),
            "observability": _chart_enabled(app_rows, "nebius-observability", "observability"),
            "n8n": {
                "enabled": _chart_enabled(app_rows, "n8n"),
                "hostname": _coalesce(
                    _lookup(n8n_route, "hostname"),
                    _lookup(n8n_values, "hostname"),
                    _lookup(n8n_chart_values, "hostname"),
                ),
            },
        },
    }


def write_inventory(config: Any, paths: InstancePaths) -> InventoryArtifacts:
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

    payload_data = to_plain_data(config)
    client_info = _mapping(payload_data if isinstance(payload_data, dict) else {})
    if isinstance(payload_data, dict):
        client_info = _mapping(_lookup(payload_data, "client_info"))
    nebius = _mapping(_lookup(client_info, "nebius"))
    app_summary = _mapping(_lookup(payload, "apps"))
    n8n_summary = _mapping(_lookup(app_summary, "n8n"))

    markdown_path = paths.inventory_dir / "inventory.md"
    lines = [
        f"# Inventory: {payload['infra']['project_id']}",
        "",
        f"- Client: `{_coalesce(_lookup(client_info, 'client_name'), paths.path_client_name)}`",
        f"- Tenant: `{_coalesce(_lookup(nebius, 'tenant_id'), paths.path_tenant_id)}`",
        f"- Project: `{_coalesce(_lookup(nebius, 'project_id'), '')}`",
        f"- Region: `{payload['infra']['region']}`",
        f"- Generated: `{payload['infra']['generated_at']}`",
        "",
        "## Components",
        f"- MK8s: `{payload['infra']['mk8s_enabled']}`",
        f"- Managed PostgreSQL: `{payload['postgresql']['enabled']}`",
        f"- SFS: `{payload['sfs']['enabled']}`",
        f"- WireGuard Jump Host: `{payload['infra']['wireguard_enabled']}`",
        "",
        "## Apps",
        f"- Envoy Gateway: `{payload['apps']['envoy_gateway']}`",
        f"- cert-manager: `{payload['apps']['cert_manager']}`",
        f"- ExternalDNS: `{payload['apps']['external_dns']}`",
        f"- Observability: `{payload['apps']['observability']}`",
        f"- n8n: `{_coalesce(_lookup(n8n_summary, 'enabled'), False)}` "
        f"({_coalesce(_lookup(n8n_summary, 'hostname'), 'n/a')})",
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


def upload_inventory(config: Any, paths: InstancePaths) -> list[str]:
    """Upload inventory artifacts to Nebius Object Storage."""
    artifacts = write_inventory(config, paths)

    payload_data = to_plain_data(config)
    if not isinstance(payload_data, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    client_info = _mapping(_lookup(payload_data, "client_info"))
    nebius = _mapping(_lookup(client_info, "nebius"))
    region_id = str(_coalesce(_lookup(nebius, "region_id"), "")).strip()
    project_id = str(_coalesce(_lookup(nebius, "project_id"), paths.path_project_id)).strip()
    if not region_id:
        raise RuntimeError("client_info.nebius.region_id is required for inventory upload")

    infra_rows = _infra_component_rows(payload_data)
    object_storage = infra_rows.get("object-storage")
    if object_storage is None or not bool(_lookup(object_storage, "enabled")):
        raise RuntimeError(
            "infra component 'object-storage' must be enabled for inventory upload"
        )

    object_storage_inputs = _component_inputs(object_storage)
    inventory_bucket = _mapping(_lookup(object_storage_inputs, "inventory_bucket"))
    bucket_name = _coalesce(
        _lookup(inventory_bucket, "name"),
        _lookup(object_storage_inputs, "inventory_bucket_name"),
        _lookup(object_storage_inputs, "bucket_name"),
    )
    if not bucket_name:
        raise RuntimeError(
            "Object Storage bucket name is missing for inventory upload. "
            "Set infra.components[id=object-storage].inputs.inventory_bucket.name "
            "(or inventory_bucket_name)."
        )
    key_prefix = str(
        _coalesce(
            _lookup(inventory_bucket, "prefix"),
            _lookup(object_storage_inputs, "inventory_bucket_prefix"),
            _lookup(object_storage_inputs, "bucket_prefix"),
            "",
        )
    ).strip("/")
    endpoint_url = f"https://storage.{region_id}.nebius.cloud"
    object_prefix = "/".join(
        part for part in [key_prefix, paths.instance_slug, project_id] if part
    )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_id,
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
        object_key = "/".join([object_prefix, filename]) if object_prefix else filename
        client.upload_file(str(file_path), str(bucket_name), object_key)
        uploaded_keys.append(object_key)

    return uploaded_keys
