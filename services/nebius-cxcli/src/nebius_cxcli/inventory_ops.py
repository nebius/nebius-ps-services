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
from .grafana_runtime import read_grafana_status
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


def _service_provider_metric_buckets(payload: Mapping[str, Any]) -> tuple[str, ...]:
    buckets = ["compute"]
    clusters = _lookup(payload, "mk8s_clusters")
    if isinstance(clusters, list):
        has_gpu_nodes = any(
            bool(_lookup(_mapping(_lookup(_mapping(item), "gpu_nodes")), "enabled"))
            for item in clusters
        )
    else:
        mk8s = _mapping(_lookup(payload, "mk8s"))
        mk8s_gpu_nodes = _mapping(_lookup(mk8s, "gpu_nodes"))
        has_gpu_nodes = bool(_lookup(mk8s_gpu_nodes, "enabled"))
    if has_gpu_nodes:
        buckets.append("gpu")
    buckets.extend(("nbs", "sp_storage"))

    postgresql = _mapping(_lookup(payload, "postgresql"))
    if bool(_lookup(postgresql, "enabled")):
        buckets.append("msp")
    return tuple(buckets)


def _format_backtick_list(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values)


def _markdown_link(label: str, url: Any) -> str:
    value = str(url or "").strip()
    return f"[{label}]({value})" if value else "`pending`"


def _shape_label(*parts: Any) -> str:
    values = [str(item).strip() for item in parts if str(item or "").strip()]
    return "/".join(values) if values else "n/a"


