"""Observability policy helpers and chart materialization."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_instances import (
    INSTANCE_ID_FIELD,
    component_instance_id,
    component_instance_label,
    component_type_id,
    normalize_component_token,
)
from .component_sources import (
    AppObservabilitySettings,
    GrafanaCliSettings,
    InfraObservabilitySettings,
    ObservabilityEndpointTemplate,
    ObservabilityGrafanaSettings,
    ObservabilityMetricTarget,
    ObservabilityServiceBucket,
    helm_chart_source_by_id,
    load_component_sources,
    tf_module_source_by_id,
)
from .components import ComponentEntry, component_entries
from .deploy_targets import (
    TARGET_REF_FIELD,
    app_chart_target_ref,
    enabled_cluster_target_refs,
    is_auto_target_scoped_app_instance_id,
    target_scoped_app_instance_id,
)
from .mk8s_node_groups import first_gpu_node_group
from .mk8s_node_groups import gpu_enabled as mk8s_gpu_enabled
from .observability_validation import OBSERVABILITY_INGESTION_VALIDATION_KIND
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class KubernetesObservabilityConfig:
    logs_enabled: bool
    logs_collect_agent_logs: bool
    logs_excluded_namespaces: tuple[str, ...]
    metrics_enabled: bool
    metrics_collect_agent_metrics: bool
    metrics_collect_k8s_cluster_metrics: bool
    metrics_excluded_namespaces: tuple[str, ...]
    traces_enabled: bool


@dataclass(frozen=True)
class VmObservabilityConfig:
    logs_enabled: bool
    logs_systemd_units: tuple[str, ...]


@dataclass(frozen=True)
class ObservabilityAppSelection:
    selected_app_ids: tuple[str, ...]
    auto_enabled_app_ids: tuple[str, ...]
    issues: tuple[str, ...]
    observability_enabled: bool
    kubernetes_agent_required: bool
    collector_app_id: str
    vm_monitoring_agent_enabled: bool
    grafana_app_id: str = ""
    grafana_required: bool = False
    grafana_gateway_app_id: str = ""
    grafana_gateway_required: bool = False


@dataclass(frozen=True)
class ObservabilityGpuNodeLabelReconciliation:
    enabled: bool
    selector: tuple[tuple[str, str], ...] = ()
    labels: tuple[tuple[str, str], ...] = ()


_VM_JOURNALD_LOGS_ENABLED_LABEL = "nebius.o11y.systemd-logs-collection.enabled"
_VM_JOURNALD_LOGS_UNITS_LABEL = "nebius.o11y.systemd-logs-collection.units"


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    if not isinstance(payload, dict):
        return {}
    return payload


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _deploy_target_rows(payload: dict[str, Any], *, create: bool = False) -> list[Any]:
    deploy = payload.get("deploy")
    if not isinstance(deploy, dict):
        if not create:
            return []
        deploy = {}
        payload["deploy"] = deploy
    targets = deploy.get("targets")
    if not isinstance(targets, list):
        if not create:
            return []
        targets = []
        deploy["targets"] = targets
    return targets


def _deploy_target_instance_id(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(INSTANCE_ID_FIELD))


def _deploy_target_row_for_ref(
    payload: dict[str, Any],
    *,
    target_ref: str,
    create: bool = False,
) -> dict[str, Any] | None:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return None
    targets = _deploy_target_rows(payload, create=create)
    for row in targets:
        if isinstance(row, dict) and _deploy_target_instance_id(row) == normalized_target_ref:
            return row
    if not create:
        return None
    row: dict[str, Any] = {INSTANCE_ID_FIELD: normalized_target_ref}
    targets.append(row)
    return row


def _deploy_target_row_has_settings(row: Mapping[str, Any]) -> bool:
    return any(str(key) != INSTANCE_ID_FIELD for key in row)


def _catalog_sources():
    return load_component_sources()


def _mk8s_observability_settings() -> InfraObservabilitySettings:
    module = tf_module_source_by_id("mk8s", sources=_catalog_sources())
    return module.observability if module is not None else InfraObservabilitySettings()


def _vm_observability_settings() -> InfraObservabilitySettings:
    module = tf_module_source_by_id("vm", sources=_catalog_sources())
    return module.observability if module is not None else InfraObservabilitySettings()


def _app_observability_settings() -> dict[str, AppObservabilitySettings]:
    settings_by_id: dict[str, AppObservabilitySettings] = {}
    for chart in _catalog_sources().helm_charts:
        app_id = _as_text(chart.name).lower()
        settings = chart.observability
        if app_id and settings.metric_targets:
            settings_by_id[app_id] = settings
    return settings_by_id


def _collector_app_id() -> str:
    settings = _mk8s_observability_settings()
    return settings.chart_component_id if settings.mode == "kubernetes_agent" else ""


def _grafana_settings() -> ObservabilityGrafanaSettings:
    return _mk8s_observability_settings().grafana


def _grafana_app_id() -> str:
    settings = _grafana_settings()
    return settings.chart_component_id if settings.enabled_by_default else ""


def _grafana_gateway_app_id() -> str:
    settings = _grafana_settings()
    return settings.gateway_chart_component_id if settings.enabled_by_default else ""


def _grafana_cli_settings() -> GrafanaCliSettings:
    grafana_app_id = _grafana_app_id()
    if not grafana_app_id:
        return GrafanaCliSettings()
    chart = helm_chart_source_by_id(grafana_app_id, sources=_catalog_sources())
    return chart.grafana if chart is not None else GrafanaCliSettings()


def _observability_target_scoped_app_ids() -> set[str]:
    return {
        app_id
        for app_id in (_collector_app_id(), _grafana_app_id(), _grafana_gateway_app_id())
        if app_id
    }


def _enabled_component_ids(payload: dict[str, Any], *, scope: str) -> set[str]:
    if scope == "infra":
        rows = payload.get("infra", {}).get("components")
    else:
        rows = payload.get("apps", {}).get("charts")
    if not isinstance(rows, list):
        return set()
    return {
        component_type_id(row)
        for row in rows
        if isinstance(row, dict) and bool(row.get("enabled", False)) and component_type_id(row)
    }


def _app_chart_row(payload: dict[str, Any], app_id: str) -> dict[str, Any] | None:
    rows = _app_chart_rows_for_id(payload, app_id)
    return rows[0] if rows else None


def _app_chart_rows_for_id(payload: dict[str, Any], app_id: str) -> list[dict[str, Any]]:
    charts = payload.get("apps", {}).get("charts")
    if not isinstance(charts, list):
        return []
    return [row for row in charts if isinstance(row, dict) and component_type_id(row) == app_id]


def _app_chart_rows(payload: dict[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        apps = {}
        payload["apps"] = apps
    charts = apps.get("charts")
    if not isinstance(charts, list):
        charts = []
        apps["charts"] = charts
    return charts


def _chart_source_parts(entry: ComponentEntry) -> tuple[str, str]:
    source = str(entry.source or "").strip().rstrip("/")
    if "/" not in source:
        return "", source
    repo, chart_name = source.rsplit("/", maxsplit=1)
    return repo, chart_name


def _canonical_chart_repo(*, chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    if repo.startswith("oci://"):
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail != chart_name.strip().lower():
            return f"{repo}/{chart_name.strip()}"
    return repo


def _normalized_app_group(entry: ComponentEntry) -> str:
    raw_group = str(entry.group or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"


def _apply_observability_app_entry_defaults(
    *,
    row: dict[str, Any],
    entry: ComponentEntry,
) -> None:
    chart_repo = str(entry.chart_repo or "").strip()
    chart_name = str(entry.chart_name or "").strip()
    if not chart_name:
        chart_repo, chart_name = _chart_source_parts(entry)
    if not chart_name:
        chart_name = entry.id
    if not str(row.get("repo", "")).strip():
        row["repo"] = _canonical_chart_repo(chart_repo=chart_repo, chart_name=chart_name)
    if not str(row.get("version", "")).strip() and entry.version:
        row["version"] = str(entry.version)
    if not str(row.get("namespace", "")).strip():
        row["namespace"] = str(entry.default_namespace or "").strip() or entry.id
    if not str(row.get("release-name", "")).strip():
        row["release-name"] = str(entry.default_release_name or "").strip() or entry.id
    if not str(row.get("group", "")).strip():
        row["group"] = _normalized_app_group(entry)
    if not isinstance(row.get("values"), dict):
        row["values"] = {}
    if entry.defaults:
        resolved = resolve_component_defaults(
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            include_shared=False,
        )
        row.clear()
        row.update(resolved)
    _strip_grafana_source_dashboard_values(row)


def _new_observability_app_row(entry: ComponentEntry) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": entry.id,
        "instance_id": entry.id,
        "group": _normalized_app_group(entry),
        "enabled": True,
        "repo": "",
        "version": "",
        "namespace": "",
        "release-name": "",
        "values": {},
    }
    _apply_observability_app_entry_defaults(row=row, entry=entry)
    return row


def _observability_instance_id(app_id: str, *, target_ref: str = "") -> str:
    return target_scoped_app_instance_id(app_id, target_ref=target_ref)


def _target_scoped_validation_name(base_name: str, *, target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    return f"{base_name} ({normalized_target_ref})" if normalized_target_ref else base_name


def _target_scoped_report_file(base_name: str, *, target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return base_name
    stem, suffix = base_name.rsplit(".", maxsplit=1) if "." in base_name else (base_name, "json")
    return f"{stem}-{normalized_target_ref}.{suffix}"


def _normalize_target_scoped_observability_instance_id(
    row: dict[str, Any],
    *,
    app_id: str,
    target_ref: str,
) -> None:
    if not str(row.get("instance_id", "")).strip() or is_auto_target_scoped_app_instance_id(
        row.get("instance_id"),
        app_id=app_id,
        target_ref=target_ref,
    ):
        row["instance_id"] = _observability_instance_id(app_id, target_ref=target_ref)


def _required_observability_app_target_refs(
    payload: dict[str, Any], app_id: str
) -> tuple[str, ...]:
    collector_app_id = _collector_app_id()
    grafana_app_id = _grafana_app_id()
    grafana_gateway_app_id = _grafana_gateway_app_id()
    if app_id == collector_app_id:
        return tuple(
            target_ref
            for target_ref in enabled_cluster_target_refs(payload)
            if _kubernetes_agent_required(payload, target_ref=target_ref)
        )
    if app_id == grafana_app_id:
        return tuple(
            target_ref
            for target_ref in enabled_cluster_target_refs(payload)
            if _grafana_required(payload, target_ref=target_ref)
        )
    if app_id == grafana_gateway_app_id:
        return tuple(
            target_ref
            for target_ref in enabled_cluster_target_refs(payload)
            if _grafana_required(payload, target_ref=target_ref)
        )
    return ()


def _apply_observability_target_ref(
    *,
    payload: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    target_refs = enabled_cluster_target_refs(payload)
    if len(target_refs) == 1:
        target_ref = target_refs[0]
        if row.get(TARGET_REF_FIELD) != target_ref:
            row[TARGET_REF_FIELD] = target_ref
            return True
        return False
    return False


def _nested_path_value(value: Any, dotted_path: str) -> Any:
    current = value
    for raw_segment in dotted_path.split("."):
        segment = str(raw_segment).strip()
        if not segment or not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _deep_merge_mapping(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        existing = merged.get(str(key))
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _deep_merge_mapping(existing, value)
        else:
            merged[str(key)] = copy.deepcopy(value)
    return merged


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = _as_text(value).lower()
    if normalized in {"true", "t", "1", "yes", "y"}:
        return True
    if normalized in {"false", "f", "0", "no", "n"}:
        return False
    return None


def _coerce_optional_string_list(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(_as_text(item) for item in value if _as_text(item))


def _project_observability_config(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> dict[str, Any]:
    root_config = _mapping(_mapping(payload.get("deploy")).get("observability"))
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return root_config
    target_row = _deploy_target_row_for_ref(payload, target_ref=normalized_target_ref)
    if not isinstance(target_row, Mapping):
        return root_config
    target_config = _mapping(target_row.get("observability"))
    if not target_config:
        return root_config
    merged = copy.deepcopy(root_config)
    return _deep_merge_mapping(merged, target_config)


def _payload_nebius_context(payload: dict[str, Any]) -> dict[str, Any]:
    client_info = _mapping(payload.get("client_info"))
    return _mapping(client_info.get("nebius"))


def _payload_project_id(payload: dict[str, Any]) -> str:
    nebius = _payload_nebius_context(payload)
    project_id = _as_text(nebius.get("project_id"))
    if project_id:
        return project_id
    rows = payload.get("infra", {}).get("components")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        inputs = _mapping(row.get("inputs"))
        project_id = _as_text(inputs.get("parent_id"))
        if project_id:
            return project_id
    return ""


def _payload_region_id(payload: dict[str, Any]) -> str:
    nebius = _payload_nebius_context(payload)
    return _as_text(nebius.get("region_id") or nebius.get("region"))


def _global_endpoint_templates() -> tuple[
    tuple[ObservabilityEndpointTemplate, ...],
    tuple[ObservabilityEndpointTemplate, ...],
]:
    templates = load_component_sources().observability.endpoints
    return templates.read, templates.write


def _render_endpoint_template(
    template: str,
    *,
    project_id: str,
    region_id: str,
) -> str:
    return _as_text(template).replace("{project_id}", project_id).replace("{region}", region_id)


def _render_observability_endpoint_group(
    endpoints: tuple[ObservabilityEndpointTemplate, ...],
    *,
    project_id: str,
    region_id: str,
    signals: Mapping[str, bool],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    rendered: dict[str, str] = {}
    metadata: dict[str, dict[str, str]] = {}
    for endpoint in endpoints:
        include_when = endpoint.include_when or ("always",)
        if not any(
            condition == "always" or bool(signals.get(condition)) for condition in include_when
        ):
            continue
        value = _render_endpoint_template(
            endpoint.template,
            project_id=project_id,
            region_id=region_id,
        )
        if value:
            rendered[endpoint.key] = value
            metadata[endpoint.key] = {
                "label": endpoint.label,
                "bucket_placeholder": endpoint.bucket_placeholder,
            }
    return rendered, metadata


def observability_project_defaults(
    *,
    mk8s_settings: InfraObservabilitySettings | None = None,
    vm_settings: InfraObservabilitySettings | None = None,
    include_kubernetes: bool = True,
    include_vm: bool = True,
) -> dict[str, Any]:
    settings = mk8s_settings or _mk8s_observability_settings()
    vm_observability = vm_settings or _vm_observability_settings()
    defaults: dict[str, Any] = {
        "enabled": False,
    }
    if include_kubernetes and settings.mode == "kubernetes_agent":
        defaults["kubernetes"] = {
            "logs": {
                "enabled": settings.logs.enabled_by_default,
                "collect_agent_logs": settings.logs.collect_agent_logs,
                "excluded_namespaces": list(settings.logs.excluded_namespaces),
            },
            "metrics": {
                "enabled": settings.metrics.enabled_by_default,
                "collect_agent_metrics": settings.metrics.collect_agent_metrics,
                "collect_k8s_cluster_metrics": settings.metrics.collect_k8s_cluster_metrics,
                "excluded_namespaces": list(settings.metrics.excluded_namespaces),
            },
            "traces": {
                "enabled": settings.traces.enabled_by_default,
            },
        }
    if include_vm and vm_observability.mode == "monitoring_agent":
        defaults["vm"] = {
            "logs": {
                "enabled": vm_observability.logs.enabled_by_default,
                "systemd_units": list(vm_observability.logs.systemd_units),
            },
        }
    return defaults


def normalize_observability_project_settings(payload: dict[str, Any]) -> bool:
    payload_map = payload if isinstance(payload, dict) else _as_payload(payload)
    if not payload_map:
        return False

    infra = payload_map.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return False
    component_ids = {
        component_type_id(item)
        for item in components
        if isinstance(item, dict)
        and bool(item.get("enabled", False))
        and component_type_id(item) in {"mk8s", "vm"}
    }
    mk8s_target_refs = [
        component_instance_id(item)
        for item in components
        if isinstance(item, dict)
        and component_type_id(item) == "mk8s"
        and bool(item.get("enabled", False))
        and component_instance_id(item)
    ]
    deploy_raw = payload_map.get("deploy")
    if deploy_raw is None:
        if not component_ids:
            return False
        deploy = {}
        payload_map["deploy"] = deploy
    elif isinstance(deploy_raw, dict):
        deploy = deploy_raw
    else:
        return False
    if not component_ids:
        changed = False
        targets = deploy.get("targets")
        if isinstance(targets, list):
            next_targets: list[Any] = []
            for row in targets:
                if not isinstance(row, dict):
                    next_targets.append(row)
                    continue
                if "observability" in row:
                    row.pop("observability", None)
                    changed = True
                if _deploy_target_row_has_settings(row):
                    next_targets.append(row)
            if next_targets != targets:
                deploy["targets"] = next_targets
                changed = True
            if not next_targets:
                deploy.pop("targets", None)
        root_observability = deploy.get("observability")
        if (
            isinstance(root_observability, Mapping)
            and "kubernetes" not in root_observability
            and "observability" in deploy
        ):
            deploy.pop("observability", None)
            changed = True
        if not deploy:
            payload_map.pop("deploy", None)
            changed = True
        return changed
    existing = deploy.get("observability")
    if existing is not None and not isinstance(existing, Mapping):
        return False
    root_existing = copy.deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
    changed = False

    kubernetes_defaults = observability_project_defaults(
        include_kubernetes=True,
        include_vm=False,
    )
    seen_targets = set(mk8s_target_refs)
    if mk8s_target_refs:
        for target_ref in mk8s_target_refs:
            target_row = _deploy_target_row_for_ref(payload_map, target_ref=target_ref, create=True)
            if target_row is None:
                continue
            target_existing = target_row.get("observability")
            if target_existing is not None and not isinstance(target_existing, Mapping):
                continue
            merged_target = copy.deepcopy(kubernetes_defaults)
            if isinstance(target_existing, Mapping):
                merged_target = _deep_merge_mapping(merged_target, target_existing)
            if target_row.get("observability") != merged_target:
                target_row["observability"] = merged_target
                changed = True

    targets = deploy.get("targets")
    if isinstance(targets, list):
        next_targets: list[Any] = []
        for row in targets:
            if not isinstance(row, dict):
                next_targets.append(row)
                continue
            target_ref = _deploy_target_instance_id(row)
            if target_ref and target_ref not in seen_targets and "observability" in row:
                row.pop("observability", None)
                changed = True
            if _deploy_target_row_has_settings(row) or target_ref in seen_targets:
                next_targets.append(row)
        if next_targets != targets:
            deploy["targets"] = next_targets
            changed = True
        if not next_targets:
            deploy.pop("targets", None)

    if "vm" not in component_ids:
        root_observability = deploy.get("observability")
        if (
            isinstance(root_observability, Mapping)
            and "kubernetes" not in root_observability
            and "observability" in deploy
        ):
            deploy.pop("observability", None)
            changed = True
    else:
        defaults = observability_project_defaults(
            include_kubernetes=False,
            include_vm=True,
        )
        merged = copy.deepcopy(defaults)
        existing_filtered = copy.deepcopy(root_existing)
        merged = _deep_merge_mapping(merged, existing_filtered)
        if deploy.get("observability") != merged:
            deploy["observability"] = merged
            changed = True

    if not deploy:
        payload_map.pop("deploy", None)
        changed = True
    return changed


def _effective_kubernetes_observability_config(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
    mk8s_settings: InfraObservabilitySettings | None = None,
) -> KubernetesObservabilityConfig:
    settings = mk8s_settings or _mk8s_observability_settings()
    defaults = observability_project_defaults(mk8s_settings=settings).get("kubernetes", {})
    project = _project_observability_config(payload, target_ref=target_ref)
    kubernetes = _mapping(project.get("kubernetes"))
    logs = _mapping(kubernetes.get("logs"))
    metrics = _mapping(kubernetes.get("metrics"))
    traces = _mapping(kubernetes.get("traces"))
    default_logs = _mapping(_mapping(defaults).get("logs"))
    default_metrics = _mapping(_mapping(defaults).get("metrics"))
    default_traces = _mapping(_mapping(defaults).get("traces"))

    def _resolve_bool(
        value: Any,
        *,
        default: bool,
    ) -> bool:
        coerced = _coerce_optional_bool(value)
        return default if coerced is None else coerced

    def _resolve_list(
        value: Any,
        *,
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        coerced = _coerce_optional_string_list(value)
        return default if coerced is None else coerced

    return KubernetesObservabilityConfig(
        logs_enabled=_resolve_bool(
            logs.get("enabled"), default=bool(default_logs.get("enabled", True))
        ),
        logs_collect_agent_logs=_resolve_bool(
            logs.get("collect_agent_logs"),
            default=bool(default_logs.get("collect_agent_logs", False)),
        ),
        logs_excluded_namespaces=_resolve_list(
            logs.get("excluded_namespaces"),
            default=tuple(default_logs.get("excluded_namespaces", [])),
        ),
        metrics_enabled=_resolve_bool(
            metrics.get("enabled"),
            default=bool(default_metrics.get("enabled", True)),
        ),
        metrics_collect_agent_metrics=_resolve_bool(
            metrics.get("collect_agent_metrics"),
            default=bool(default_metrics.get("collect_agent_metrics", False)),
        ),
        metrics_collect_k8s_cluster_metrics=_resolve_bool(
            metrics.get("collect_k8s_cluster_metrics"),
            default=bool(default_metrics.get("collect_k8s_cluster_metrics", False)),
        ),
        metrics_excluded_namespaces=_resolve_list(
            metrics.get("excluded_namespaces"),
            default=tuple(default_metrics.get("excluded_namespaces", [])),
        ),
        traces_enabled=_resolve_bool(
            traces.get("enabled"),
            default=bool(default_traces.get("enabled", True)),
        ),
    )


def _effective_vm_observability_config(
    payload: dict[str, Any],
    *,
    vm_settings: InfraObservabilitySettings | None = None,
) -> VmObservabilityConfig:
    settings = vm_settings or _vm_observability_settings()
    defaults = observability_project_defaults(vm_settings=settings).get("vm", {})
    project = _project_observability_config(payload)
    vm = _mapping(project.get("vm"))
    logs = _mapping(vm.get("logs"))
    default_logs = _mapping(_mapping(defaults).get("logs"))

    def _resolve_bool(
        value: Any,
        *,
        default: bool,
    ) -> bool:
        coerced = _coerce_optional_bool(value)
        return default if coerced is None else coerced

    def _resolve_list(
        value: Any,
        *,
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        coerced = _coerce_optional_string_list(value)
        return default if coerced is None else coerced

    return VmObservabilityConfig(
        logs_enabled=_resolve_bool(
            logs.get("enabled"),
            default=bool(default_logs.get("enabled", False)),
        ),
        logs_systemd_units=_resolve_list(
            logs.get("systemd_units"),
            default=tuple(default_logs.get("systemd_units", [])),
        ),
    )


def _observability_enabled(payload: dict[str, Any], *, target_ref: str = "") -> bool:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        for row in _deploy_target_rows(payload):
            if not isinstance(row, Mapping):
                continue
            row_target_ref = _deploy_target_instance_id(row)
            if row_target_ref and _observability_enabled(payload, target_ref=row_target_ref):
                return True
    project = _project_observability_config(payload, target_ref=normalized_target_ref)
    enabled = _coerce_optional_bool(project.get("enabled"))
    return bool(enabled)


def _root_observability_enabled(payload: dict[str, Any]) -> bool:
    project = _project_observability_config(payload)
    enabled = _coerce_optional_bool(project.get("enabled"))
    return bool(enabled)


def _kubernetes_agent_required(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
    kubernetes_settings: KubernetesObservabilityConfig | None = None,
) -> bool:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return any(
            _kubernetes_agent_required(payload, target_ref=item)
            for item in enabled_cluster_target_refs(payload)
        )
    if not _observability_enabled(payload, target_ref=normalized_target_ref):
        return False
    if "mk8s" not in _enabled_component_ids(payload, scope="infra"):
        return False
    effective = kubernetes_settings or _effective_kubernetes_observability_config(
        payload,
        target_ref=normalized_target_ref,
    )
    return effective.logs_enabled or effective.metrics_enabled or effective.traces_enabled


def _grafana_required(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> bool:
    if not _grafana_app_id():
        return False
    return _kubernetes_agent_required(payload, target_ref=target_ref)


def _vm_monitoring_agent_enabled(payload: dict[str, Any]) -> bool:
    if "vm" not in _enabled_component_ids(payload, scope="infra"):
        return False
    return _vm_observability_settings().mode == "monitoring_agent"


def _vm_journald_logs_enabled(
    payload: dict[str, Any],
    *,
    vm_settings: VmObservabilityConfig | None = None,
) -> bool:
    if not _root_observability_enabled(payload):
        return False
    if not _vm_monitoring_agent_enabled(payload):
        return False
    effective = vm_settings or _effective_vm_observability_config(payload)
    return effective.logs_enabled


def _selected_app_metric_targets(
    payload: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ObservabilityMetricTarget], ...]:
    policies = _app_observability_settings()
    resolved: list[tuple[dict[str, Any], ObservabilityMetricTarget]] = []
    for row in _app_chart_rows(payload):
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        app_id = component_type_id(row)
        settings = policies.get(app_id)
        if settings is None:
            continue
        for target in settings.metric_targets:
            resolved.append((row, target))
    return tuple(resolved)


def _infra_component_rows(payload: dict[str, Any]) -> list[Any]:
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return []
    components = infra.get("components")
    return components if isinstance(components, list) else []


def _path_lookup(node: Mapping[str, Any], path: str) -> Any:
    current: Any = node
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _service_bucket_condition_matches(condition: str, row: Mapping[str, Any]) -> bool:
    normalized = _as_text(condition)
    if not normalized or normalized == "always":
        return True
    if normalized == "enabled":
        return bool(row.get("enabled", False))
    if normalized == "mk8s_gpu":
        return _mk8s_gpu_nodes_enabled(row)
    if normalized.startswith("inputs."):
        value = _path_lookup(_mapping(row.get("inputs")), normalized.removeprefix("inputs."))
        return bool(value)
    return False


def _service_bucket_matches(row: Mapping[str, Any], bucket: ObservabilityServiceBucket) -> bool:
    conditions = bucket.include_when or ("enabled",)
    return any(_service_bucket_condition_matches(condition, row) for condition in conditions)


def _observability_service_buckets(
    payload: dict[str, Any],
    *,
    signal: str,
) -> tuple[str, ...]:
    sources_by_component = {module.module: module for module in _catalog_sources().tf_modules}
    selected: list[str] = []
    seen: set[str] = set()
    for raw_row in _infra_component_rows(payload):
        if not isinstance(raw_row, Mapping) or not bool(raw_row.get("enabled", False)):
            continue
        component_id = component_type_id(raw_row)
        module = sources_by_component.get(component_id)
        if module is None:
            continue
        buckets = (
            module.observability.service_metrics
            if signal == "metrics"
            else module.observability.service_logs
        )
        for bucket in buckets:
            if bucket.name in seen or not _service_bucket_matches(raw_row, bucket):
                continue
            seen.add(bucket.name)
            selected.append(bucket.name)
    return tuple(selected)


def _mk8s_gpu_nodes_enabled(row: Mapping[str, Any]) -> bool:
    inputs = row.get("inputs")
    if not isinstance(inputs, Mapping):
        return False
    return mk8s_gpu_enabled(inputs)


def _mk8s_default_gpu_stack_source() -> str:
    module = tf_module_source_by_id("mk8s", sources=_catalog_sources())
    if module is None:
        return ""
    return _as_text(module.mk8s_gpu.default_stack_source).lower()


def _mk8s_gpu_stack_source(row: Mapping[str, Any]) -> str:
    inputs = row.get("inputs")
    gpu_group = first_gpu_node_group(_mapping(inputs))
    explicit = _as_text(gpu_group.gpu_stack_source if gpu_group is not None else "").lower()
    return explicit or _mk8s_default_gpu_stack_source()


def _observability_gpu_node_label_targets(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> tuple[ObservabilityMetricTarget, ...]:
    if not _observability_enabled(payload, target_ref=target_ref):
        return ()
    if "mk8s" not in _enabled_component_ids(payload, scope="infra"):
        return ()
    if not _effective_kubernetes_observability_config(
        payload, target_ref=target_ref
    ).metrics_enabled:
        return ()
    return tuple(
        target
        for _row, target in _selected_app_metric_targets(payload)
        if target.required_gpu_node_labels
    )


def _target_matches_mk8s_row(target: ObservabilityMetricTarget, row: Mapping[str, Any]) -> bool:
    stack_sources = {item.lower() for item in target.required_gpu_node_label_stack_sources}
    if not stack_sources:
        return True
    return _mk8s_gpu_stack_source(row) in stack_sources


def _merge_label_mapping(
    target: dict[str, str],
    values: tuple[tuple[str, str], ...],
    *,
    conflict_label: str,
) -> None:
    for key, value in values:
        existing = target.get(key)
        if existing is not None and existing != value:
            raise RuntimeError(
                f"Observability metric targets resolve conflicting {conflict_label} for '{key}'"
            )
        target[key] = value


def _observability_gpu_node_labels_for_row(
    payload: dict[str, Any],
    row: Mapping[str, Any],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for target in _observability_gpu_node_label_targets(
        payload,
        target_ref=component_instance_id(row),
    ):
        if not _target_matches_mk8s_row(target, row):
            continue
        _merge_label_mapping(
            labels,
            target.required_gpu_node_labels,
            conflict_label="GPU node labels",
        )
    return labels


def _catalog_observability_gpu_node_label_values() -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    for settings in _app_observability_settings().values():
        for target in settings.metric_targets:
            for key, value in target.required_gpu_node_labels:
                labels.setdefault(key, set()).add(value)
    return labels


def _gpu_node_group_label_maps(
    row: dict[str, Any],
    *,
    create: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    inputs = row.get("inputs")
    if not isinstance(inputs, dict):
        if not create:
            return None, None, None
        inputs = {}
        row["inputs"] = inputs
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, dict):
        if not create:
            return inputs, None, None
        node_groups = {}
        inputs["node_groups"] = node_groups
    target_group: dict[str, Any] | None = None
    for group in node_groups.values():
        if isinstance(group, dict) and bool(group.get("gpu", False)):
            target_group = group
            break
    if target_group is None:
        if not create:
            return inputs, node_groups, None
        target_group = {}
        node_groups["worker"] = target_group
    labels = target_group.get("node_labels")
    if not isinstance(labels, dict):
        if not create:
            return inputs, target_group, None
        labels = {}
        target_group["node_labels"] = labels
    return inputs, target_group, labels


def _prune_empty_gpu_node_label_maps(
    *,
    inputs: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    labels: dict[str, Any] | None,
) -> None:
    if overrides is not None and labels is not None and not labels:
        overrides.pop("node_labels", None)
    if inputs is not None and overrides is not None and not overrides:
        node_groups = inputs.get("node_groups")
        if isinstance(node_groups, dict):
            for key, value in list(node_groups.items()):
                if value is overrides:
                    node_groups.pop(key, None)
                    break


def observability_gpu_node_label_reconciliation(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> ObservabilityGpuNodeLabelReconciliation:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    normalized_target_ref = normalize_component_token(target_ref)
    labels: dict[str, str] = {}
    selector: dict[str, str] = {}
    for row in _infra_component_rows(payload):
        if (
            not isinstance(row, dict)
            or component_type_id(row) != "mk8s"
            or not bool(row.get("enabled", False))
            or not _mk8s_gpu_nodes_enabled(row)
        ):
            continue
        row_target_ref = component_instance_id(row)
        if normalized_target_ref and row_target_ref != normalized_target_ref:
            continue
        for target in _observability_gpu_node_label_targets(payload, target_ref=row_target_ref):
            if not _target_matches_mk8s_row(target, row):
                continue
            _merge_label_mapping(
                labels,
                target.required_gpu_node_labels,
                conflict_label="GPU node labels",
            )
            _merge_label_mapping(
                selector,
                target.required_gpu_node_selector,
                conflict_label="GPU node selectors",
            )
    enabled = bool(labels and selector)
    return ObservabilityGpuNodeLabelReconciliation(
        enabled=enabled,
        selector=tuple(sorted(selector.items())) if enabled else (),
        labels=tuple(sorted(labels.items())) if enabled else (),
    )


def resolve_observability_app_selection(
    payload_or_config: Any,
    *,
    selected_app_ids: set[str] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> ObservabilityAppSelection:
    _ = cli_settings
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    selected = {
        str(item).strip().lower() for item in (selected_app_ids or set()) if str(item).strip()
    }
    collector_app_id = _collector_app_id()
    grafana_app_id = _grafana_app_id()
    grafana_gateway_app_id = _grafana_gateway_app_id()
    kubernetes_settings = _effective_kubernetes_observability_config(payload)
    observability_enabled = _observability_enabled(payload)
    kubernetes_agent_required = _kubernetes_agent_required(
        payload,
        kubernetes_settings=kubernetes_settings,
    )
    grafana_required = _grafana_required(payload)
    grafana_gateway_required = bool(grafana_required and grafana_gateway_app_id)
    vm_monitoring_agent_enabled = _vm_monitoring_agent_enabled(payload)
    available_ids = (
        {entry.id for entry in app_entries}
        if app_entries is not None
        else {entry.id for entry in component_entries("apps")}
    )

    issues: list[str] = []
    auto_enabled: list[str] = []

    if collector_app_id:
        collector_row = _app_chart_row(payload, collector_app_id)
        if (
            collector_row is not None
            and _nested_path_value(_mapping(collector_row), "values.config.iam.enabled") is False
        ):
            issues.append(
                "Observability-enabled MK8s deployment requires "
                f"'{collector_app_id}.values.config.iam.enabled' to stay true"
            )
        if collector_app_id in selected:
            collector_rows = [
                row
                for row in _app_chart_rows_for_id(payload, collector_app_id)
                if bool(row.get("enabled", False))
            ]
            if collector_rows:
                targeted_rows = [
                    (row, app_chart_target_ref(row))
                    for row in collector_rows
                    if app_chart_target_ref(row)
                ]
                for row, row_target_ref in targeted_rows:
                    if not _observability_enabled(
                        payload,
                        target_ref=row_target_ref,
                    ):
                        app_label = component_instance_label(
                            collector_app_id,
                            component_instance_id(row),
                        )
                        issues.append(
                            f"apps:{app_label} requires "
                            f"deploy.targets[instance_id={row_target_ref}].observability.enabled=true"
                        )
                if not targeted_rows and not observability_enabled:
                    issues.append(
                        f"apps:{collector_app_id} requires "
                        "deploy.targets[].observability.enabled=true for an MK8s target"
                    )
            elif not observability_enabled:
                issues.append(
                    f"apps:{collector_app_id} requires "
                    "deploy.targets[].observability.enabled=true for an MK8s target"
                )
        if collector_app_id in selected and "mk8s" not in _enabled_component_ids(
            payload, scope="infra"
        ):
            issues.append(f"apps:{collector_app_id} requires one enabled infra:mk8s component")
        if kubernetes_agent_required:
            if not collector_app_id:
                issues.append(
                    "Observability-enabled MK8s deployment requires a collector app id under "
                    "components.infra.mk8s.cli.observability.primary_agent.chart_component_id"
                )
            elif collector_app_id not in available_ids:
                issues.append(
                    f"components.infra.mk8s.cli.observability.primary_agent.chart_component_id references "
                    f"unknown or unavailable apps component '{collector_app_id}'"
                )
            elif collector_app_id not in selected:
                selected.add(collector_app_id)
                auto_enabled.append(collector_app_id)

    if grafana_app_id:
        if grafana_app_id in selected:
            grafana_rows = [
                row
                for row in _app_chart_rows_for_id(payload, grafana_app_id)
                if bool(row.get("enabled", False))
            ]
            if grafana_rows:
                targeted_rows = [
                    (row, app_chart_target_ref(row))
                    for row in grafana_rows
                    if app_chart_target_ref(row)
                ]
                for row, row_target_ref in targeted_rows:
                    if not _observability_enabled(payload, target_ref=row_target_ref):
                        app_label = component_instance_label(
                            grafana_app_id,
                            component_instance_id(row),
                        )
                        issues.append(
                            f"apps:{app_label} requires "
                            f"deploy.targets[instance_id={row_target_ref}].observability.enabled=true"
                        )
                if not targeted_rows and not observability_enabled:
                    issues.append(
                        f"apps:{grafana_app_id} requires "
                        "deploy.targets[].observability.enabled=true for an MK8s target"
                    )
            elif not observability_enabled:
                issues.append(
                    f"apps:{grafana_app_id} requires "
                    "deploy.targets[].observability.enabled=true for an MK8s target"
                )
        if grafana_app_id in selected and "mk8s" not in _enabled_component_ids(
            payload, scope="infra"
        ):
            issues.append(f"apps:{grafana_app_id} requires one enabled infra:mk8s component")
        if grafana_required:
            if grafana_app_id not in available_ids:
                issues.append(
                    "components.infra.mk8s.cli.observability.grafana.chart_component_id references "
                    f"unknown or unavailable apps component '{grafana_app_id}'"
                )
            elif grafana_app_id not in selected:
                selected.add(grafana_app_id)
                auto_enabled.append(grafana_app_id)

    if grafana_gateway_app_id and grafana_gateway_required:
        if grafana_gateway_app_id not in available_ids:
            issues.append(
                "components.infra.mk8s.cli.observability.grafana.gateway_chart_component_id "
                f"references unknown or unavailable apps component '{grafana_gateway_app_id}'"
            )
        elif grafana_gateway_app_id not in selected:
            selected.add(grafana_gateway_app_id)
            auto_enabled.append(grafana_gateway_app_id)

    return ObservabilityAppSelection(
        selected_app_ids=tuple(sorted(selected)),
        auto_enabled_app_ids=tuple(sorted(auto_enabled)),
        issues=tuple(issues),
        observability_enabled=observability_enabled,
        kubernetes_agent_required=kubernetes_agent_required,
        collector_app_id=collector_app_id,
        vm_monitoring_agent_enabled=vm_monitoring_agent_enabled,
        grafana_app_id=grafana_app_id,
        grafana_required=grafana_required,
        grafana_gateway_app_id=grafana_gateway_app_id,
        grafana_gateway_required=grafana_gateway_required,
    )


def observability_dependency_issues(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> list[str]:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    selected = _enabled_component_ids(payload, scope="apps")
    resolution = resolve_observability_app_selection(
        payload,
        selected_app_ids=selected,
        app_entries=app_entries,
        cli_settings=cli_settings,
    )
    issues = list(resolution.issues)
    expected = set(
        resolve_observability_app_selection(
            payload,
            selected_app_ids=set(),
            app_entries=app_entries,
            cli_settings=cli_settings,
        ).selected_app_ids
    )
    missing = sorted(expected - selected)
    for app_id in missing:
        issues.append(
            f"Observability-enabled MK8s deployment requires 'apps:{app_id}' to be enabled"
        )
    return issues


def ensure_observability_app_rows(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> bool:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    if not payload:
        return False
    entries = app_entries if app_entries is not None else component_entries("apps")
    selected = _enabled_component_ids(payload, scope="apps")
    resolution = resolve_observability_app_selection(
        payload,
        selected_app_ids=selected,
        app_entries=entries,
        cli_settings=cli_settings,
    )
    if resolution.issues:
        return False

    entry_by_id = {entry.id: entry for entry in entries}
    charts = _app_chart_rows(payload)
    changed = False
    collector_app_id = _collector_app_id()
    grafana_app_id = _grafana_app_id()
    grafana_gateway_app_id = _grafana_gateway_app_id()
    target_refs = enabled_cluster_target_refs(payload)
    app_ids_to_ensure = set(resolution.auto_enabled_app_ids)
    if collector_app_id and _kubernetes_agent_required(payload):
        app_ids_to_ensure.add(collector_app_id)
    if grafana_app_id and _grafana_required(payload):
        app_ids_to_ensure.add(grafana_app_id)
    if grafana_gateway_app_id and _grafana_required(payload):
        app_ids_to_ensure.add(grafana_gateway_app_id)
    if not app_ids_to_ensure:
        return False
    for app_id in sorted(app_ids_to_ensure):
        entry = entry_by_id.get(app_id)
        if entry is None:
            continue
        if len(target_refs) > 1 and app_id in _observability_target_scoped_app_ids():
            required_target_refs = _required_observability_app_target_refs(payload, app_id)
            remaining_rows = _app_chart_rows_for_id(payload, app_id)
            rows_by_target = {
                app_chart_target_ref(row): row
                for row in remaining_rows
                if app_chart_target_ref(row)
            }
            for target_ref in required_target_refs:
                existing = rows_by_target.get(target_ref)
                if existing is None:
                    row = _new_observability_app_row(entry)
                    row["instance_id"] = _observability_instance_id(app_id, target_ref=target_ref)
                    row[TARGET_REF_FIELD] = target_ref
                    charts.append(row)
                    changed = True
                    continue
                before = copy.deepcopy(existing)
                existing["enabled"] = True
                if not str(existing.get("id", "")).strip():
                    existing["id"] = entry.id
                _apply_observability_app_entry_defaults(row=existing, entry=entry)
                if app_chart_target_ref(existing) != target_ref:
                    existing[TARGET_REF_FIELD] = target_ref
                _normalize_target_scoped_observability_instance_id(
                    existing,
                    app_id=app_id,
                    target_ref=target_ref,
                )
                if existing != before:
                    changed = True
            continue
        existing = _app_chart_row(payload, app_id)
        if existing is None:
            row = _new_observability_app_row(entry)
            if _apply_observability_target_ref(payload=payload, row=row):
                target_ref = app_chart_target_ref(row)
                if target_ref:
                    row["instance_id"] = _observability_instance_id(
                        app_id,
                        target_ref=target_ref,
                    )
            charts.append(row)
            changed = True
            continue
        before = copy.deepcopy(existing)
        previous_target_ref = app_chart_target_ref(existing)
        existing["enabled"] = True
        if not str(existing.get("id", "")).strip():
            existing["id"] = entry.id
        _apply_observability_app_entry_defaults(row=existing, entry=entry)
        _apply_observability_target_ref(payload=payload, row=existing)
        if app_chart_target_ref(existing):
            if not previous_target_ref:
                existing["instance_id"] = _observability_instance_id(
                    app_id,
                    target_ref=app_chart_target_ref(existing),
                )
            else:
                _normalize_target_scoped_observability_instance_id(
                    existing,
                    app_id=app_id,
                    target_ref=app_chart_target_ref(existing),
                )
        elif not str(existing.get("instance_id", "")).strip():
            existing["instance_id"] = entry.id
        if existing != before:
            changed = True
    return changed


def _path_segments(path: str) -> tuple[str, ...]:
    segments: list[str] = []
    current: list[str] = []
    escaped = False
    for char in str(path):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ".":
            segment = "".join(current)
            if segment:
                segments.append(segment)
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    segment = "".join(current)
    if segment:
        segments.append(segment)
    return tuple(segments)


def _set_path_value(node: dict[str, Any], path: str, value: Any) -> None:
    segments = _path_segments(path)
    if not segments:
        return
    current: dict[str, Any] = node
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = value


def _delete_path_value(node: dict[str, Any], path: str) -> None:
    segments = _path_segments(path)
    if not segments:
        return
    current: Any = node
    parents: list[tuple[dict[str, Any], str]] = []
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return
        parents.append((current, segment))
        current = current[segment]
    if not isinstance(current, dict) or segments[-1] not in current:
        return
    del current[segments[-1]]
    for parent, segment in reversed(parents):
        child = parent.get(segment)
        if isinstance(child, dict) and not child:
            del parent[segment]
            continue
        break


_COLLECTOR_MANAGED_VALUE_PATHS = (
    "values.config.logs.enabled",
    "values.config.logs.collectAgentLogs",
    "values.config.logs.excludedNamespaces",
    "values.config.metrics.enabled",
    "values.config.metrics.collectAgentMetrics",
    "values.config.metrics.collectK8sClusterMetrics",
    "values.config.metrics.excludedNamespaces",
    "values.config.traces.enabled",
)
_CXCLI_CLUSTER_METRIC_JOB_NAMES = {
    "cxcli-kubernetes-apiservers",
    "cxcli-kubernetes-nodes",
    "cxcli-kubernetes-nodes-cadvisor",
    "cxcli-hubble",
}
_SOPERATOR_OWNED_OBSERVABILITY_NAMESPACES = (
    "soperator",
    "soperator-system",
    "monitoring-system",
    "logs-system",
)
_GRAFANA_MANAGED_VALUE_PATHS = (
    "values.admin",
    "values.dashboardProviders",
    "values.datasources",
    "values.envValueFrom",
    "values.grafana\\.ini.auth",
    "values.service.type",
    "values.route.main",
    "values.extraObjects",
)


def _strip_grafana_source_dashboard_values(row: dict[str, Any]) -> bool:
    if _as_text(row.get("id")) != _grafana_app_id():
        return False
    values = row.get("values")
    if not isinstance(values, dict):
        return False
    changed = False
    for key in ("dashboards", "dashboardsConfigMaps"):
        if key in values:
            values.pop(key, None)
            changed = True
    return changed


def _grafana_catalog_managed_values() -> dict[str, Any]:
    grafana_app_id = _grafana_app_id()
    if not grafana_app_id:
        return {}
    chart = helm_chart_source_by_id(grafana_app_id, sources=_catalog_sources())
    if chart is None:
        return {}
    managed: dict[str, Any] = {}
    managed_paths = set(_GRAFANA_MANAGED_VALUE_PATHS)
    for default in chart.defaults:
        target_path = _as_text(default.target_path)
        if target_path in managed_paths or any(
            target_path.startswith(f"{path}.") for path in managed_paths
        ):
            managed[target_path] = copy.deepcopy(default.value)
    return managed


def _collector_managed_values(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> dict[str, Any]:
    settings = _effective_kubernetes_observability_config(payload, target_ref=target_ref)
    # cxcli renders its own cluster-metrics scrape jobs below. The upstream chart
    # maps every node label into metric labels for kubelet/cAdvisor jobs, which
    # is fragile on NFD-labeled GPU clusters and can make the read endpoint reject
    # otherwise valid container metrics.
    chart_collect_k8s_cluster_metrics = False
    logs_excluded = list(settings.logs_excluded_namespaces)
    metrics_excluded = list(settings.metrics_excluded_namespaces)
    target_refs = enabled_cluster_target_refs(payload)
    soperator_for_target = any(
        isinstance(row, Mapping)
        and component_type_id(row) == "soperator"
        and bool(row.get("enabled", False))
        and (
            app_chart_target_ref(row) == target_ref
            or (
                not app_chart_target_ref(row)
                and len(target_refs) == 1
                and (not target_ref or target_ref in target_refs)
            )
        )
        for row in _app_chart_rows(payload)
    )
    if soperator_for_target:
        for namespace in _SOPERATOR_OWNED_OBSERVABILITY_NAMESPACES:
            if namespace not in logs_excluded:
                logs_excluded.append(namespace)
            if namespace not in metrics_excluded:
                metrics_excluded.append(namespace)
    return {
        "values.config.logs.enabled": settings.logs_enabled,
        "values.config.logs.collectAgentLogs": settings.logs_collect_agent_logs,
        "values.config.logs.excludedNamespaces": logs_excluded,
        "values.config.metrics.enabled": settings.metrics_enabled,
        "values.config.metrics.collectAgentMetrics": settings.metrics_collect_agent_metrics,
        "values.config.metrics.collectK8sClusterMetrics": chart_collect_k8s_cluster_metrics,
        "values.config.metrics.excludedNamespaces": metrics_excluded,
        "values.config.traces.enabled": settings.traces_enabled,
    }


def _grafana_authorized_datasource(
    *,
    name: str,
    uid: str,
    datasource_type: str,
    url: str,
    token_env: str,
    is_default: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "uid": uid,
        "type": datasource_type,
        "access": "proxy",
        "url": url,
        "isDefault": is_default,
        "editable": True,
        "jsonData": {
            "httpHeaderName1": "Authorization",
        },
        "secureJsonData": {
            "httpHeaderValue1": f"Bearer ${{{token_env}}}",
        },
    }


def _grafana_managed_values(
    payload: dict[str, Any],
) -> dict[str, Any]:
    managed = _grafana_catalog_managed_values()
    grafana_settings = _grafana_cli_settings()
    if grafana_settings.admin_secret != GrafanaCliSettings().admin_secret:
        managed["values.admin"] = {
            "existingSecret": grafana_settings.admin_secret.secret_name,
            "userKey": grafana_settings.admin_secret.user_key,
            "passwordKey": grafana_settings.admin_secret.password_key,
        }
    if grafana_settings.read_token != GrafanaCliSettings().read_token:
        managed["values.envValueFrom"] = {
            grafana_settings.read_token.env: {
                "secretKeyRef": {
                    "name": grafana_settings.read_token.secret_name,
                    "key": grafana_settings.read_token.key,
                }
            }
        }
    endpoints = observability_endpoint_summary(payload)
    read_endpoints = _mapping(endpoints.get("read"))
    datasources: list[dict[str, Any]] = []
    for datasource in grafana_settings.datasources:
        url = _as_text(read_endpoints.get(datasource.read_endpoint))
        if not url:
            continue
        datasources.append(
            _grafana_authorized_datasource(
                name=datasource.name,
                uid=datasource.uid,
                datasource_type=datasource.datasource_type,
                url=url,
                token_env=grafana_settings.read_token.env,
                is_default=datasource.is_default,
            )
        )
    managed["values.datasources"] = {
        "datasources.yaml": {
            "apiVersion": 1,
            "datasources": datasources,
        }
    }
    logout_timeout = grafana_settings.logout_timeout
    if logout_timeout:
        managed["values.grafana\\.ini.auth.login_maximum_inactive_lifetime_duration"] = (
            logout_timeout
        )
    return managed


def _additional_target_config(
    *,
    namespace: str,
    target: ObservabilityMetricTarget,
) -> dict[str, Any]:
    relabel_configs: list[dict[str, Any]] = [
        {
            "source_labels": ["__meta_kubernetes_namespace"],
            "action": "keep",
            "regex": namespace,
        },
        {
            "source_labels": ["__meta_kubernetes_service_name"],
            "action": "keep",
            "regex": target.service_name,
        },
    ]
    if target.port is not None:
        relabel_configs.append(
            {
                "source_labels": ["__meta_kubernetes_endpoint_port_number"],
                "action": "keep",
                "regex": str(target.port),
            }
        )
    return {
        "job_name": target.job_name,
        "kubernetes_sd_configs": [{"role": "endpoints"}],
        "relabel_configs": relabel_configs,
    }


def _safe_node_label_relabel_configs() -> list[dict[str, Any]]:
    return [
        {
            "source_labels": ["__meta_kubernetes_node_name"],
            "target_label": "node",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_kubernetes_io_hostname"],
            "target_label": "kubernetes_io_hostname",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_kubernetes_io_arch"],
            "target_label": "kubernetes_io_arch",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_kubernetes_io_os"],
            "target_label": "kubernetes_io_os",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_node_kubernetes_io_instance_type"],
            "target_label": "node_kubernetes_io_instance_type",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_topology_kubernetes_io_region"],
            "target_label": "topology_kubernetes_io_region",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_nebius_com_node_group_id"],
            "target_label": "nebius_com_node_group_id",
        },
        {
            "source_labels": ["__meta_kubernetes_node_label_nebius_com_resource_preset"],
            "target_label": "nebius_com_resource_preset",
        },
    ]


def _kubelet_proxy_target(
    *,
    job_name: str,
    metrics_path: str,
) -> dict[str, Any]:
    return {
        "job_name": job_name,
        "bearer_token_file": "/var/run/secrets/kubernetes.io/serviceaccount/token",
        "kubernetes_sd_configs": [{"role": "node"}],
        "relabel_configs": [
            {
                "source_labels": ["__meta_kubernetes_node_name"],
                "action": "keep",
                "regex": "${env:K8S_NODE_NAME}",
            },
            *_safe_node_label_relabel_configs(),
            {
                "replacement": "kubernetes.default.svc:443",
                "target_label": "__address__",
            },
            {
                "source_labels": ["__meta_kubernetes_node_name"],
                "regex": "(.+)",
                "replacement": f"/api/v1/nodes/$$$1/proxy/{metrics_path}",
                "target_label": "__metrics_path__",
            },
        ],
        "scheme": "https",
        "tls_config": {
            "ca_file": "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        },
    }


def _cluster_metric_targets() -> tuple[dict[str, Any], ...]:
    return (
        {
            "job_name": "cxcli-kubernetes-apiservers",
            "bearer_token_file": "/var/run/secrets/kubernetes.io/serviceaccount/token",
            "kubernetes_sd_configs": [{"role": "endpoints"}],
            "relabel_configs": [
                {
                    "source_labels": [
                        "__meta_kubernetes_namespace",
                        "__meta_kubernetes_service_name",
                        "__meta_kubernetes_endpoint_port_name",
                    ],
                    "action": "keep",
                    "regex": "default;kubernetes;https",
                }
            ],
            "scheme": "https",
            "tls_config": {
                "ca_file": "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
                "server_name": "kubernetes.default.svc",
            },
        },
        _kubelet_proxy_target(
            job_name="cxcli-kubernetes-nodes",
            metrics_path="metrics",
        ),
        _kubelet_proxy_target(
            job_name="cxcli-kubernetes-nodes-cadvisor",
            metrics_path="metrics/cadvisor",
        ),
        {
            "job_name": "cxcli-hubble",
            "kubernetes_sd_configs": [
                {
                    "role": "endpoints",
                    "namespaces": {"names": ["kube-system"]},
                }
            ],
            "relabel_configs": [
                {
                    "source_labels": ["__meta_kubernetes_service_label_k8s_app"],
                    "regex": "hubble",
                    "action": "keep",
                },
                {
                    "source_labels": ["__meta_kubernetes_endpoint_port_name"],
                    "regex": "hubble-metrics",
                    "action": "keep",
                },
                {
                    "source_labels": ["__meta_kubernetes_pod_node_name"],
                    "action": "keep",
                    "regex": "${env:K8S_NODE_NAME}",
                },
                {
                    "source_labels": ["__meta_kubernetes_pod_node_name"],
                    "regex": "(.+)",
                    "replacement": "$$$1",
                    "target_label": "node",
                },
                {
                    "source_labels": ["__meta_kubernetes_service_name"],
                    "target_label": "service",
                },
                {
                    "source_labels": ["__meta_kubernetes_namespace"],
                    "target_label": "namespace",
                },
            ],
            "metrics_path": "/metrics",
            "scheme": "http",
            "honor_labels": True,
            "scrape_interval": "15s",
        },
    )


def _collector_additional_targets(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> tuple[dict[str, Any], ...]:
    resolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    settings = _effective_kubernetes_observability_config(payload, target_ref=target_ref)
    if settings.metrics_enabled and settings.metrics_collect_k8s_cluster_metrics:
        resolved.extend(copy.deepcopy(item) for item in _cluster_metric_targets())
    for app_row, target in _selected_app_metric_targets(payload):
        if target.discovery != "additional_target":
            continue
        app_target_ref = app_chart_target_ref(app_row)
        if target_ref and app_target_ref != target_ref:
            continue
        namespace = _as_text(_mapping(app_row).get("namespace"))
        if not namespace:
            app_id = component_type_id(app_row)
            chart = helm_chart_source_by_id(app_id, sources=_catalog_sources())
            namespace = _as_text(getattr(chart, "namespace", "")) or app_id
        key = (app_target_ref, namespace, target.job_name)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(_additional_target_config(namespace=namespace, target=target))
    return tuple(resolved)


def _catalog_metric_target_job_names() -> set[str]:
    names = {
        target.job_name
        for settings in _app_observability_settings().values()
        for target in settings.metric_targets
        if target.job_name
    }
    names.update(_CXCLI_CLUSTER_METRIC_JOB_NAMES)
    return names


def _merge_managed_additional_targets(
    *,
    chart_row: dict[str, Any],
    managed_targets: tuple[dict[str, Any], ...],
    managed_job_names: set[str] | None = None,
) -> None:
    values = chart_row.get("values")
    if not isinstance(values, dict):
        values = {}
        chart_row["values"] = values
    metrics = _mapping(_nested_path_value(values, "config.metrics"))
    existing_raw = metrics.get("additionalTargets")
    existing_targets = existing_raw if isinstance(existing_raw, list) else []
    owned_job_names = set(managed_job_names or ())
    owned_job_names.update(
        _as_text(item.get("job_name")) for item in managed_targets if _as_text(item.get("job_name"))
    )
    preserved = [
        item
        for item in existing_targets
        if isinstance(item, Mapping) and _as_text(item.get("job_name")) not in owned_job_names
    ]
    merged = preserved + [copy.deepcopy(item) for item in managed_targets]
    if merged:
        _set_path_value(chart_row, "values.config.metrics.additionalTargets", merged)
        return
    _delete_path_value(chart_row, "values.config.metrics.additionalTargets")


def materialize_observability_infra_values(payload_or_config: Any) -> bool:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    if not payload:
        return False

    changed = False
    vm_settings = _effective_vm_observability_config(payload)
    managed_label_values = _catalog_observability_gpu_node_label_values()
    for row in _infra_component_rows(payload):
        if not isinstance(row, dict):
            continue
        component_id = component_type_id(row)
        if component_id == "mk8s":
            row_enabled = bool(row.get("enabled", False))
            active_labels = _observability_gpu_node_labels_for_row(payload, row)
            should_apply = bool(active_labels and row_enabled and _mk8s_gpu_nodes_enabled(row))
            inputs, overrides, labels = _gpu_node_group_label_maps(row, create=should_apply)

            if should_apply:
                assert labels is not None
                for key, value in active_labels.items():
                    if labels.get(key) != value:
                        labels[key] = value
                        changed = True
                continue

            if labels is None:
                continue
            for key, values in sorted(managed_label_values.items()):
                if key in labels and _as_text(labels.get(key)) in values:
                    del labels[key]
                    changed = True
            _prune_empty_gpu_node_label_maps(inputs=inputs, overrides=overrides, labels=labels)
            continue
        if component_id != "vm":
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
            row["inputs"] = inputs
        labels = inputs.get("labels")
        if not isinstance(labels, dict):
            if _vm_journald_logs_enabled(payload, vm_settings=vm_settings):
                labels = {}
                inputs["labels"] = labels
            else:
                labels = {}
        built_in_logs_enabled = _vm_journald_logs_enabled(
            payload, vm_settings=vm_settings
        ) and bool(row.get("enabled", False))
        if built_in_logs_enabled:
            if labels.get(_VM_JOURNALD_LOGS_ENABLED_LABEL) != "true":
                labels[_VM_JOURNALD_LOGS_ENABLED_LABEL] = "true"
                changed = True
            rendered_units = ";".join(vm_settings.logs_systemd_units)
            if rendered_units:
                if labels.get(_VM_JOURNALD_LOGS_UNITS_LABEL) != rendered_units:
                    labels[_VM_JOURNALD_LOGS_UNITS_LABEL] = rendered_units
                    changed = True
            elif _VM_JOURNALD_LOGS_UNITS_LABEL in labels:
                del labels[_VM_JOURNALD_LOGS_UNITS_LABEL]
                changed = True
        else:
            if _VM_JOURNALD_LOGS_ENABLED_LABEL in labels:
                del labels[_VM_JOURNALD_LOGS_ENABLED_LABEL]
                changed = True
            if _VM_JOURNALD_LOGS_UNITS_LABEL in labels:
                del labels[_VM_JOURNALD_LOGS_UNITS_LABEL]
                changed = True
            if not labels:
                inputs.pop("labels", None)
    return changed


def materialize_observability_app_values(payload_or_config: Any) -> bool:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    collector_app_id = _collector_app_id()
    grafana_app_id = _grafana_app_id()
    if not collector_app_id and not grafana_app_id:
        return False
    collector_rows = (
        [
            row
            for row in _app_chart_rows_for_id(payload, collector_app_id)
            if isinstance(row, dict) and bool(row.get("enabled", False))
        ]
        if collector_app_id
        else []
    )
    grafana_rows = (
        [
            row
            for row in _app_chart_rows_for_id(payload, grafana_app_id)
            if isinstance(row, dict) and bool(row.get("enabled", False))
        ]
        if grafana_app_id
        else []
    )
    if not collector_rows and not grafana_rows:
        return False

    changed = False
    catalog_metric_target_job_names = _catalog_metric_target_job_names()
    for chart_row in collector_rows:
        before = copy.deepcopy(chart_row)
        target_ref = app_chart_target_ref(chart_row)
        if _kubernetes_agent_required(payload, target_ref=target_ref):
            for target_path, target_value in _collector_managed_values(
                payload,
                target_ref=target_ref,
            ).items():
                _set_path_value(chart_row, target_path, copy.deepcopy(target_value))
            metrics_enabled = _effective_kubernetes_observability_config(
                payload,
                target_ref=target_ref,
            ).metrics_enabled
            managed_targets = (
                _collector_additional_targets(
                    payload,
                    target_ref=target_ref,
                )
                if metrics_enabled
                else ()
            )
            _merge_managed_additional_targets(
                chart_row=chart_row,
                managed_targets=managed_targets,
                managed_job_names=catalog_metric_target_job_names,
            )
            if chart_row != before:
                changed = True
            continue

        for stale_path in sorted(
            _COLLECTOR_MANAGED_VALUE_PATHS, key=lambda item: item.count("."), reverse=True
        ):
            _delete_path_value(chart_row, stale_path)
        _merge_managed_additional_targets(
            chart_row=chart_row,
            managed_targets=(),
            managed_job_names=catalog_metric_target_job_names,
        )
        if chart_row != before:
            changed = True
    for chart_row in grafana_rows:
        before = copy.deepcopy(chart_row)
        target_ref = app_chart_target_ref(chart_row)
        if _grafana_required(payload, target_ref=target_ref):
            for target_path, target_value in _grafana_managed_values(payload).items():
                _set_path_value(chart_row, target_path, copy.deepcopy(target_value))
            _strip_grafana_source_dashboard_values(chart_row)
            if chart_row != before:
                changed = True
            continue
        for stale_path in sorted(
            _GRAFANA_MANAGED_VALUE_PATHS, key=lambda item: item.count("."), reverse=True
        ):
            _delete_path_value(chart_row, stale_path)
        if chart_row != before:
            changed = True
    return changed


def _strip_observability_collector_generated_values(
    chart_row: dict[str, Any],
    *,
    managed_job_names: set[str],
) -> bool:
    before = copy.deepcopy(chart_row)
    for stale_path in sorted(
        _COLLECTOR_MANAGED_VALUE_PATHS, key=lambda item: item.count("."), reverse=True
    ):
        _delete_path_value(chart_row, stale_path)

    values = chart_row.get("values")
    metrics = _nested_path_value(values, "config.metrics") if isinstance(values, dict) else None
    additional_targets = metrics.get("additionalTargets") if isinstance(metrics, dict) else None
    if isinstance(additional_targets, list):
        preserved = [
            item
            for item in additional_targets
            if not (
                isinstance(item, Mapping) and _as_text(item.get("job_name")) in managed_job_names
            )
        ]
        if preserved:
            metrics["additionalTargets"] = preserved
        else:
            _delete_path_value(chart_row, "values.config.metrics.additionalTargets")

    if "values" not in chart_row:
        chart_row["values"] = {}

    return chart_row != before


def strip_observability_generated_app_values(payload_or_config: Any) -> bool:
    """Remove cxcli-generated observability agent values from source config."""
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    collector_app_id = _collector_app_id()
    if not collector_app_id:
        return False

    changed = False
    managed_job_names = _catalog_metric_target_job_names()
    for chart_row in _app_chart_rows_for_id(payload, collector_app_id):
        if not isinstance(chart_row, dict):
            continue
        if _strip_observability_collector_generated_values(
            chart_row,
            managed_job_names=managed_job_names,
        ):
            changed = True
    return changed


def observability_endpoint_summary(
    payload_or_config: Any,
    *,
    project_id: str | None = None,
    region_id: str | None = None,
) -> dict[str, Any]:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    resolved_project_id = _as_text(project_id) or _payload_project_id(payload)
    resolved_region_id = _as_text(region_id) or _payload_region_id(payload)
    configured = bool(resolved_project_id and resolved_region_id)
    kubernetes_settings_by_target = tuple(
        _effective_kubernetes_observability_config(payload, target_ref=target_ref)
        for target_ref in enabled_cluster_target_refs(payload)
        if _observability_enabled(payload, target_ref=target_ref)
    )
    if not kubernetes_settings_by_target and _observability_enabled(payload):
        kubernetes_settings_by_target = (_effective_kubernetes_observability_config(payload),)
    vm_settings = _effective_vm_observability_config(payload)
    observability_enabled = _observability_enabled(payload)
    enabled_infra = _enabled_component_ids(payload, scope="infra")
    kubernetes_enabled = bool(observability_enabled and "mk8s" in enabled_infra)
    kubernetes_logs_enabled = bool(
        kubernetes_enabled and any(item.logs_enabled for item in kubernetes_settings_by_target)
    )
    kubernetes_metrics_enabled = bool(
        kubernetes_enabled and any(item.metrics_enabled for item in kubernetes_settings_by_target)
    )
    kubernetes_traces_enabled = bool(
        kubernetes_enabled and any(item.traces_enabled for item in kubernetes_settings_by_target)
    )
    vm_service_metrics_enabled = _vm_monitoring_agent_enabled(payload)
    vm_logs_enabled = _vm_journald_logs_enabled(payload, vm_settings=vm_settings)
    service_metric_buckets = _observability_service_buckets(payload, signal="metrics")
    service_log_buckets = _observability_service_buckets(payload, signal="logs")
    service_metrics_enabled = bool(service_metric_buckets)
    service_logs_enabled = bool(service_log_buckets)
    metrics_enabled = bool(
        kubernetes_metrics_enabled or vm_service_metrics_enabled or service_metrics_enabled
    )
    signals = {
        "logs": bool(kubernetes_logs_enabled or vm_logs_enabled or service_logs_enabled),
        "metrics": metrics_enabled,
        "traces": kubernetes_traces_enabled,
        "service_metrics": service_metrics_enabled,
        "service_logs": service_logs_enabled,
        "kubernetes_logs": kubernetes_logs_enabled,
        "kubernetes_metrics": kubernetes_metrics_enabled,
        "kubernetes_traces": kubernetes_traces_enabled,
        "vm_service_metrics": vm_service_metrics_enabled,
        "vm_logs": vm_logs_enabled,
    }
    read_templates, write_templates = _global_endpoint_templates()
    if not configured:
        return {
            "configured": False,
            "reason": "project_id_and_region_id_required",
            "signals": signals,
            "read": {},
            "read_metadata": {},
            "write": {},
            "write_metadata": {},
            "auth": {},
            "service_provider_metric_buckets": list(service_metric_buckets),
            "service_log_buckets": list(service_log_buckets),
        }

    read_endpoints, read_metadata = _render_observability_endpoint_group(
        read_templates,
        project_id=resolved_project_id,
        region_id=resolved_region_id,
        signals=signals,
    )
    write_endpoints, write_metadata = _render_observability_endpoint_group(
        write_templates,
        project_id=resolved_project_id,
        region_id=resolved_region_id,
        signals=signals,
    )

    return {
        "configured": True,
        "project_id": resolved_project_id,
        "region": resolved_region_id,
        "signals": signals,
        "read": read_endpoints,
        "read_metadata": read_metadata,
        "write": write_endpoints,
        "write_metadata": write_metadata,
        "auth": {
            "read": (
                "Authorization: Bearer <observability static token or IAM token>; "
                "viewer access is enough for read."
            ),
            "write": (
                "Nebius-managed MK8s and built-in VM Monitoring agents use platform-managed auth. "
                "External collectors use Authorization: Bearer <observability static token or "
                "IAM token>; editor or signal-specific writer access is required for ingest."
            ),
        },
        "service_provider_metric_buckets": list(service_metric_buckets),
        "service_log_buckets": list(service_log_buckets),
    }


def observability_status_summary(
    payload_or_config: Any,
) -> dict[str, Any]:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    selection = resolve_observability_app_selection(
        payload,
        selected_app_ids=_enabled_component_ids(payload, scope="apps"),
        app_entries=component_entries("apps"),
    )
    selected_metric_targets = _selected_app_metric_targets(payload)
    kubernetes_metrics_enabled = any(
        _effective_kubernetes_observability_config(payload, target_ref=target_ref).metrics_enabled
        for target_ref in enabled_cluster_target_refs(payload)
        if _observability_enabled(payload, target_ref=target_ref)
    )
    if not kubernetes_metrics_enabled and _observability_enabled(payload):
        kubernetes_metrics_enabled = _effective_kubernetes_observability_config(
            payload
        ).metrics_enabled
    vm_logs_enabled = _vm_journald_logs_enabled(payload)
    collector_enabled = bool(
        selection.collector_app_id
        and selection.collector_app_id in _enabled_component_ids(payload, scope="apps")
    )
    grafana_enabled = bool(
        selection.grafana_app_id
        and selection.grafana_app_id in _enabled_component_ids(payload, scope="apps")
    )
    dcgm_metric_source = next(
        (
            target.discovery
            for _app_id, target in selected_metric_targets
            if target.service_name == "nvidia-dcgm-exporter"
        ),
        "",
    )
    dcgm_metric_source_configured = bool(
        selection.observability_enabled
        and collector_enabled
        and kubernetes_metrics_enabled
        and dcgm_metric_source
    )
    dcgm_node_policy_managed = bool(
        dcgm_metric_source_configured
        and observability_gpu_node_label_reconciliation(payload).enabled
    )
    service_metric_buckets = _observability_service_buckets(payload, signal="metrics")
    service_log_buckets = _observability_service_buckets(payload, signal="logs")
    return {
        "enabled": selection.observability_enabled,
        "kubernetes_agent": collector_enabled,
        "grafana": grafana_enabled,
        "vm_monitoring_agent": selection.vm_monitoring_agent_enabled,
        "vm_journald_logs": vm_logs_enabled,
        "service_provider_metric_buckets": list(service_metric_buckets),
        "service_log_buckets": list(service_log_buckets),
        "gpu_dcgm_metric_source": dcgm_metric_source
        if dcgm_metric_source_configured
        else "disabled",
        "gpu_dcgm_node_policy": (
            "managed_gpu_operator_dcgm_labels" if dcgm_node_policy_managed else "not_managed"
        ),
        "gpu_dcgm_live_readiness": (
            "verify_live_nvidia_dcgm_exporter_endpoints_after_deploy"
            if dcgm_metric_source_configured
            else "not_configured"
        ),
    }


def observability_validation_specs(payload_or_config: Any) -> list[dict[str, Any]]:
    """Build source-enabled deploy validations for observability-enabled MK8s targets."""
    payload = _as_payload(payload_or_config)
    mk8s_observability = _mk8s_observability_settings()
    collector_app_id = (
        mk8s_observability.chart_component_id
        if mk8s_observability.mode == "kubernetes_agent"
        else ""
    )
    if not payload or not collector_app_id:
        return []
    validation = mk8s_observability.validation
    if not validation.enabled:
        return []
    target_refs = enabled_cluster_target_refs(payload)
    if not target_refs:
        return []
    collector_source = helm_chart_source_by_id(collector_app_id, sources=_catalog_sources())
    if collector_source is None:
        raise ValueError(
            "components.infra.mk8s.cli.observability.primary_agent.chart_component_id "
            f"references unknown apps component '{collector_app_id}'"
        )
    collector_rows = [
        row
        for row in _app_chart_rows_for_id(payload, collector_app_id)
        if isinstance(row, dict) and bool(row.get("enabled", False))
    ]
    validations: list[dict[str, Any]] = []
    for target_ref in target_refs:
        if not _kubernetes_agent_required(payload, target_ref=target_ref):
            continue
        matching_row = next(
            (
                row
                for row in collector_rows
                if app_chart_target_ref(row) == target_ref
                or (not app_chart_target_ref(row) and len(target_refs) == 1)
            ),
            {},
        )
        namespace = _as_text(matching_row.get("namespace"))
        release_name = _as_text(matching_row.get("release-name"))
        namespace = namespace or _as_text(collector_source.namespace)
        release_name = (
            release_name
            or _as_text(collector_source.release_name)
            or _as_text(collector_source.name)
        )
        if not namespace:
            raise ValueError(
                f"apps:{collector_app_id} must declare release.namespace in component_sources.yaml "
                "for observability validation"
            )
        if not release_name:
            raise ValueError(
                f"apps:{collector_app_id} must declare a chart name in component_sources.yaml "
                "for observability validation"
            )
        settings = _effective_kubernetes_observability_config(payload, target_ref=target_ref)
        validations.append(
            {
                "kind": OBSERVABILITY_INGESTION_VALIDATION_KIND,
                "target_ref": target_ref,
                "name": _target_scoped_validation_name(
                    "Observability ingestion",
                    target_ref=target_ref,
                ),
                "namespace": namespace,
                "helmrelease_name": release_name,
                "helmrelease_ready_condition": validation.helmrelease_ready_condition,
                "signal_value_paths": dict(validation.signal_value_paths),
                "cluster_metric_targets_path": validation.cluster_metric_targets_path,
                "daemonset_name": validation.daemonset_name,
                "pod_selector": validation.pod_selector,
                "pod_failure_sample_limit": validation.pod_failure_sample_limit,
                "trace_otlp_service": {
                    "name": validation.trace_otlp_service.name,
                    "port": validation.trace_otlp_service.port,
                    "endpoint_slice_selector": validation.trace_otlp_service.endpoint_slice_selector,
                    "endpoint_slice_check_limit": (
                        validation.trace_otlp_service.endpoint_slice_check_limit
                    ),
                },
                "signals": {
                    "logs": settings.logs_enabled,
                    "metrics": settings.metrics_enabled,
                    "traces": settings.traces_enabled,
                    "collect_k8s_cluster_metrics": settings.metrics_collect_k8s_cluster_metrics,
                },
                "report_file": _target_scoped_report_file(
                    "observability-ingestion-report.json",
                    target_ref=target_ref,
                ),
            }
        )
    return validations


__all__ = [
    "KubernetesObservabilityConfig",
    "VmObservabilityConfig",
    "ObservabilityAppSelection",
    "ObservabilityGpuNodeLabelReconciliation",
    "ensure_observability_app_rows",
    "materialize_observability_app_values",
    "materialize_observability_infra_values",
    "observability_endpoint_summary",
    "observability_gpu_node_label_reconciliation",
    "normalize_observability_project_settings",
    "observability_dependency_issues",
    "observability_project_defaults",
    "observability_status_summary",
    "observability_validation_specs",
    "resolve_observability_app_selection",
    "strip_observability_generated_app_values",
]
