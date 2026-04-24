"""Deploy report generation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .component_wiring import resolved_component_row
from .deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    build_deploy_validation_report,
    validation_section_lines,
)
from .observability import observability_endpoint_summary, observability_status_summary
from .paths import ProjectPaths
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class InventoryArtifacts:
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


def _infra_component_rows(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    infra = _mapping(_lookup(payload, "infra"))
    rows = _lookup(infra, "components")
    if not isinstance(rows, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        instance_id = component_instance_id(item)
        _entry, resolved = resolved_component_row(
            payload,
            component_id=component_id,
            instance_id=instance_id or None,
        )
        result.setdefault(component_id, []).append(
            _mapping(resolved if isinstance(resolved, Mapping) else item)
        )
    return result


def _app_chart_rows(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    apps = _mapping(_lookup(payload, "apps"))
    rows = _lookup(apps, "charts")
    if not isinstance(rows, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        chart_id = component_type_id(item)
        if not chart_id:
            continue
        instance_id = component_instance_id(item)
        _entry, resolved = resolved_component_row(
            payload,
            component_id=chart_id,
            instance_id=instance_id or None,
        )
        result.setdefault(chart_id, []).append(
            _mapping(resolved if isinstance(resolved, Mapping) else item)
        )
    return result


def _component_inputs(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return _mapping(_lookup(row, "inputs"))


def _chart_values(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return _mapping(_lookup(row, "values"))


def _first_row(rows: dict[str, list[dict[str, Any]]], key: str) -> dict[str, Any] | None:
    grouped = rows.get(_normalize_component_id(key))
    if not grouped:
        return None
    return grouped[0]


def _chart_enabled(rows: dict[str, list[dict[str, Any]]], *chart_ids: str) -> bool:
    for chart_id in chart_ids:
        grouped = rows.get(_normalize_component_id(chart_id))
        if not grouped:
            continue
        return any(bool(_lookup(row, "enabled")) for row in grouped)
    return False


def _build_payload(config: Any, paths: ProjectPaths) -> dict[str, dict]:
    payload_data = to_plain_data(config)
    if not isinstance(payload_data, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    client_info = _mapping(_lookup(payload_data, "client_info"))
    nebius = _mapping(_lookup(client_info, "nebius"))
    project_id = str(_coalesce(_lookup(nebius, "project_id"), ""))
    tenant_id = str(_coalesce(_lookup(nebius, "tenant_id"), ""))
    region_id = str(_coalesce(_lookup(nebius, "region_id"), ""))

    infra_rows = _infra_component_rows(payload_data)
    app_rows = _app_chart_rows(payload_data)

    # Look up infra components by their declared status kind instead of
    # hard-coded component ids.
    from .components import component_lookup as _component_lookup

    entry_by_id = _component_lookup("infra")

    def _row_by_status_kind(kind: str) -> dict[str, Any] | None:
        for cid, entry in entry_by_id.items():
            status = getattr(entry, "status", None)
            if status is not None and getattr(status, "kind", "") == kind:
                return _first_row(infra_rows, cid)
        return None

    mk8s_row = _row_by_status_kind("nebius.mk8s.cluster")
    mk8s_inputs = _component_inputs(mk8s_row)
    cpu_nodes = _mapping(_lookup(mk8s_inputs, "cpu_nodes"))
    gpu_nodes = _mapping(_lookup(mk8s_inputs, "gpu_nodes"))
    api_endpoint = _mapping(_lookup(mk8s_inputs, "api_endpoint"))

    pg_row = _row_by_status_kind("nebius.msp.postgresql.cluster")
    pg_inputs = _component_inputs(pg_row)

    sfs_row = _row_by_status_kind("nebius.compute.filesystem")
    sfs_inputs = _component_inputs(sfs_row)

    n8n_values = _chart_values(_first_row(app_rows, "n8n"))
    n8n_chart_values = _mapping(_lookup(n8n_values, "values"))
    n8n_route = _mapping(_lookup(n8n_values, "route"))
    if not n8n_route:
        n8n_route = _mapping(_lookup(n8n_chart_values, "route"))
    observability = observability_status_summary(payload_data)
    observability_endpoints = observability_endpoint_summary(
        payload_data,
        project_id=project_id,
        region_id=region_id,
    )
    cluster_name = str(
        _coalesce(
            _lookup(mk8s_inputs, "cluster_name"),
            project_id,
            f"{tenant_id}/{project_id}",
        )
    )

    return {
        "infra": {
            "project_scope": f"{tenant_id}/{project_id}",
            "project_id": project_id,
            "region": region_id,
            "mk8s_enabled": bool(_lookup(mk8s_row or {}, "enabled")),
            "wireguard_enabled": any(
                bool(_lookup(row, "enabled"))
                for cid, rows in infra_rows.items()
                for row in rows
                if "wireguard" in cid
            ),
        },
        "mk8s": {
            "cluster_name": cluster_name,
            "cpu_nodes": {
                "count": _coalesce(
                    _lookup(cpu_nodes, "count"), _lookup(mk8s_inputs, "cpu_nodes_count")
                ),
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
                    _coalesce(
                        _lookup(gpu_nodes, "enabled"),
                        _lookup(mk8s_inputs, "gpu_nodes_enabled"),
                        False,
                    )
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
            "envoy_gateway": _chart_enabled(
                app_rows, "gateway-helm", "envoy-gateway", "envoy_gateway"
            ),
            "cert_manager": _chart_enabled(app_rows, "cert-manager", "cert_manager"),
            "external_dns": _chart_enabled(app_rows, "external-dns", "external_dns"),
            "observability": observability,
            "observability_endpoints": observability_endpoints,
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


def write_inventory(
    config: Any,
    paths: ProjectPaths,
    *,
    validations: Sequence[Mapping[str, Any]] = (),
) -> InventoryArtifacts:
    """Write the human-readable deploy report artifact to disk."""
    payload = _build_payload(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in (
        paths.inventory_dir / "infra.json",
        paths.inventory_dir / "mk8s.json",
        paths.inventory_dir / "postgresql.json",
        paths.inventory_dir / "sfs.json",
        paths.inventory_dir / "apps.json",
    ):
        stale_path.unlink(missing_ok=True)
    for stale_markdown in (
        paths.inventory_dir / "inventory.md",
        paths.inventory_dir / "deploy-validation-report.md",
    ):
        stale_markdown.unlink(missing_ok=True)

    payload_data = to_plain_data(config)
    client_info = _mapping(payload_data if isinstance(payload_data, dict) else {})
    if isinstance(payload_data, dict):
        client_info = _mapping(_lookup(payload_data, "client_info"))
    nebius = _mapping(_lookup(client_info, "nebius"))
    app_summary = _mapping(_lookup(payload, "apps"))
    n8n_summary = _mapping(_lookup(app_summary, "n8n"))
    observability_summary = _mapping(_lookup(app_summary, "observability"))
    observability_endpoints = _mapping(_lookup(app_summary, "observability_endpoints"))
    observability_read_endpoints = _mapping(_lookup(observability_endpoints, "read"))
    observability_write_endpoints = _mapping(_lookup(observability_endpoints, "write"))
    observability_auth = _mapping(_lookup(observability_endpoints, "auth"))

    markdown_path = paths.inventory_dir / DEPLOY_REPORT_FILENAME
    validation_report = build_deploy_validation_report(
        validations,
        inventory_dir=paths.inventory_dir,
        markdown_path=markdown_path,
    )
    lines = [
        f"# Deploy Report: {payload['infra']['project_id']}",
        "",
        "## Infra",
        "",
        f"- Client: `{_coalesce(_lookup(client_info, 'client_name'), '')}`",
        f"- Tenant: `{_coalesce(_lookup(nebius, 'tenant_id'), '')}`",
        f"- Project: `{_coalesce(_lookup(nebius, 'project_id'), '')}`",
        f"- Region: `{payload['infra']['region']}`",
        f"- MK8s: `{payload['infra']['mk8s_enabled']}`",
        f"- Managed PostgreSQL: `{payload['postgresql']['enabled']}`",
        f"- SFS: `{payload['sfs']['enabled']}`",
        f"- WireGuard Jump Host: `{payload['infra']['wireguard_enabled']}`",
        "",
        "## Apps",
        "",
        f"- Envoy Gateway: `{payload['apps']['envoy_gateway']}`",
        f"- cert-manager: `{payload['apps']['cert_manager']}`",
        f"- ExternalDNS: `{payload['apps']['external_dns']}`",
        f"- Observability: `{_coalesce(_lookup(observability_summary, 'enabled'), False)}`",
        f"- K8s o11y agent: `{_coalesce(_lookup(observability_summary, 'kubernetes_agent'), False)}`",
        f"- VM monitoring agent: `{_coalesce(_lookup(observability_summary, 'vm_monitoring_agent'), False)}`",
        f"- VM journald logs (systemd services): `{_coalesce(_lookup(observability_summary, 'vm_journald_logs'), False)}`",
        f"- VM standalone collector: `{_coalesce(_lookup(observability_summary, 'vm_standalone_collector'), False)}`",
        f"- VM standalone collector metrics: `{_coalesce(_lookup(observability_summary, 'vm_standalone_metrics'), False)}`",
        f"- VM standalone collector logs: `{_coalesce(_lookup(observability_summary, 'vm_standalone_logs'), False)}`",
        f"- GPU DCGM metrics source: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_metric_source'), 'disabled')}`",
        f"- GPU DCGM node policy: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_node_policy'), 'not_managed')}`",
        f"- GPU DCGM live readiness: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_live_readiness'), 'not_configured')}`",
        f"- n8n: `{_coalesce(_lookup(n8n_summary, 'enabled'), False)}` "
        f"({_coalesce(_lookup(n8n_summary, 'hostname'), 'n/a')})",
        "",
    ]
    if bool(_lookup(observability_endpoints, "configured")) and (
        bool(_lookup(observability_summary, "enabled"))
        or bool(_lookup(observability_summary, "vm_monitoring_agent"))
    ):
        endpoint_lines: list[str] = []

        def _append_endpoint(
            label: str,
            endpoints: dict[str, Any],
            key: str,
        ) -> None:
            value = _lookup(endpoints, key)
            if value:
                endpoint_lines.append(f"- {label}: `{value}`")

        _append_endpoint(
            "Metrics read (Nebius service metrics)",
            observability_read_endpoints,
            "metrics_service_provider_read",
        )
        _append_endpoint(
            "Metrics read (user-ingested metrics)",
            observability_read_endpoints,
            "metrics_user_read",
        )
        _append_endpoint(
            "Metrics read (federate template)",
            observability_read_endpoints,
            "metrics_federate_read",
        )
        _append_endpoint("Logs read (Loki)", observability_read_endpoints, "logs_loki_read")
        _append_endpoint("Traces read (Tempo)", observability_read_endpoints, "traces_tempo_read")
        _append_endpoint(
            "Metrics write (OTLP HTTP/protobuf)",
            observability_write_endpoints,
            "metrics_otlp_write",
        )
        _append_endpoint(
            "Metrics write (Prometheus Remote Write)",
            observability_write_endpoints,
            "metrics_prometheus_remote_write",
        )
        _append_endpoint(
            "Metrics write (VM monitoring agent)",
            observability_write_endpoints,
            "metrics_platform_managed_write",
        )
        _append_endpoint(
            "Logs write (direct/self-managed)",
            observability_write_endpoints,
            "logs_otlp_write",
        )
        _append_endpoint(
            "Logs write (Nebius agent gRPC)",
            observability_write_endpoints,
            "logs_agent_grpc_write",
        )
        _append_endpoint(
            "Logs write (VM monitoring agent)",
            observability_write_endpoints,
            "logs_platform_managed_write",
        )
        _append_endpoint(
            "Traces write (OTLP gRPC)",
            observability_write_endpoints,
            "traces_otlp_grpc_write",
        )
        if endpoint_lines:
            endpoint_lines.append(f"- Read auth: `{_lookup(observability_auth, 'read')}`")
            endpoint_lines.append(f"- Write auth: `{_lookup(observability_auth, 'write')}`")
            lines.extend(["## Observability Endpoints", "", *endpoint_lines, ""])
    if validations:
        lines.extend(validation_section_lines(validation_report))
    else:
        lines.extend(
            [
                "## Validations",
                "",
                "- No deploy-time validations configured.",
            ]
        )
    while lines and not str(lines[-1]).strip():
        lines.pop()
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return InventoryArtifacts(markdown=markdown_path)