def _mk8s_cluster_markdown_line(cluster: Mapping[str, Any]) -> str:
    instance_id = str(_coalesce(_lookup(cluster, "instance_id"), "mk8s"))
    cluster_name = str(_coalesce(_lookup(cluster, "cluster_name"), instance_id))
    cpu_nodes = _mapping(_lookup(cluster, "cpu_nodes"))
    gpu_nodes = _mapping(_lookup(cluster, "gpu_nodes"))
    cpu_count = _coalesce(_lookup(cpu_nodes, "count"), "n/a")
    cpu_shape = _shape_label(_lookup(cpu_nodes, "platform"), _lookup(cpu_nodes, "preset"))
    if bool(_lookup(gpu_nodes, "enabled")):
        groups = _coalesce(_lookup(gpu_nodes, "groups"), 1)
        nodes_per_group = _coalesce(_lookup(gpu_nodes, "nodes_per_group"), "n/a")
        gpu_shape = _shape_label(_lookup(gpu_nodes, "platform"), _lookup(gpu_nodes, "preset"))
        gpu_text = f"`{groups}x{nodes_per_group}` node(s) at `{gpu_shape}`"
    else:
        gpu_text = "`disabled`"
    fabric = _coalesce(_lookup(cluster, "infiniband_fabric"), "none")
    public_endpoint = _coalesce(_lookup(cluster, "api_public"), "unknown")
    return (
        f"- MK8s cluster `{instance_id}` (`{cluster_name}`): CPU `{cpu_count}` node(s) "
        f"at `{cpu_shape}`; GPU {gpu_text}; fabric `{fabric}`; public endpoint "
        f"`{public_endpoint}`"
    )


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

    def _rows_by_status_kind(kind: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for cid, entry in entry_by_id.items():
            status = getattr(entry, "status", None)
            if status is not None and getattr(status, "kind", "") == kind:
                rows.extend(infra_rows.get(cid, []))
        return rows

    def _row_by_status_kind(kind: str) -> dict[str, Any] | None:
        rows = _rows_by_status_kind(kind)
        return rows[0] if rows else None

    mk8s_rows = _rows_by_status_kind("nebius.mk8s.cluster")

    def _mk8s_summary(row: dict[str, Any] | None) -> dict[str, Any]:
        mk8s_inputs = _component_inputs(row)
        cpu_nodes = _mapping(_lookup(mk8s_inputs, "cpu_nodes"))
        gpu_nodes = _mapping(_lookup(mk8s_inputs, "gpu_nodes"))
        api_endpoint = _mapping(_lookup(mk8s_inputs, "api_endpoint"))
        cluster_name = str(
            _coalesce(
                _lookup(mk8s_inputs, "cluster_name"),
                project_id,
                f"{tenant_id}/{project_id}",
            )
        )
        return {
            "instance_id": component_instance_id(row or {}) or component_type_id(row or {}),
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
                        _lookup(mk8s_inputs, "gpu_enabled"),
                        _lookup(mk8s_inputs, "gpu_nodes_enabled"),
                        False,
                    )
                ),
                "groups": _coalesce(
                    _lookup(gpu_nodes, "node_groups"),
                    _lookup(mk8s_inputs, "gpu_node_groups"),
                    _lookup(mk8s_inputs, "gpu_nodes_node_groups"),
                ),
                "nodes_per_group": _coalesce(
                    _lookup(gpu_nodes, "nodes_per_group"),
                    _lookup(mk8s_inputs, "gpu_nodes_count_per_group"),
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
                _lookup(mk8s_inputs, "mk8s_cluster_public_endpoint"),
                _lookup(mk8s_inputs, "api_endpoint_public"),
            ),
            "infiniband_fabric": _lookup(mk8s_inputs, "infiniband_fabric"),
        }

    mk8s_row = mk8s_rows[0] if mk8s_rows else None
    mk8s_summary = _mk8s_summary(mk8s_row)
    mk8s_cluster_summaries = [_mk8s_summary(row) for row in mk8s_rows]

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
        "mk8s": mk8s_summary,
        "mk8s_clusters": mk8s_cluster_summaries,
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
    ]
    for cluster in payload.get("mk8s_clusters", []):
        if isinstance(cluster, Mapping):
            lines.append(_mk8s_cluster_markdown_line(cluster))
    lines.extend(
        [
            "",
            "## Apps",
            "",
            f"- Envoy Gateway: `{payload['apps']['envoy_gateway']}`",
            f"- cert-manager: `{payload['apps']['cert_manager']}`",
            f"- ExternalDNS: `{payload['apps']['external_dns']}`",
            f"- Observability: `{_coalesce(_lookup(observability_summary, 'enabled'), False)}`",
            f"- K8s o11y agent: `{_coalesce(_lookup(observability_summary, 'kubernetes_agent'), False)}`",
            f"- Grafana: `{_coalesce(_lookup(observability_summary, 'grafana'), False)}`",
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
    )
    if bool(_lookup(observability_endpoints, "configured")) and (
        bool(_lookup(observability_summary, "enabled"))
        or bool(_lookup(observability_summary, "vm_monitoring_agent"))
    ):
        read_lines: list[str] = []
        write_lines: list[str] = []
        service_provider_metric_buckets = _service_provider_metric_buckets(payload)

        def _append_endpoint(
            label: str,
            endpoints: dict[str, Any],
            key: str,
            target: list[str],
        ) -> None:
            value = _lookup(endpoints, key)
            if value:
                target.append(f"- {label}: `{value}`")

        def _append_federate_endpoints() -> None:
            value = str(
                _lookup(observability_read_endpoints, "metrics_federate_read") or ""
            ).strip()
            if not value:
                return
            if "<service-provider>" not in value:
                read_lines.append(f"- Metrics read (federate): `{value}`")
                return
            for bucket in service_provider_metric_buckets:
                read_lines.append(
                    f"- Metrics read (federate, `{bucket}` bucket): "
                    f"`{value.replace('<service-provider>', bucket)}`"
                )

        probe_lines: list[str] = []

        def _append_probe_url(label: str, key: str, suffix: str) -> None:
            value = str(_lookup(observability_read_endpoints, key) or "").strip().rstrip("/")
            if value:
                probe_lines.append(f"- {label}: `{value}{suffix}`")

        _append_endpoint(
            "Metrics read (Prometheus, Nebius service metrics)",
            observability_read_endpoints,
            "metrics_service_provider_read",
            read_lines,
        )
        _append_endpoint(
            "Metrics read (Prometheus, user-ingested metrics)",
            observability_read_endpoints,
            "metrics_user_read",
            read_lines,
        )
        _append_federate_endpoints()
        _append_endpoint(
            "Logs read (Loki)", observability_read_endpoints, "logs_loki_read", read_lines
        )
        _append_endpoint(
            "Traces read (Tempo)", observability_read_endpoints, "traces_tempo_read", read_lines
        )
        _append_probe_url(
            "Metrics API probe (Nebius service metrics)",
            "metrics_service_provider_read",
            "/api/v1/query?query=count%28%7B__name__%3D~%22.%2B%22%7D%29",
        )
        _append_probe_url(
            "Metrics API probe (user-ingested metrics)",
            "metrics_user_read",
            "/api/v1/query?query=up",
        )
        _append_probe_url(
            "Logs API probe (default bucket)",
            "logs_loki_read",
            "/loki/api/v1/query?query=%7B__bucket__%3D%22default%22%7D",
        )
        _append_probe_url("Traces API probe", "traces_tempo_read", "/api/v2/search/tags")
        _append_endpoint(
            "Metrics write (OTLP HTTP/protobuf)",
            observability_write_endpoints,
            "metrics_otlp_write",
            write_lines,
        )
        _append_endpoint(
            "Metrics write (Prometheus Remote Write)",
            observability_write_endpoints,
            "metrics_prometheus_remote_write",
            write_lines,
        )
        _append_endpoint(
            "Metrics write (VM monitoring agent)",
            observability_write_endpoints,
            "metrics_platform_managed_write",
            write_lines,
        )
        _append_endpoint(
            "Logs write (direct/self-managed)",
            observability_write_endpoints,
            "logs_otlp_write",
            write_lines,
        )
        _append_endpoint(
            "Logs write (Nebius agent gRPC)",
            observability_write_endpoints,
            "logs_agent_grpc_write",
            write_lines,
        )
        _append_endpoint(
            "Logs write (VM monitoring agent)",
            observability_write_endpoints,
            "logs_platform_managed_write",
            write_lines,
        )
        _append_endpoint(
            "Traces write (OTLP gRPC)",
            observability_write_endpoints,
            "traces_otlp_grpc_write",
            write_lines,
        )
        if write_lines:
            write_lines.append(f"- Write auth: `{_lookup(observability_auth, 'write')}`")
            lines.extend(["## Observability Write Endpoints", "", *write_lines, ""])
        if read_lines:
            read_lines.append(f"- Read auth: `{_lookup(observability_auth, 'read')}`")
            read_lines.append(
                "- Bucket note: `service-provider` is a literal path segment for Nebius "
                "service metrics. Only the federation template placeholder "
                "`<service-provider>` should be replaced. This deployment shows "
                f"{_format_backtick_list(service_provider_metric_buckets)} bucket URLs."
            )
            lines.extend(["## Observability Read Endpoints", "", *read_lines, ""])
        grafana_statuses = read_grafana_status(paths)
        grafana_lines: list[str] = []
        for status in grafana_statuses:
            target_label = str(_lookup(status, "target_ref") or "current-cluster").strip()
            namespace = str(_lookup(status, "namespace") or "observability").strip()
            admin_secret = str(_lookup(status, "admin_secret_name") or "").strip()
            password_key = str(_lookup(status, "admin_password_key") or "admin-password").strip()
            password_command = (
                f"printf '%s\\n' \"$(kubectl -n {namespace} get secret {admin_secret} "
                f"-o jsonpath='{{.data.{password_key}}}' | base64 -d)\""
            )
            grafana_lines.extend(
                [
                    f"- Target `{target_label}` Grafana: {_markdown_link('Open Grafana', _lookup(status, 'base_url'))}",
                    f"- Target `{target_label}` metrics: {_markdown_link('Open metrics Explore', _lookup(status, 'metrics_url'))}",
                    f"- Target `{target_label}` logs: {_markdown_link('Open logs Explore', _lookup(status, 'logs_url'))}",
                    f"- Target `{target_label}` traces: {_markdown_link('Open traces Explore', _lookup(status, 'traces_url'))}",
                    f"- Target `{target_label}` dashboards: {_markdown_link('Open dashboards', _lookup(status, 'dashboards_url'))}",
                    f"- Target `{target_label}` credentials: user `admin`; password command:",
                    "```bash",
                    password_command,
                    "```",
                ]
            )
        if bool(_lookup(observability_summary, "grafana")) and not grafana_lines:
            grafana_lines.append(
                "- Grafana is configured for this project. The live Gateway/LoadBalancer URL is "
                "written after `deploy` or `flux apply` can read the Gateway status."
            )
        if grafana_lines:
            grafana_lines.append(
                "- Datasources are provisioned in Grafana with server/proxy access and a deploy-time "
                "Kubernetes Secret containing an Observability static token."
            )
            grafana_lines.append(
                "- Nebius dashboards expect the Prometheus service-metrics datasource name "
                "`Nebius Services`; cxcli provisions that display name with stable UID "
                "`nebius-service-metrics`."
            )
            lines.extend(["## Grafana", "", *grafana_lines, ""])
        if probe_lines:
            lines.extend(
                [
                    "## Read Endpoint Probe URLs",
                    "",
                    "- Use these URLs for browser or `curl` reachability checks with a valid "
                    "`Authorization` header. Without credentials, a live route can return "
                    "`401` or `403`; opening a Grafana datasource base URL directly can "
                    "return `404` even when its API subpaths are reachable.",
                    "- Read endpoints use global `read.*.api.nebius.cloud` hostnames. "
                    f"Region `{payload['infra']['region']}` applies to the write endpoints above.",
                    *probe_lines,
                    "",
                ]
            )
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
