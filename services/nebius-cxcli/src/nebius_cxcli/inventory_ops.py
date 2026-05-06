"""Deploy report generation helpers."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from .component_instances import component_instance_id, component_type_id
from .component_sources import component_output_root_name, load_component_sources
from .component_wiring import resolved_component_row
from .deploy_targets import enabled_cluster_target_refs
from .deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    build_deploy_validation_report,
    validation_section_lines,
)
from .generated_manifest import manifest_path_for_generated_dir
from .grafana_runtime import grafana_release_specs, read_grafana_status
from .observability import observability_endpoint_summary, observability_status_summary
from .paths import ProjectPaths
from .runtime_config import to_plain_data
from .terraform_ops import terraform_output_json


@dataclass(frozen=True)
class InventoryArtifacts:
    markdown: Path


@dataclass(frozen=True)
class _BundledGrafanaDashboard:
    folder: str
    dashboard: str
    uid: str
    title: str
    datasource: str


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


def _sentence(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    if value.endswith((".", "!", "?")):
        return value
    return f"{value}."


def _grafana_prometheus_datasource_notes(read_metadata: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for chart in load_component_sources().helm_charts:
        for datasource in chart.grafana.datasources:
            if str(datasource.datasource_type).lower() != "prometheus":
                continue
            endpoint_metadata = _mapping(_lookup(read_metadata, datasource.read_endpoint))
            endpoint_label = str(
                _lookup(endpoint_metadata, "label") or datasource.read_endpoint
            ).strip()
            note = f"`{datasource.name}` reads `{endpoint_label}`"
            description = _sentence(datasource.description)
            note = f"{note}: {description}" if description else _sentence(note)
            lines.append(f"  - {note}")
    if not lines:
        return []
    return ["- Prometheus datasource split:", *lines]


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


def _rows_enabled(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(bool(_lookup(row, "enabled")) for row in rows)


def _format_backtick_list(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values)


def _markdown_link(label: str, url: Any) -> str:
    value = str(url or "").strip()
    return f"[{label}]({value})" if value else "`pending`"


def _dashboard_markdown_link(label: str, url: Any) -> str:
    value = str(url or "").strip()
    return f"[{label}]({value})" if value else f"{label}: `pending`"


def _enabled_label(value: Any) -> str:
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "y", "1", "enabled"}:
        return "enabled"
    if text in {"false", "no", "n", "0", "disabled"}:
        return "disabled"
    return text or "unknown"


def _status_line(label: str, value: Any) -> str:
    return f"- {label}: `{_enabled_label(value)}`"


def _component_status_line(item: Mapping[str, Any]) -> str:
    component_id = str(_lookup(item, "id") or "").strip()
    label = str(_lookup(item, "label") or "").strip()
    enabled = _lookup(item, "enabled")
    if component_id and label:
        return f"- `{component_id}` ({label}): `{_enabled_label(enabled)}`"
    if component_id:
        return f"- `{component_id}`: `{_enabled_label(enabled)}`"
    return _status_line(label or "Unknown component", enabled)


def _grafana_status_key(status: Mapping[str, Any]) -> tuple[str, str, str]:
    target_label = str(_lookup(status, "target_ref") or "current-cluster").strip()
    namespace = str(_lookup(status, "namespace") or "observability").strip()
    release_name = str(
        _lookup(status, "release_name") or _lookup(status, "service_name") or "grafana"
    ).strip()
    return target_label, namespace, release_name


def _dashboard_payload(raw_json: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(raw_json or ""))
    except json.JSONDecodeError:
        return None
    return _mapping(payload)


def _packaged_grafana_dashboards_by_uid() -> dict[str, dict[str, Any]]:
    try:
        root = importlib_resources.files("nebius_cxcli").joinpath("grafana_dashboards")
        candidates = tuple(item for item in root.iterdir() if item.name.endswith(".json"))
    except (FileNotFoundError, NotADirectoryError, OSError):
        return {}

    dashboards: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        try:
            payload = _dashboard_payload(candidate.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError):
            continue
        if not payload:
            continue
        uid = str(payload.get("uid") or "").strip()
        if uid:
            dashboards[uid] = payload
    return dashboards


def _dashboard_defaults_from_chart(defaults: Sequence[Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    dashboards: list[tuple[str, str, Mapping[str, Any]]] = []
    for default in defaults:
        if getattr(default, "kind", "") != "literal":
            continue
        target_path = str(getattr(default, "target_path", "") or "")
        if not target_path.startswith("values.dashboards"):
            continue
        value = getattr(default, "value", None)
        path_parts = target_path.split(".")
        if target_path == "values.dashboards" and isinstance(value, Mapping):
            for folder, folder_dashboards in value.items():
                if not isinstance(folder_dashboards, Mapping):
                    continue
                for dashboard, dashboard_spec in folder_dashboards.items():
                    if isinstance(dashboard_spec, Mapping):
                        dashboards.append((str(folder), str(dashboard), dashboard_spec))
        elif len(path_parts) == 3 and isinstance(value, Mapping):
            folder = path_parts[2]
            for dashboard, dashboard_spec in value.items():
                if isinstance(dashboard_spec, Mapping):
                    dashboards.append((folder, str(dashboard), dashboard_spec))
        elif len(path_parts) == 4 and isinstance(value, Mapping):
            dashboards.append((path_parts[2], path_parts[3], value))
    return dashboards


def _bundled_grafana_dashboards() -> tuple[_BundledGrafanaDashboard, ...]:
    packaged_by_uid = _packaged_grafana_dashboards_by_uid()
    if not packaged_by_uid:
        return ()
    try:
        charts = load_component_sources().helm_charts
    except (OSError, ValueError, RuntimeError):
        return ()

    result: list[_BundledGrafanaDashboard] = []
    seen: set[tuple[str, str, str]] = set()
    for chart in charts:
        for folder, dashboard, spec in _dashboard_defaults_from_chart(chart.defaults):
            payload = _dashboard_payload(spec.get("json")) if isinstance(spec, Mapping) else None
            if not payload:
                continue
            uid = str(payload.get("uid") or "").strip()
            if not uid or packaged_by_uid.get(uid) != payload:
                continue
            key = (folder, dashboard, uid)
            if key in seen:
                continue
            seen.add(key)
            title = str(payload.get("title") or dashboard).strip() or dashboard
            result.append(
                _BundledGrafanaDashboard(
                    folder=folder,
                    dashboard=dashboard,
                    uid=uid,
                    title=title,
                    datasource=str(spec.get("datasource") or "").strip(),
                )
            )
    return tuple(result)


def _grafana_org_id() -> int:
    try:
        for chart in load_component_sources().helm_charts:
            grafana = getattr(chart, "grafana", None)
            if grafana is None:
                continue
            has_grafana_contract = bool(
                _dashboard_defaults_from_chart(getattr(chart, "defaults", ()))
                or getattr(grafana, "datasources", ())
                or getattr(grafana, "dashboard_signals", ())
            )
            if not has_grafana_contract:
                continue
            org_id = int(getattr(grafana, "org_id", 0) or 0)
            if org_id > 0:
                return org_id
    except (OSError, ValueError, RuntimeError):
        pass
    return 1


def _url_with_query_params(url: str, params: Mapping[str, str]) -> str:
    clean_params = {key: value for key, value in params.items() if key and value}
    if not clean_params:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(clean_params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _bundled_dashboard_url(
    *,
    base_url: str,
    dashboard_uid: str,
    cluster_id: str,
    org_id: int,
) -> str:
    if not base_url or not dashboard_uid:
        return ""
    url = urljoin(base_url.rstrip("/") + "/", f"d/{quote(dashboard_uid, safe='')}")
    params = {"orgId": str(org_id)}
    if cluster_id:
        params["var-Cluster"] = cluster_id
    return _url_with_query_params(url, params)


def _configured_grafana_statuses(
    config: Any,
    *,
    target_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...]:
    target_refs = enabled_cluster_target_refs(config)
    scoped_refs = target_refs or ("",)
    statuses: list[dict[str, Any]] = []
    for target_ref in scoped_refs:
        for spec in grafana_release_specs(config, target_ref=target_ref):
            status = {
                "target_ref": spec.target_ref,
                "namespace": spec.namespace,
                "release_name": spec.release_name,
                "service_name": spec.service_name,
                "admin_secret_name": spec.admin_secret_name,
                "admin_user": spec.admin_user,
                "admin_password_key": spec.admin_password_key,
                "gateway_name": spec.gateway_name,
                "gateway_namespace": spec.gateway_namespace,
                "base_url": "",
                "metrics_url": "",
                "logs_url": "",
                "traces_url": "",
                "dashboards_url": "",
            }
            metadata = _mapping((target_metadata or {}).get(str(spec.target_ref).strip()))
            cluster_id = str(_lookup(metadata, "cluster_id") or "").strip()
            kube_context = str(_lookup(metadata, "kube_context") or "").strip()
            if cluster_id:
                status["cluster_id"] = cluster_id
            if kube_context:
                status["kube_context"] = kube_context
            statuses.append(status)
    return tuple(statuses)


def _merge_grafana_statuses(
    configured: Sequence[Mapping[str, Any]],
    runtime: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {
        _grafana_status_key(status): dict(status) for status in configured
    }
    for status in runtime:
        key = _grafana_status_key(status)
        if merged and key not in merged:
            continue
        existing = merged.get(key, {})
        merged_status = dict(existing)
        merged_status.update(dict(status))
        merged[key] = merged_status
    return tuple(merged.values())


def _kube_context_segment(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]", "-", value.strip().lower()).strip("-._")
    return token or fallback


def _derived_kube_context(*, cluster_name: str, cluster_id: str, access: str) -> str:
    if not cluster_id:
        return ""
    cluster_name_token = _kube_context_segment(cluster_name or cluster_id, fallback="cluster")
    cluster_id_token = _kube_context_segment(cluster_id, fallback="cluster-id")
    access_token = _kube_context_segment(access or "external", fallback="access")
    return f"nebius-{cluster_name_token}-{cluster_id_token}-{access_token}"


def _output_value_text(outputs: Mapping[str, Any], output_name: str) -> str:
    payload = outputs.get(output_name)
    if isinstance(payload, Mapping) and "value" in payload:
        return str(to_plain_data(payload["value"]) or "").strip()
    return ""


def _terraform_outputs_if_available(paths: ProjectPaths) -> dict[str, Any]:
    candidate_dirs = [paths.infra_dir]
    canonical_infra_dir = paths.project_dir / "generated" / "infra"
    if canonical_infra_dir != paths.infra_dir:
        candidate_dirs.append(canonical_infra_dir)
    for infra_dir in candidate_dirs:
        if not (infra_dir / ".terraform").exists():
            continue
        try:
            return terraform_output_json(infra_dir, initialize=False)
        except Exception:
            continue
    return {}


def _manifest_target_metadata(paths: ProjectPaths) -> dict[str, dict[str, Any]]:
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    deploy = _mapping(_lookup(_mapping(manifest), "deploy"))
    targets = _lookup(deploy, "targets")
    if not isinstance(targets, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in targets:
        if not isinstance(item, Mapping):
            continue
        target_ref = str(
            _coalesce(_lookup(item, "target_ref"), _lookup(item, "instance_id"), "")
        ).strip()
        if not target_ref:
            continue
        result[target_ref] = {
            "target_ref": target_ref,
            "cluster_id_output_name": str(_lookup(item, "cluster_id_output_name") or "").strip(),
            "access": str(_lookup(item, "access") or "external").strip().lower() or "external",
        }
    return result


def _target_metadata_by_ref(
    *,
    paths: ProjectPaths,
    mk8s_rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    metadata = _manifest_target_metadata(paths)
    outputs = _terraform_outputs_if_available(paths)

    for row in mk8s_rows:
        instance_id = component_instance_id(row) or component_type_id(row)
        if not instance_id:
            continue
        row_metadata = metadata.setdefault(instance_id, {"target_ref": instance_id})
        row_metadata.setdefault(
            "cluster_id_output_name",
            component_output_root_name(instance_id, "cluster_id"),
        )
        row_metadata.setdefault(
            "cluster_name_output_name",
            component_output_root_name(instance_id, "cluster_name"),
        )

        inputs = _component_inputs(dict(row))
        api_endpoint = _mapping(_lookup(inputs, "api_endpoint"))
        public_endpoint = _coalesce(
            _lookup(api_endpoint, "public"),
            _lookup(inputs, "mk8s_cluster_public_endpoint"),
            _lookup(inputs, "api_endpoint_public"),
        )
        if "access" not in row_metadata:
            row_metadata["access"] = "external" if bool(public_endpoint) else "internal"

        configured_cluster_name = str(
            _coalesce(_lookup(inputs, "cluster_name"), instance_id)
        ).strip()
        cluster_name_output = str(row_metadata.get("cluster_name_output_name") or "").strip()
        cluster_name = (
            _output_value_text(outputs, cluster_name_output) if cluster_name_output else ""
        ) or configured_cluster_name
        if cluster_name:
            row_metadata["cluster_name"] = cluster_name

        cluster_id_output = str(row_metadata.get("cluster_id_output_name") or "").strip()
        cluster_id = _output_value_text(outputs, cluster_id_output) if cluster_id_output else ""
        if cluster_id:
            row_metadata["cluster_id"] = cluster_id
            row_metadata["kube_context"] = _derived_kube_context(
                cluster_name=cluster_name,
                cluster_id=cluster_id,
                access=str(row_metadata.get("access") or "external"),
            )
    for status in read_grafana_status(paths):
        if not isinstance(status, Mapping):
            continue
        target_ref = str(_lookup(status, "target_ref") or "").strip()
        if not target_ref:
            continue
        row_metadata = metadata.setdefault(target_ref, {"target_ref": target_ref})
        cluster_id = str(_lookup(status, "cluster_id") or "").strip()
        kube_context = str(_lookup(status, "kube_context") or "").strip()
        if cluster_id and not str(row_metadata.get("cluster_id") or "").strip():
            row_metadata["cluster_id"] = cluster_id
        if kube_context and not str(row_metadata.get("kube_context") or "").strip():
            row_metadata["kube_context"] = kube_context
    return metadata


def _shape_label(*parts: Any) -> str:
    values = [str(item).strip() for item in parts if str(item or "").strip()]
    return "/".join(values) if values else "n/a"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _node_count_text(value: Any) -> str:
    return f"`{_coalesce(value, 'n/a')}` node(s)"


def _mk8s_cluster_markdown_lines(cluster: Mapping[str, Any]) -> list[str]:
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
        group_count = _as_int(groups)
        nodes_per_group_count = _as_int(nodes_per_group)
        if group_count is not None and nodes_per_group_count is not None:
            total_nodes = group_count * nodes_per_group_count
            gpu_text = (
                f"`{total_nodes}` node(s) "
                f"(`{group_count}` group(s) x `{nodes_per_group_count}` node(s)/group) "
                f"at `{gpu_shape}`"
            )
        else:
            gpu_text = f"`{groups}x{nodes_per_group}` node(s) at `{gpu_shape}`"
    else:
        gpu_text = "`disabled`"
    fabric = _coalesce(_lookup(cluster, "infiniband_fabric"), "none")
    public_endpoint = _coalesce(_lookup(cluster, "api_public"), "unknown")
    cluster_id = str(_lookup(cluster, "cluster_id") or "").strip()
    kube_context = str(_lookup(cluster, "kube_context") or "").strip()
    lines = [
        f"- `{instance_id}` (`{cluster_name}`)",
        f"  - CPU nodes: {_node_count_text(cpu_count)} at `{cpu_shape}`",
        f"  - GPU nodes: {gpu_text}",
        f"  - InfiniBand fabric: `{fabric}`",
        f"  - Public endpoint: `{_enabled_label(public_endpoint)}`",
    ]
    if cluster_id:
        lines.append(f"  - Cluster ID: `{cluster_id}`")
    if kube_context:
        lines.append(f"  - Kube context: `{kube_context}`")
    return lines


def _mysterybox_secret_markdown_lines(instances: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for instance in instances:
        instance_id = str(_lookup(instance, "instance_id") or "mysterybox").strip()
        secret_names = [
            str(item.get("name") or "").strip()
            for item in _lookup(instance, "secrets") or []
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        ]
        if secret_names:
            lines.append(f"- `{instance_id}`: {_format_backtick_list(secret_names)}")
        else:
            lines.append(f"- `{instance_id}`: no secrets declared")
    return lines


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
    from .components import component_entries as _component_entries
    from .components import component_lookup as _component_lookup

    entry_by_id = _component_lookup("infra")
    infra_component_statuses = [
        {
            "id": entry.id,
            "label": entry.description,
            "enabled": _rows_enabled(infra_rows.get(entry.id, [])),
        }
        for entry in _component_entries("infra")
        if entry.status is not None
    ]

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
    mysterybox_rows = _rows_by_status_kind("nebius.mysterybox.secret")
    target_metadata = _target_metadata_by_ref(
        paths=paths,
        mk8s_rows=mk8s_rows,
    )

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
        instance_id = component_instance_id(row or {}) or component_type_id(row or {})
        metadata = _mapping(target_metadata.get(instance_id))
        return {
            "instance_id": instance_id,
            "cluster_name": cluster_name,
            "cluster_id": _lookup(metadata, "cluster_id"),
            "kube_context": _lookup(metadata, "kube_context"),
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
    mysterybox_summaries = [
        {
            "instance_id": component_instance_id(row) or component_type_id(row),
            "secrets": _lookup(_component_inputs(row), "secrets") or [],
        }
        for row in mysterybox_rows
        if bool(_lookup(row, "enabled"))
    ]

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
            "component_statuses": infra_component_statuses,
            "mk8s_enabled": _rows_enabled(mk8s_rows),
            "mysterybox_enabled": _rows_enabled(mysterybox_rows),
            "mysterybox_secrets": mysterybox_summaries,
            "wireguard_enabled": any(
                bool(_lookup(row, "enabled"))
                for cid, rows in infra_rows.items()
                for row in rows
                if "wireguard" in cid
            ),
        },
        "target_metadata": target_metadata,
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
            "external_secrets": _chart_enabled(
                app_rows, "external-secrets", "external_secrets"
            ),
            "nvidia_gpu_operator": _chart_enabled(
                app_rows, "nvidia-gpu-operator", "nvidia_gpu_operator"
            ),
            "nvidia_network_operator": _chart_enabled(
                app_rows, "nvidia-network-operator", "nvidia_network_operator"
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
    observability_read_metadata = _mapping(_lookup(observability_endpoints, "read_metadata"))
    observability_write_metadata = _mapping(_lookup(observability_endpoints, "write_metadata"))
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
        "## Client",
        "",
        f"- Client: `{_coalesce(_lookup(client_info, 'client_name'), '')}`",
        f"- Tenant: `{_coalesce(_lookup(nebius, 'tenant_id'), '')}`",
        f"- Project: `{_coalesce(_lookup(nebius, 'project_id'), '')}`",
        f"- Region: `{payload['infra']['region']}`",
        "",
        "## Infra",
        "",
        "### Component Status",
        "",
        *[
            _component_status_line(item)
            for item in payload["infra"].get("component_statuses", [])
            if isinstance(item, Mapping)
        ],
        "",
        "### MK8s Clusters",
        "",
    ]
    clusters = [cluster for cluster in payload.get("mk8s_clusters", []) if isinstance(cluster, Mapping)]
    if clusters:
        for cluster in clusters:
            lines.extend(_mk8s_cluster_markdown_lines(cluster))
    else:
        lines.append("- No MK8s clusters configured.")
    mysterybox_secrets = [
        item for item in payload["infra"].get("mysterybox_secrets", []) if isinstance(item, Mapping)
    ]
    if mysterybox_secrets:
        lines.extend(["", "### MysteryBox Secrets", ""])
        lines.extend(_mysterybox_secret_markdown_lines(mysterybox_secrets))
    lines.extend(
        [
            "",
            "## Apps",
            "",
            "### Platform Apps",
            "",
            _status_line("Envoy Gateway", payload["apps"]["envoy_gateway"]),
            _status_line("External Secrets Operator", payload["apps"]["external_secrets"]),
            _status_line("NVIDIA GPU Operator", payload["apps"]["nvidia_gpu_operator"]),
            _status_line(
                "NVIDIA Network Operator",
                payload["apps"]["nvidia_network_operator"],
            ),
            _status_line("cert-manager", payload["apps"]["cert_manager"]),
            _status_line("ExternalDNS", payload["apps"]["external_dns"]),
            "",
            "### Observability Apps",
            "",
            _status_line("Observability", _coalesce(_lookup(observability_summary, "enabled"), False)),
            _status_line(
                "K8s o11y agent",
                _coalesce(_lookup(observability_summary, "kubernetes_agent"), False),
            ),
            _status_line("Grafana", _coalesce(_lookup(observability_summary, "grafana"), False)),
            _status_line(
                "VM monitoring agent",
                _coalesce(_lookup(observability_summary, "vm_monitoring_agent"), False),
            ),
            _status_line(
                "VM journald logs (systemd services)",
                _coalesce(_lookup(observability_summary, "vm_journald_logs"), False),
            ),
            _status_line(
                "VM standalone collector",
                _coalesce(_lookup(observability_summary, "vm_standalone_collector"), False),
            ),
            _status_line(
                "VM standalone collector metrics",
                _coalesce(_lookup(observability_summary, "vm_standalone_metrics"), False),
            ),
            _status_line(
                "VM standalone collector logs",
                _coalesce(_lookup(observability_summary, "vm_standalone_logs"), False),
            ),
            f"- GPU DCGM metrics source: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_metric_source'), 'disabled')}`",
            f"- GPU DCGM node policy: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_node_policy'), 'not_managed')}`",
            f"- GPU DCGM live readiness: `{_coalesce(_lookup(observability_summary, 'gpu_dcgm_live_readiness'), 'not_configured')}`",
            "",
            "### Workloads",
            "",
            f"- n8n: `{_enabled_label(_coalesce(_lookup(n8n_summary, 'enabled'), False))}`; "
            f"hostname `{_coalesce(_lookup(n8n_summary, 'hostname'), 'n/a')}`",
            "",
        ]
    )
    if bool(_lookup(observability_endpoints, "configured")) and (
        bool(_lookup(observability_summary, "enabled"))
        or bool(_lookup(observability_summary, "vm_monitoring_agent"))
    ):
        read_lines: list[str] = []
        write_lines: list[str] = []
        service_provider_metric_buckets = tuple(
            str(item).strip()
            for item in _lookup(observability_endpoints, "service_provider_metric_buckets") or []
            if str(item).strip()
        )
        service_log_buckets = tuple(
            str(item).strip()
            for item in _lookup(observability_endpoints, "service_log_buckets") or []
            if str(item).strip()
        )

        def _append_endpoint_group(
            endpoints: dict[str, Any],
            metadata: dict[str, Any],
            target: list[str],
        ) -> bool:
            expanded_placeholder = False
            for key, raw_value in endpoints.items():
                value = str(raw_value or "").strip()
                if not value:
                    continue
                item_metadata = _mapping(_lookup(metadata, key))
                label = str(_lookup(item_metadata, "label") or key).strip() or key
                bucket_placeholder = str(_lookup(item_metadata, "bucket_placeholder") or "").strip()
                if bucket_placeholder and bucket_placeholder in value:
                    expanded_placeholder = True
                    for bucket in service_provider_metric_buckets:
                        bucket_label = (
                            f"{label[:-1]}, `{bucket}` bucket)"
                            if label.endswith(")")
                            else f"{label} (`{bucket}` bucket)"
                        )
                        target.append(
                            f"- {bucket_label}: `{value.replace(bucket_placeholder, bucket)}`"
                        )
                    continue
                target.append(f"- {label}: `{value}`")
            return expanded_placeholder

        expanded_bucket_placeholder = _append_endpoint_group(
            observability_read_endpoints,
            observability_read_metadata,
            read_lines,
        )
        _append_endpoint_group(
            observability_write_endpoints,
            observability_write_metadata,
            write_lines,
        )
        if write_lines:
            write_lines.append(f"- Write auth: `{_lookup(observability_auth, 'write')}`")
            lines.extend(["## Observability Write Endpoints", "", *write_lines, ""])
        if read_lines:
            read_lines.append(f"- Read auth: `{_lookup(observability_auth, 'read')}`")
            if expanded_bucket_placeholder:
                read_lines.append(
                    "- Bucket note: `service-provider` is a literal path segment for Nebius "
                    "service metrics. Only the federation template placeholder "
                    "`<service-provider>` should be replaced. This deployment shows "
                    f"{_format_backtick_list(service_provider_metric_buckets)} bucket URLs."
                )
            if service_log_buckets:
                read_lines.append(
                    "- Log bucket note: Nebius service logs are selected with the Loki "
                    "`__bucket__` label. This deployment has "
                    f"{_format_backtick_list(service_log_buckets)} service log buckets."
                )
            lines.extend(["## Observability Read Endpoints", "", *read_lines, ""])
        target_metadata = _mapping(_lookup(payload, "target_metadata"))
        grafana_statuses = _merge_grafana_statuses(
            _configured_grafana_statuses(config, target_metadata=target_metadata),
            read_grafana_status(paths),
        )
        bundled_dashboards = _bundled_grafana_dashboards()
        grafana_org_id = _grafana_org_id()
        grafana_lines: list[str] = []
        pending_grafana_links = False
        for status in grafana_statuses:
            target_label = str(_lookup(status, "target_ref") or "current-cluster").strip()
            namespace = str(_lookup(status, "namespace") or "observability").strip()
            admin_secret = str(_lookup(status, "admin_secret_name") or "").strip()
            admin_user = str(_lookup(status, "admin_user") or "").strip()
            password_key = str(_lookup(status, "admin_password_key") or "").strip()
            cluster_id = str(_lookup(status, "cluster_id") or "").strip()
            kube_context = str(_lookup(status, "kube_context") or "").strip()
            kube_context_arg = f" --context={shlex.quote(kube_context)}" if kube_context else ""
            if not str(_lookup(status, "base_url") or "").strip():
                pending_grafana_links = True
            password_command = (
                f"printf '%s\\n' \"$(kubectl{kube_context_arg} -n {namespace} get secret {admin_secret} "
                f"-o jsonpath='{{.data.{password_key}}}' | base64 -d)\""
            )
            target_info: list[str] = []
            if cluster_id:
                target_info.append(f"cluster ID `{cluster_id}`")
            if kube_context:
                target_info.append(f"kube context `{kube_context}`")
            bundled_dashboard_lines: list[str] = []
            if bundled_dashboards:
                base_url = str(_lookup(status, "base_url") or "").strip()
                bundled_dashboard_lines = ["- Bundled dashboards:"]
                for dashboard in bundled_dashboards:
                    dashboard_url = _bundled_dashboard_url(
                        base_url=base_url,
                        dashboard_uid=dashboard.uid,
                        cluster_id=cluster_id,
                        org_id=grafana_org_id,
                    )
                    dashboard_ref = f"`{dashboard.folder}/{dashboard.dashboard}`"
                    bundled_dashboard_lines.append(
                        "  - "
                        f"{_dashboard_markdown_link(dashboard.title, dashboard_url)} "
                        f"({dashboard_ref})"
                    )
            grafana_lines.extend(
                [
                    f"### Target `{target_label}`",
                    "",
                    *([f"- MK8s: {'; '.join(target_info)}"] if target_info else []),
                    f"- Grafana: {_markdown_link('Open Grafana', _lookup(status, 'base_url'))}",
                    *bundled_dashboard_lines,
                    f"- Credentials: user `{admin_user}`; password command:",
                    "",
                    "```bash",
                    password_command,
                    "```",
                    "",
                ]
            )
        grafana_note_lines: list[str] = []
        if pending_grafana_links:
            grafana_note_lines.append(
                "- Pending Grafana links are populated after `deploy` or `flux apply` can "
                "read each target Gateway/LoadBalancer status."
            )
        elif bool(_lookup(observability_summary, "grafana")) and not grafana_lines:
            grafana_note_lines.append(
                "- Grafana is configured for this project. The live Gateway/LoadBalancer URL is "
                "written after `deploy` or `flux apply` can read the Gateway status."
            )
        if grafana_lines or grafana_note_lines:
            grafana_lines.extend(["### Notes", "", *grafana_note_lines])
            grafana_lines.append(
                "- Datasources are provisioned in Grafana with server/proxy access and a deploy-time "
                "Kubernetes Secret containing an Observability static token."
            )
            grafana_lines.extend(
                _grafana_prometheus_datasource_notes(observability_read_metadata)
            )
            if bundled_dashboards:
                grafana_lines.append(
                    "- Bundled dashboard links list cxcli-owned JSON dashboards shipped "
                    "under `src/nebius_cxcli/grafana_dashboards`; operator-owned external "
                    "dashboard JSON is still imported into Grafana but is not listed here."
                )
            lines.extend(["## Grafana", "", *grafana_lines, ""])
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
