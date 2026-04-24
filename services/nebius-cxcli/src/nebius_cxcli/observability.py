"""Observability policy helpers and chart materialization."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .component_sources import (
    AppObservabilitySettings,
    InfraObservabilitySettings,
    ObservabilityEndpointTemplates,
    ObservabilityMetricTarget,
    helm_chart_source_by_id,
    load_component_sources,
    tf_module_source_by_id,
)
from .components import ComponentEntry, component_entries
from .deploy_targets import TARGET_REF_FIELD, app_chart_target_ref, enabled_cluster_target_refs
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
class VmStandaloneCollectorConfig:
    enabled: bool
    metrics_enabled: bool
    logs_enabled: bool
    logs_systemd_units: tuple[str, ...]
    package_version: str
    iam_token_file: str
    metrics_export_port: int
    prometheus_agent_port: int


@dataclass(frozen=True)
class ObservabilityAppSelection:
    selected_app_ids: tuple[str, ...]
    auto_enabled_app_ids: tuple[str, ...]
    issues: tuple[str, ...]
    observability_enabled: bool
    kubernetes_agent_required: bool
    collector_app_id: str
    vm_monitoring_agent_enabled: bool


@dataclass(frozen=True)
class ObservabilityGpuNodeLabelReconciliation:
    enabled: bool
    selector: tuple[tuple[str, str], ...] = ()
    labels: tuple[tuple[str, str], ...] = ()


_DEFAULT_OBSERVABILITY_ENDPOINTS = ObservabilityEndpointTemplates(
    metrics_otlp_write=(
        "https://write.monitoring.{region}.nebius.cloud/projects/"
        "{project_id}/opentelemetry/v1/metrics"
    ),
    metrics_prometheus_remote_write=(
        "https://write.monitoring.{region}.nebius.cloud/projects/"
        "{project_id}/prometheus/api/v1/write"
    ),
    metrics_platform_managed_write=(
        "Nebius-managed Monitoring agent metrics ingest is automatic for Compute VMs "
        "and uses platform-managed regional endpoints; cxcli does not configure a "
        "public customer write endpoint for this path."
    ),
    logs_otlp_write="https://write.logging.{region}.nebius.cloud",
    logs_agent_grpc_write="dns:///write.logging.{region}.nebius.cloud:443",
    logs_platform_managed_write=(
        "When journald collection is enabled, the Nebius-managed Monitoring agent "
        "forwards VM logs through the platform-managed Logging ingest path; cxcli "
        "does not configure a public customer write endpoint for this path."
    ),
    traces_otlp_grpc_write="dns:///write.tracing.{region}.nebius.cloud:443",
    metrics_service_provider_read=(
        "https://read.monitoring.api.nebius.cloud/projects/{project_id}/service-provider/prometheus"
    ),
    metrics_user_read=("https://read.monitoring.api.nebius.cloud/projects/{project_id}/prometheus"),
    metrics_federate_read=(
        "https://read.monitoring.api.nebius.cloud/projects/"
        "{project_id}/buckets/<service-provider>/prometheus/federate"
    ),
    logs_loki_read="https://read.logging.api.nebius.cloud/projects/{project_id}",
    traces_tempo_read="https://read.tracing.api.nebius.cloud/projects/{project_id}/tempo",
)

_SERVICE_PROVIDER_METRIC_BUCKETS = ("compute", "gpu", "nbs", "sp_storage", "msp")
_VM_JOURNALD_LOGS_ENABLED_LABEL = "nebius.o11y.systemd-logs-collection.enabled"
_VM_JOURNALD_LOGS_UNITS_LABEL = "nebius.o11y.systemd-logs-collection.units"
_VM_COLLECTOR_ENABLED_INPUT = "observability_collector_enabled"
_VM_COLLECTOR_REGION_INPUT = "observability_collector_region_id"
_VM_COLLECTOR_PACKAGE_VERSION_INPUT = "observability_collector_package_version"
_VM_COLLECTOR_IAM_TOKEN_FILE_INPUT = "observability_collector_iam_token_file"
_VM_COLLECTOR_LOGS_ENABLED_INPUT = "observability_collector_logs_enabled"
_VM_COLLECTOR_LOGS_SYSTEMD_UNITS_INPUT = "observability_collector_logs_systemd_units"
_VM_COLLECTOR_METRICS_ENABLED_INPUT = "observability_collector_metrics_enabled"
_VM_COLLECTOR_METRICS_EXPORT_PORT_INPUT = "observability_collector_metrics_export_port"
_VM_COLLECTOR_PROMETHEUS_AGENT_PORT_INPUT = "observability_collector_prometheus_agent_port"
_VM_COLLECTOR_MANAGED_INPUTS = (
    _VM_COLLECTOR_ENABLED_INPUT,
    _VM_COLLECTOR_REGION_INPUT,
    _VM_COLLECTOR_PACKAGE_VERSION_INPUT,
    _VM_COLLECTOR_IAM_TOKEN_FILE_INPUT,
    _VM_COLLECTOR_LOGS_ENABLED_INPUT,
    _VM_COLLECTOR_LOGS_SYSTEMD_UNITS_INPUT,
    _VM_COLLECTOR_METRICS_ENABLED_INPUT,
    _VM_COLLECTOR_METRICS_EXPORT_PORT_INPUT,
    _VM_COLLECTOR_PROMETHEUS_AGENT_PORT_INPUT,
)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    if not isinstance(payload, dict):
        return {}
    return payload


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
    return [
        row for row in charts if isinstance(row, dict) and component_type_id(row) == app_id
    ]


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
    normalized_app_id = _as_text(app_id).lower()
    normalized_target_ref = _as_text(target_ref).lower()
    if normalized_app_id and normalized_target_ref:
        return f"{normalized_app_id}-{normalized_target_ref}"
    return normalized_app_id


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


def _project_observability_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _mapping(payload.get("observability"))


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


def _endpoint_templates(settings: InfraObservabilitySettings) -> ObservabilityEndpointTemplates:
    endpoints = settings.endpoints
    if endpoints == ObservabilityEndpointTemplates():
        return _DEFAULT_OBSERVABILITY_ENDPOINTS
    return endpoints


def _merge_endpoint_templates(
    base: ObservabilityEndpointTemplates,
    overlay: ObservabilityEndpointTemplates,
) -> ObservabilityEndpointTemplates:
    merged: dict[str, str] = {}
    for field_name in ObservabilityEndpointTemplates.__dataclass_fields__:
        merged[field_name] = _as_text(getattr(overlay, field_name)) or _as_text(
            getattr(base, field_name)
        )
    return ObservabilityEndpointTemplates(**merged)


def _combined_endpoint_templates() -> ObservabilityEndpointTemplates:
    templates = _DEFAULT_OBSERVABILITY_ENDPOINTS
    templates = _merge_endpoint_templates(templates, _endpoint_templates(_mk8s_observability_settings()))
    templates = _merge_endpoint_templates(templates, _endpoint_templates(_vm_observability_settings()))
    return templates


def _render_endpoint_template(
    template: str,
    *,
    project_id: str,
    region_id: str,
) -> str:
    return _as_text(template).replace("{project_id}", project_id).replace("{region}", region_id)


def _render_observability_endpoint_group(
    templates: ObservabilityEndpointTemplates,
    *,
    project_id: str,
    region_id: str,
    names: tuple[str, ...],
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for name in names:
        value = _render_endpoint_template(
            getattr(templates, name, ""),
            project_id=project_id,
            region_id=region_id,
        )
        if value:
            rendered[name] = value
    return rendered


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
            "collector": {
                "enabled": vm_observability.standalone_collector.enabled_by_default,
                "metrics": {
                    "enabled": vm_observability.standalone_collector.metrics.enabled_by_default,
                },
                "logs": {
                    "enabled": vm_observability.standalone_collector.logs.enabled_by_default,
                    "systemd_units": list(vm_observability.standalone_collector.logs.systemd_units),
                },
            },
            "logs": {
                "enabled": vm_observability.logs.enabled_by_default,
                "systemd_units": list(vm_observability.logs.systemd_units),
            }
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
        if isinstance(item, dict) and component_type_id(item) in {"mk8s", "vm"}
    }
    if not component_ids:
        return False

    defaults = observability_project_defaults(
        include_kubernetes="mk8s" in component_ids,
        include_vm="vm" in component_ids,
    )
    existing = payload_map.get("observability")
    merged = copy.deepcopy(defaults)
    if isinstance(existing, Mapping):
        existing_filtered = copy.deepcopy(dict(existing))
        if "mk8s" not in component_ids:
            existing_filtered.pop("kubernetes", None)
        if "vm" not in component_ids:
            existing_filtered.pop("vm", None)
        merged = _deep_merge_mapping(merged, existing_filtered)
    if payload_map.get("observability") != merged:
        payload_map["observability"] = merged
        return True
    return False


def _effective_kubernetes_observability_config(
    payload: dict[str, Any],
    *,
    mk8s_settings: InfraObservabilitySettings | None = None,
) -> KubernetesObservabilityConfig:
    settings = mk8s_settings or _mk8s_observability_settings()
    defaults = observability_project_defaults(mk8s_settings=settings).get("kubernetes", {})
    project = _project_observability_config(payload)
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


def _effective_vm_standalone_collector_config(
    payload: dict[str, Any],
    *,
    vm_settings: InfraObservabilitySettings | None = None,
) -> VmStandaloneCollectorConfig:
    settings = vm_settings or _vm_observability_settings()
    defaults = observability_project_defaults(vm_settings=settings).get("vm", {})
    project = _project_observability_config(payload)
    vm = _mapping(project.get("vm"))
    collector = _mapping(vm.get("collector"))
    logs = _mapping(collector.get("logs"))
    metrics = _mapping(collector.get("metrics"))
    default_collector = _mapping(_mapping(defaults).get("collector"))
    default_logs = _mapping(default_collector.get("logs"))
    default_metrics = _mapping(default_collector.get("metrics"))

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

    return VmStandaloneCollectorConfig(
        enabled=_resolve_bool(
            collector.get("enabled"),
            default=bool(default_collector.get("enabled", False)),
        ),
        metrics_enabled=_resolve_bool(
            metrics.get("enabled"),
            default=bool(default_metrics.get("enabled", True)),
        ),
        logs_enabled=_resolve_bool(
            logs.get("enabled"),
            default=bool(default_logs.get("enabled", True)),
        ),
        logs_systemd_units=_resolve_list(
            logs.get("systemd_units"),
            default=tuple(default_logs.get("systemd_units", [])),
        ),
        package_version=_as_text(settings.standalone_collector.package_version),
        iam_token_file=_as_text(settings.standalone_collector.iam_token_file)
        or "/mnt/cloud-metadata/token",
        metrics_export_port=settings.standalone_collector.metrics_export_port,
        prometheus_agent_port=settings.standalone_collector.prometheus_agent_port,
    )


def _observability_enabled(payload: dict[str, Any]) -> bool:
    project = _project_observability_config(payload)
    enabled = _coerce_optional_bool(project.get("enabled"))
    return bool(enabled)


def _kubernetes_agent_required(
    payload: dict[str, Any],
    *,
    kubernetes_settings: KubernetesObservabilityConfig | None = None,
) -> bool:
    if not _observability_enabled(payload):
        return False
    if "mk8s" not in _enabled_component_ids(payload, scope="infra"):
        return False
    effective = kubernetes_settings or _effective_kubernetes_observability_config(payload)
    return effective.logs_enabled or effective.metrics_enabled or effective.traces_enabled


def _vm_monitoring_agent_enabled(payload: dict[str, Any]) -> bool:
    if "vm" not in _enabled_component_ids(payload, scope="infra"):
        return False
    return _vm_observability_settings().mode == "monitoring_agent"


def _enabled_vm_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for row in _infra_component_rows(payload):
        if (
            isinstance(row, dict)
            and component_type_id(row) == "vm"
            and bool(row.get("enabled", False))
        ):
            rows.append(row)
    return tuple(rows)


def _vm_journald_logs_enabled(
    payload: dict[str, Any],
    *,
    vm_settings: VmObservabilityConfig | None = None,
) -> bool:
    if not _vm_monitoring_agent_enabled(payload):
        return False
    effective = vm_settings or _effective_vm_observability_config(payload)
    return effective.logs_enabled


def _vm_standalone_collector_enabled(
    payload: dict[str, Any],
    *,
    collector_settings: VmStandaloneCollectorConfig | None = None,
) -> bool:
    if not _observability_enabled(payload):
        return False
    if not _vm_monitoring_agent_enabled(payload):
        return False
    effective = collector_settings or _effective_vm_standalone_collector_config(payload)
    return effective.enabled


def _vm_standalone_collector_metrics_enabled(
    payload: dict[str, Any],
    *,
    collector_settings: VmStandaloneCollectorConfig | None = None,
) -> bool:
    effective = collector_settings or _effective_vm_standalone_collector_config(payload)
    return _vm_standalone_collector_enabled(payload, collector_settings=effective) and bool(
        effective.metrics_enabled
    )


def _vm_standalone_collector_logs_enabled(
    payload: dict[str, Any],
    *,
    collector_settings: VmStandaloneCollectorConfig | None = None,
) -> bool:
    effective = collector_settings or _effective_vm_standalone_collector_config(payload)
    return _vm_standalone_collector_enabled(payload, collector_settings=effective) and bool(
        effective.logs_enabled
    )


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


def _mk8s_gpu_nodes_enabled(row: Mapping[str, Any]) -> bool:
    inputs = row.get("inputs")
    if not isinstance(inputs, Mapping):
        return False
    enabled = _coerce_optional_bool(inputs.get("gpu_enabled"))
    return bool(enabled)


def _mk8s_default_gpu_stack_source() -> str:
    module = tf_module_source_by_id("mk8s", sources=_catalog_sources())
    if module is None:
        return ""
    for default in module.defaults:
        if default.target_path == "inputs.gpu_stack_source":
            return _as_text(default.value).lower()
    return ""


def _mk8s_gpu_stack_source(row: Mapping[str, Any]) -> str:
    inputs = row.get("inputs")
    explicit = _as_text(_mapping(inputs).get("gpu_stack_source")).lower()
    return explicit or _mk8s_default_gpu_stack_source()


def _observability_gpu_node_label_targets(
    payload: dict[str, Any],
) -> tuple[ObservabilityMetricTarget, ...]:
    if not _observability_enabled(payload):
        return ()
    if "mk8s" not in _enabled_component_ids(payload, scope="infra"):
        return ()
    if not _effective_kubernetes_observability_config(payload).metrics_enabled:
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
                f"Observability metric targets resolve conflicting {conflict_label} "
                f"for '{key}'"
            )
        target[key] = value


def _observability_gpu_node_labels_for_row(
    payload: dict[str, Any],
    row: Mapping[str, Any],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for target in _observability_gpu_node_label_targets(payload):
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
    overrides = inputs.get("mk8s_gpu_node_group_overrides")
    if not isinstance(overrides, dict):
        if not create:
            return inputs, None, None
        overrides = {}
        inputs["mk8s_gpu_node_group_overrides"] = overrides
    labels = overrides.get("labels")
    if not isinstance(labels, dict):
        if not create:
            return inputs, overrides, None
        labels = {}
        overrides["labels"] = labels
    return inputs, overrides, labels


def _prune_empty_gpu_node_label_maps(
    *,
    inputs: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    labels: dict[str, Any] | None,
) -> None:
    if overrides is not None and labels is not None and not labels:
        overrides.pop("labels", None)
    if inputs is not None and overrides is not None and not overrides:
        inputs.pop("mk8s_gpu_node_group_overrides", None)


def observability_gpu_node_label_reconciliation(
    payload_or_config: Any,
) -> ObservabilityGpuNodeLabelReconciliation:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
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
        for target in _observability_gpu_node_label_targets(payload):
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
    kubernetes_settings = _effective_kubernetes_observability_config(payload)
    observability_enabled = _observability_enabled(payload)
    kubernetes_agent_required = _kubernetes_agent_required(
        payload,
        kubernetes_settings=kubernetes_settings,
    )
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
        if collector_app_id in selected and not observability_enabled:
            issues.append(f"apps:{collector_app_id} requires observability.enabled=true")
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

    return ObservabilityAppSelection(
        selected_app_ids=tuple(sorted(selected)),
        auto_enabled_app_ids=tuple(sorted(auto_enabled)),
        issues=tuple(issues),
        observability_enabled=observability_enabled,
        kubernetes_agent_required=kubernetes_agent_required,
        collector_app_id=collector_app_id,
        vm_monitoring_agent_enabled=vm_monitoring_agent_enabled,
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
    vm_settings = _effective_vm_observability_config(payload)
    collector_settings = _effective_vm_standalone_collector_config(payload)
    collector_requested = bool(collector_settings.enabled)
    if collector_requested and not _observability_enabled(payload):
        issues.append("observability.vm.collector.enabled=true requires observability.enabled=true")
    if collector_requested and not _enabled_vm_rows(payload):
        issues.append("observability.vm.collector.enabled=true requires one enabled infra:vm component")
    if collector_requested and not (collector_settings.metrics_enabled or collector_settings.logs_enabled):
        issues.append(
            "observability.vm.collector.enabled=true requires observability.vm.collector.metrics.enabled "
            "or observability.vm.collector.logs.enabled to stay true"
        )
    if collector_requested:
        missing_service_accounts = [
            component_instance_label(component_type_id(row), component_instance_id(row))
            for row in _enabled_vm_rows(payload)
            if not _as_text(_mapping(row.get("inputs")).get("service_account_id"))
        ]
        if missing_service_accounts:
            issues.append(
                "VM standalone collector requires inputs.service_account_id on enabled vm component(s): "
                + ", ".join(sorted(missing_service_accounts))
            )
    if (
        collector_requested
        and collector_settings.logs_enabled
        and _vm_journald_logs_enabled(payload, vm_settings=vm_settings)
    ):
        issues.append(
            "observability.vm.logs.enabled and observability.vm.collector.logs.enabled "
            "cannot both be true; choose the built-in Monitoring-agent journald path or the "
            "cxcli-managed standalone collector path"
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
    if resolution.issues or not resolution.auto_enabled_app_ids:
        return False

    entry_by_id = {entry.id: entry for entry in entries}
    charts = _app_chart_rows(payload)
    changed = False
    collector_app_id = _collector_app_id()
    target_refs = enabled_cluster_target_refs(payload)
    for app_id in resolution.auto_enabled_app_ids:
        entry = entry_by_id.get(app_id)
        if entry is None:
            continue
        if len(target_refs) > 1 and app_id == collector_app_id:
            remaining_rows = _app_chart_rows_for_id(payload, app_id)
            rows_by_target = {
                app_chart_target_ref(row): row
                for row in remaining_rows
                if app_chart_target_ref(row)
            }
            unassigned_rows = [row for row in remaining_rows if not app_chart_target_ref(row)]
            for target_ref in target_refs:
                existing = rows_by_target.get(target_ref)
                if existing is None and unassigned_rows:
                    existing = unassigned_rows.pop(0)
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
                if not str(existing.get("instance_id", "")).strip():
                    existing["instance_id"] = _observability_instance_id(
                        app_id, target_ref=target_ref
                    )
                _apply_observability_app_entry_defaults(row=existing, entry=entry)
                if app_chart_target_ref(existing) != target_ref:
                    existing[TARGET_REF_FIELD] = target_ref
                if existing != before:
                    changed = True
            continue
        existing = _app_chart_row(payload, app_id)
        if existing is None:
            row = _new_observability_app_row(entry)
            _apply_observability_target_ref(payload=payload, row=row)
            charts.append(row)
            changed = True
            continue
        before = copy.deepcopy(existing)
        existing["enabled"] = True
        if not str(existing.get("id", "")).strip():
            existing["id"] = entry.id
        if not str(existing.get("instance_id", "")).strip():
            existing["instance_id"] = entry.id
        _apply_observability_app_entry_defaults(row=existing, entry=entry)
        _apply_observability_target_ref(payload=payload, row=existing)
        if existing != before:
            changed = True
    return changed


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in str(path).split(".") if segment)


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


def _collector_managed_values(
    payload: dict[str, Any],
) -> dict[str, Any]:
    settings = _effective_kubernetes_observability_config(payload)
    return {
        "values.config.logs.enabled": settings.logs_enabled,
        "values.config.logs.collectAgentLogs": settings.logs_collect_agent_logs,
        "values.config.logs.excludedNamespaces": list(settings.logs_excluded_namespaces),
        "values.config.metrics.enabled": settings.metrics_enabled,
        "values.config.metrics.collectAgentMetrics": settings.metrics_collect_agent_metrics,
        "values.config.metrics.collectK8sClusterMetrics": settings.metrics_collect_k8s_cluster_metrics,
        "values.config.metrics.excludedNamespaces": list(settings.metrics_excluded_namespaces),
        "values.config.traces.enabled": settings.traces_enabled,
    }


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


def _collector_additional_targets(
    payload: dict[str, Any],
    *,
    target_ref: str = "",
) -> tuple[dict[str, Any], ...]:
    resolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
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


def _merge_managed_additional_targets(
    *,
    chart_row: dict[str, Any],
    managed_targets: tuple[dict[str, Any], ...],
) -> None:
    values = chart_row.get("values")
    if not isinstance(values, dict):
        values = {}
        chart_row["values"] = values
    metrics = _mapping(_nested_path_value(values, "config.metrics"))
    existing_raw = metrics.get("additionalTargets")
    existing_targets = existing_raw if isinstance(existing_raw, list) else []
    managed_job_names = {
        _as_text(item.get("job_name")) for item in managed_targets if _as_text(item.get("job_name"))
    }
    preserved = [
        item
        for item in existing_targets
        if isinstance(item, Mapping) and _as_text(item.get("job_name")) not in managed_job_names
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
    collector_settings = _effective_vm_standalone_collector_config(payload)
    collector_region = _payload_region_id(payload)
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
        built_in_logs_enabled = _vm_journald_logs_enabled(payload, vm_settings=vm_settings) and bool(
            row.get("enabled", False)
        )
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

        collector_enabled = _vm_standalone_collector_enabled(
            payload,
            collector_settings=collector_settings,
        ) and bool(row.get("enabled", False))
        if collector_enabled:
            managed_inputs: dict[str, Any] = {
                _VM_COLLECTOR_ENABLED_INPUT: True,
                _VM_COLLECTOR_REGION_INPUT: collector_region,
                _VM_COLLECTOR_PACKAGE_VERSION_INPUT: collector_settings.package_version,
                _VM_COLLECTOR_IAM_TOKEN_FILE_INPUT: collector_settings.iam_token_file,
                _VM_COLLECTOR_LOGS_ENABLED_INPUT: bool(collector_settings.logs_enabled),
                _VM_COLLECTOR_LOGS_SYSTEMD_UNITS_INPUT: list(collector_settings.logs_systemd_units),
                _VM_COLLECTOR_METRICS_ENABLED_INPUT: bool(collector_settings.metrics_enabled),
                _VM_COLLECTOR_METRICS_EXPORT_PORT_INPUT: collector_settings.metrics_export_port,
                _VM_COLLECTOR_PROMETHEUS_AGENT_PORT_INPUT: collector_settings.prometheus_agent_port,
            }
            for key, value in managed_inputs.items():
                if inputs.get(key) != value:
                    inputs[key] = copy.deepcopy(value)
                    changed = True
            continue

        for key in _VM_COLLECTOR_MANAGED_INPUTS:
            if key in inputs:
                del inputs[key]
                changed = True
    return changed


def materialize_observability_app_values(payload_or_config: Any) -> None:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    collector_app_id = _collector_app_id()
    if not collector_app_id:
        return
    chart_rows = [
        row
        for row in _app_chart_rows_for_id(payload, collector_app_id)
        if isinstance(row, dict) and bool(row.get("enabled", False))
    ]
    if not chart_rows:
        return

    for chart_row in chart_rows:
        if _kubernetes_agent_required(payload):
            for target_path, target_value in _collector_managed_values(payload).items():
                _set_path_value(chart_row, target_path, copy.deepcopy(target_value))
            metrics_enabled = _effective_kubernetes_observability_config(payload).metrics_enabled
            managed_targets = (
                _collector_additional_targets(
                    payload,
                    target_ref=app_chart_target_ref(chart_row),
                )
                if metrics_enabled
                else ()
            )
            _merge_managed_additional_targets(chart_row=chart_row, managed_targets=managed_targets)
            continue

        for stale_path in sorted(
            _COLLECTOR_MANAGED_VALUE_PATHS, key=lambda item: item.count("."), reverse=True
        ):
            _delete_path_value(chart_row, stale_path)
        _merge_managed_additional_targets(chart_row=chart_row, managed_targets=())


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
    kubernetes_settings = _effective_kubernetes_observability_config(payload)
    vm_settings = _effective_vm_observability_config(payload)
    collector_settings = _effective_vm_standalone_collector_config(payload)
    observability_enabled = _observability_enabled(payload)
    enabled_infra = _enabled_component_ids(payload, scope="infra")
    kubernetes_enabled = bool(observability_enabled and "mk8s" in enabled_infra)
    kubernetes_logs_enabled = bool(kubernetes_enabled and kubernetes_settings.logs_enabled)
    kubernetes_metrics_enabled = bool(kubernetes_enabled and kubernetes_settings.metrics_enabled)
    kubernetes_traces_enabled = bool(kubernetes_enabled and kubernetes_settings.traces_enabled)
    vm_service_metrics_enabled = _vm_monitoring_agent_enabled(payload)
    vm_logs_enabled = _vm_journald_logs_enabled(payload, vm_settings=vm_settings)
    vm_standalone_metrics_enabled = _vm_standalone_collector_metrics_enabled(
        payload,
        collector_settings=collector_settings,
    )
    vm_standalone_logs_enabled = _vm_standalone_collector_logs_enabled(
        payload,
        collector_settings=collector_settings,
    )
    metrics_enabled = bool(
        kubernetes_metrics_enabled or vm_service_metrics_enabled or vm_standalone_metrics_enabled
    )
    signals = {
        "logs": bool(kubernetes_logs_enabled or vm_logs_enabled or vm_standalone_logs_enabled),
        "metrics": metrics_enabled,
        "traces": kubernetes_traces_enabled,
        "kubernetes_logs": kubernetes_logs_enabled,
        "kubernetes_metrics": kubernetes_metrics_enabled,
        "kubernetes_traces": kubernetes_traces_enabled,
        "vm_service_metrics": vm_service_metrics_enabled,
        "vm_logs": vm_logs_enabled,
        "vm_standalone_collector": _vm_standalone_collector_enabled(
            payload,
            collector_settings=collector_settings,
        ),
        "vm_standalone_metrics": vm_standalone_metrics_enabled,
        "vm_standalone_logs": vm_standalone_logs_enabled,
    }
    templates = _combined_endpoint_templates()
    if not configured:
        return {
            "configured": False,
            "reason": "project_id_and_region_id_required",
            "signals": signals,
            "read": {},
            "write": {},
            "auth": {},
            "service_provider_metric_buckets": list(_SERVICE_PROVIDER_METRIC_BUCKETS),
        }

    read_names: list[str] = []
    write_names: list[str] = []
    if metrics_enabled:
        read_names.extend(("metrics_service_provider_read", "metrics_federate_read"))
    if kubernetes_metrics_enabled:
        read_names.append("metrics_user_read")
        write_names.extend(("metrics_otlp_write", "metrics_prometheus_remote_write"))
    if vm_standalone_metrics_enabled:
        read_names.append("metrics_user_read")
        write_names.append("metrics_prometheus_remote_write")
    if vm_service_metrics_enabled:
        write_names.append("metrics_platform_managed_write")
    if kubernetes_logs_enabled or vm_logs_enabled or vm_standalone_logs_enabled:
        read_names.append("logs_loki_read")
    if kubernetes_logs_enabled:
        write_names.extend(("logs_otlp_write", "logs_agent_grpc_write"))
    if vm_standalone_logs_enabled:
        write_names.append("logs_agent_grpc_write")
    if vm_logs_enabled:
        write_names.append("logs_platform_managed_write")
    if kubernetes_traces_enabled:
        read_names.append("traces_tempo_read")
        write_names.append("traces_otlp_grpc_write")

    return {
        "configured": True,
        "project_id": resolved_project_id,
        "region": resolved_region_id,
        "signals": signals,
        "read": _render_observability_endpoint_group(
            templates,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            names=tuple(read_names),
        ),
        "write": _render_observability_endpoint_group(
            templates,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            names=tuple(write_names),
        ),
        "auth": {
            "read": (
                "Authorization: Bearer <observability static token or IAM token>; "
                "viewer access is enough for read."
            ),
            "write": (
                "Nebius-managed MK8s and built-in VM Monitoring agents use platform-managed auth. "
                "The cxcli-managed VM standalone collector uses the VM-attached service account "
                "metadata token. External collectors use Authorization: Bearer <observability "
                "static token or IAM token>; editor or signal-specific writer access is required "
                "for ingest."
            ),
        },
        "service_provider_metric_buckets": list(_SERVICE_PROVIDER_METRIC_BUCKETS),
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
    kubernetes_metrics_enabled = _effective_kubernetes_observability_config(payload).metrics_enabled
    vm_logs_enabled = _vm_journald_logs_enabled(payload)
    vm_collector = _effective_vm_standalone_collector_config(payload)
    collector_enabled = bool(
        selection.collector_app_id
        and selection.collector_app_id in _enabled_component_ids(payload, scope="apps")
    )
    dcgm_metric_source_configured = bool(
        selection.observability_enabled
        and collector_enabled
        and kubernetes_metrics_enabled
        and any(
            target.service_name == "nvidia-dcgm-exporter"
            for _app_id, target in selected_metric_targets
        )
    )
    dcgm_node_policy_managed = bool(
        dcgm_metric_source_configured
        and observability_gpu_node_label_reconciliation(payload).enabled
    )
    return {
        "enabled": selection.observability_enabled,
        "kubernetes_agent": collector_enabled,
        "vm_monitoring_agent": selection.vm_monitoring_agent_enabled,
        "vm_journald_logs": vm_logs_enabled,
        "vm_standalone_collector": _vm_standalone_collector_enabled(
            payload,
            collector_settings=vm_collector,
        ),
        "vm_standalone_metrics": _vm_standalone_collector_metrics_enabled(
            payload,
            collector_settings=vm_collector,
        ),
        "vm_standalone_logs": _vm_standalone_collector_logs_enabled(
            payload,
            collector_settings=vm_collector,
        ),
        "gpu_dcgm_metric_source": (
            "prometheus_annotations" if dcgm_metric_source_configured else "disabled"
        ),
        "gpu_dcgm_node_policy": (
            "managed_gpu_operator_dcgm_labels" if dcgm_node_policy_managed else "not_managed"
        ),
        "gpu_dcgm_live_readiness": (
            "verify_live_nvidia_dcgm_exporter_endpoints_after_deploy"
            if dcgm_metric_source_configured
            else "not_configured"
        ),
    }


__all__ = [
    "KubernetesObservabilityConfig",
    "VmObservabilityConfig",
    "VmStandaloneCollectorConfig",
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
    "resolve_observability_app_selection",
]
