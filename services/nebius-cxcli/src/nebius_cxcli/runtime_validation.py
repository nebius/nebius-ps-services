"""Runtime validation for config payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .component_defaults import (
    component_path_has_material_value,
    read_component_path,
    shared_default_target_paths,
)
from .component_instances import (
    INSTANCE_ID_PATTERN,
    component_instance_id,
    component_type_id,
)
from .components import (
    ComponentScope,
    component_entries,
    component_lookup,
    parse_dependency_ref,
)
from .deploy_targets import TARGET_REF_FIELD, app_chart_target_ref
from .mk8s_gpu import mk8s_gpu_dependency_issues
from .observability import observability_dependency_issues
from .runtime_config import read_path_with_catalog
from .runtime_plugin_validation import run_runtime_validation_plugins

_ROOT_KEYS = frozenset({"version", "client_info", "deploy", "observability", "infra", "apps"})
_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_SECTION_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CLIENT_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    resolved = read_path_with_catalog(payload, dotted_path)
    return default if resolved is None else resolved


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validate_client_info(payload: Mapping[str, Any]) -> None:
    client_info = payload.get("client_info")
    if not isinstance(client_info, Mapping):
        raise ValueError("client_info must be a mapping")

    supported_client_info_keys = {"client_name", "nebius", "notifications"}
    unknown_client_info = sorted(
        str(key) for key in client_info if str(key) not in supported_client_info_keys
    )
    if unknown_client_info:
        raise ValueError("client_info has unsupported field(s): " + ", ".join(unknown_client_info))

    client_name = _as_text(client_info.get("client_name"))
    if not client_name:
        raise ValueError("client_info.client_name is required")
    if not _CLIENT_NAME_PATTERN.fullmatch(client_name):
        raise ValueError("client_info.client_name must use lowercase letters, digits, and hyphens")

    nebius = client_info.get("nebius")
    if not isinstance(nebius, Mapping):
        raise ValueError("client_info.nebius must be a mapping")
    supported_nebius_keys = {"tenant_id", "project_id", "region_id"}
    unknown_nebius = sorted(str(key) for key in nebius if str(key) not in supported_nebius_keys)
    if unknown_nebius:
        raise ValueError(
            "client_info.nebius has unsupported field(s): " + ", ".join(unknown_nebius)
        )
    for field in ("tenant_id", "project_id", "region_id"):
        value = _as_text(nebius.get(field))
        if not value:
            raise ValueError(f"client_info.nebius.{field} is required")

    notifications = client_info.get("notifications")
    if not isinstance(notifications, Mapping):
        raise ValueError("client_info.notifications must be a mapping")
    supported_notification_keys = {"email_enabled", "email"}
    unknown_notification_keys = sorted(
        str(key) for key in notifications if str(key) not in supported_notification_keys
    )
    if unknown_notification_keys:
        raise ValueError(
            "client_info.notifications has unsupported field(s): "
            + ", ".join(unknown_notification_keys)
        )
    email_enabled = notifications.get("email_enabled")
    if not isinstance(email_enabled, bool):
        raise ValueError("client_info.notifications.email_enabled must be true or false")
    email = notifications.get("email")
    if email is not None and not isinstance(email, str):
        raise ValueError("client_info.notifications.email must be a string or null")


def _validate_deploy(payload: Mapping[str, Any]) -> None:
    deploy = payload.get("deploy")
    if deploy is None:
        return
    if not isinstance(deploy, Mapping):
        raise ValueError("deploy must be a mapping")

    supported_deploy_keys = {"validations"}
    unknown_deploy_keys = sorted(str(key) for key in deploy if str(key) not in supported_deploy_keys)
    if unknown_deploy_keys:
        raise ValueError("deploy has unsupported field(s): " + ", ".join(unknown_deploy_keys))

    validations = deploy.get("validations")
    if validations is None:
        return
    if not isinstance(validations, Mapping):
        raise ValueError("deploy.validations must be a mapping")

    supported_validation_keys = {"mk8s_gpu"}
    unknown_validation_keys = sorted(
        str(key) for key in validations if str(key) not in supported_validation_keys
    )
    if unknown_validation_keys:
        raise ValueError(
            "deploy.validations has unsupported field(s): " + ", ".join(unknown_validation_keys)
        )

    mk8s_gpu = validations.get("mk8s_gpu")
    if mk8s_gpu is not None and not isinstance(mk8s_gpu, Mapping):
        raise ValueError("deploy.validations.mk8s_gpu must be a mapping")


def _validate_observability(payload: Mapping[str, Any]) -> None:
    observability = payload.get("observability")
    if observability is None:
        return
    if not isinstance(observability, Mapping):
        raise ValueError("observability must be a mapping")
    supported_keys = {"enabled", "kubernetes", "vm"}
    unknown_keys = sorted(str(key) for key in observability if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(
            "observability has unsupported field(s): " + ", ".join(unknown_keys)
        )
    enabled = observability.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("observability.enabled must be true or false")

    kubernetes = observability.get("kubernetes")
    if kubernetes is not None:
        if not isinstance(kubernetes, Mapping):
            raise ValueError("observability.kubernetes must be a mapping")
        supported_kubernetes_keys = {"logs", "metrics", "traces"}
        unknown_kubernetes_keys = sorted(
            str(key) for key in kubernetes if str(key) not in supported_kubernetes_keys
        )
        if unknown_kubernetes_keys:
            raise ValueError(
                "observability.kubernetes has unsupported field(s): "
                + ", ".join(unknown_kubernetes_keys)
            )

        logs = kubernetes.get("logs")
        if logs is not None:
            if not isinstance(logs, Mapping):
                raise ValueError("observability.kubernetes.logs must be a mapping")
            supported_log_keys = {"enabled", "collect_agent_logs", "excluded_namespaces"}
            unknown_log_keys = sorted(str(key) for key in logs if str(key) not in supported_log_keys)
            if unknown_log_keys:
                raise ValueError(
                    "observability.kubernetes.logs has unsupported field(s): "
                    + ", ".join(unknown_log_keys)
                )
            for field in ("enabled", "collect_agent_logs"):
                value = logs.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f"observability.kubernetes.logs.{field} must be true or false")
            excluded_namespaces = logs.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    "observability.kubernetes.logs.excluded_namespaces must be a list of strings"
                )

        metrics = kubernetes.get("metrics")
        if metrics is not None:
            if not isinstance(metrics, Mapping):
                raise ValueError("observability.kubernetes.metrics must be a mapping")
            supported_metric_keys = {
                "enabled",
                "collect_agent_metrics",
                "collect_k8s_cluster_metrics",
                "excluded_namespaces",
            }
            unknown_metric_keys = sorted(
                str(key) for key in metrics if str(key) not in supported_metric_keys
            )
            if unknown_metric_keys:
                raise ValueError(
                    "observability.kubernetes.metrics has unsupported field(s): "
                    + ", ".join(unknown_metric_keys)
                )
            for field in ("enabled", "collect_agent_metrics", "collect_k8s_cluster_metrics"):
                value = metrics.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(
                        f"observability.kubernetes.metrics.{field} must be true or false"
                    )
            excluded_namespaces = metrics.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    "observability.kubernetes.metrics.excluded_namespaces must be a list of strings"
                )

        traces = kubernetes.get("traces")
        if traces is not None:
            if not isinstance(traces, Mapping):
                raise ValueError("observability.kubernetes.traces must be a mapping")
            supported_trace_keys = {"enabled"}
            unknown_trace_keys = sorted(
                str(key) for key in traces if str(key) not in supported_trace_keys
            )
            if unknown_trace_keys:
                raise ValueError(
                    "observability.kubernetes.traces has unsupported field(s): "
                    + ", ".join(unknown_trace_keys)
                )
            value = traces.get("enabled")
            if value is not None and not isinstance(value, bool):
                raise ValueError("observability.kubernetes.traces.enabled must be true or false")

    vm = observability.get("vm")
    if vm is None:
        return
    if not isinstance(vm, Mapping):
        raise ValueError("observability.vm must be a mapping")
    supported_vm_keys = {"logs", "collector"}
    unknown_vm_keys = sorted(str(key) for key in vm if str(key) not in supported_vm_keys)
    if unknown_vm_keys:
        raise ValueError("observability.vm has unsupported field(s): " + ", ".join(unknown_vm_keys))

    logs = vm.get("logs")
    if logs is None:
        return
    if not isinstance(logs, Mapping):
        raise ValueError("observability.vm.logs must be a mapping")
    supported_vm_log_keys = {"enabled", "systemd_units"}
    unknown_vm_log_keys = sorted(
        str(key) for key in logs if str(key) not in supported_vm_log_keys
    )
    if unknown_vm_log_keys:
        raise ValueError(
            "observability.vm.logs has unsupported field(s): " + ", ".join(unknown_vm_log_keys)
        )
    enabled = logs.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("observability.vm.logs.enabled must be true or false")
    systemd_units = logs.get("systemd_units")
    if systemd_units is not None and (
        not isinstance(systemd_units, list) or any(not isinstance(item, str) for item in systemd_units)
    ):
        raise ValueError("observability.vm.logs.systemd_units must be a list of strings")

    collector = vm.get("collector")
    if collector is None:
        return
    if not isinstance(collector, Mapping):
        raise ValueError("observability.vm.collector must be a mapping")
    supported_collector_keys = {"enabled", "logs", "metrics"}
    unknown_collector_keys = sorted(
        str(key) for key in collector if str(key) not in supported_collector_keys
    )
    if unknown_collector_keys:
        raise ValueError(
            "observability.vm.collector has unsupported field(s): "
            + ", ".join(unknown_collector_keys)
        )
    collector_enabled = collector.get("enabled")
    if collector_enabled is not None and not isinstance(collector_enabled, bool):
        raise ValueError("observability.vm.collector.enabled must be true or false")

    collector_logs = collector.get("logs")
    if collector_logs is not None:
        if not isinstance(collector_logs, Mapping):
            raise ValueError("observability.vm.collector.logs must be a mapping")
        supported_collector_log_keys = {"enabled", "systemd_units"}
        unknown_collector_log_keys = sorted(
            str(key) for key in collector_logs if str(key) not in supported_collector_log_keys
        )
        if unknown_collector_log_keys:
            raise ValueError(
                "observability.vm.collector.logs has unsupported field(s): "
                + ", ".join(unknown_collector_log_keys)
            )
        collector_logs_enabled = collector_logs.get("enabled")
        if collector_logs_enabled is not None and not isinstance(collector_logs_enabled, bool):
            raise ValueError("observability.vm.collector.logs.enabled must be true or false")
        collector_units = collector_logs.get("systemd_units")
        if collector_units is not None and (
            not isinstance(collector_units, list)
            or any(not isinstance(item, str) for item in collector_units)
        ):
            raise ValueError(
                "observability.vm.collector.logs.systemd_units must be a list of strings"
            )

    collector_metrics = collector.get("metrics")
    if collector_metrics is not None:
        if not isinstance(collector_metrics, Mapping):
            raise ValueError("observability.vm.collector.metrics must be a mapping")
        supported_collector_metric_keys = {"enabled"}
        unknown_collector_metric_keys = sorted(
            str(key) for key in collector_metrics if str(key) not in supported_collector_metric_keys
        )
        if unknown_collector_metric_keys:
            raise ValueError(
                "observability.vm.collector.metrics has unsupported field(s): "
                + ", ".join(unknown_collector_metric_keys)
            )
        collector_metrics_enabled = collector_metrics.get("enabled")
        if collector_metrics_enabled is not None and not isinstance(
            collector_metrics_enabled, bool
        ):
            raise ValueError("observability.vm.collector.metrics.enabled must be true or false")


def _enabled_component_ids(payload: Mapping[str, Any], *, scope: ComponentScope) -> set[str]:
    selected: set[str] = set()
    if scope == "infra":
        infra = payload.get("infra")
        if not isinstance(infra, Mapping):
            return selected
        components = infra.get("components")
        if not isinstance(components, list):
            return selected
        for item in components:
            if not isinstance(item, Mapping):
                continue
            if not bool(item.get("enabled", False)):
                continue
            component_id = _as_text(item.get("id")).lower()
            if component_id:
                selected.add(component_id)
        return selected

    apps = payload.get("apps")
    if not isinstance(apps, Mapping):
        return selected
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return selected
    for item in charts:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        chart_id = _as_text(item.get("id")).lower()
        if chart_id:
            selected.add(chart_id)
    return selected


def _expected_app_group(config_path: str) -> str | None:
    parts = config_path.split(".")
    if len(parts) < 3:
        return None
    if parts[0] != "apps":
        return None
    return parts[1]


def _component_config_path_label(
    *,
    scope: ComponentScope,
    component_id: str,
    instance_id: str,
    target_path: str,
) -> str:
    collection = "components" if scope == "infra" else "charts"
    selector = f"id={component_id}"
    if instance_id and instance_id != component_id:
        selector = f"{selector},instance_id={instance_id}"
    return f"{scope}.{collection}[{selector}].{target_path}"


def _validate_materialized_shared_defaults(payload: Mapping[str, Any]) -> None:
    scopes: tuple[tuple[ComponentScope, str, str], ...] = (
        ("infra", "infra", "components"),
        ("apps", "apps", "charts"),
    )
    for scope, section_name, collection_name in scopes:
        section = payload.get(section_name)
        if not isinstance(section, Mapping):
            continue
        rows = section.get(collection_name)
        if not isinstance(rows, list):
            continue
        entry_by_id = {entry.id: entry for entry in component_entries(scope)}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if not bool(row.get("enabled", False)):
                continue
            component_id = component_type_id(row)
            if not component_id:
                continue
            entry = entry_by_id.get(component_id)
            if entry is None:
                continue
            instance_id = component_instance_id(row)
            if not instance_id:
                continue
            for target_path in sorted(shared_default_target_paths(entry)):
                value = read_component_path(row, target_path)
                if component_path_has_material_value(value):
                    continue
                raise ValueError(
                    f"{_component_config_path_label(scope=scope, component_id=component_id, instance_id=instance_id, target_path=target_path)} "
                    "is required; shared-derived defaults must be materialized into config.yaml during create/component add"
                )


def validate_dynamic_payload_structure(payload: Mapping[str, Any]) -> None:
    """Validate dynamic model sections (`infra.components[]`, `apps.charts[]`)."""
    infra = payload.get("infra")
    apps = payload.get("apps")
    if not isinstance(infra, Mapping) or not isinstance(apps, Mapping):
        return

    infra_components = infra.get("components")
    apps_charts = apps.get("charts")
    if infra_components is None and apps_charts is None:
        return

    if not isinstance(infra_components, list):
        raise ValueError("infra.components must be a list in dynamic config mode")
    if not isinstance(apps_charts, list):
        raise ValueError("apps.charts must be a list in dynamic config mode")

    app_lookup = component_lookup("apps")
    infra_lookup = component_lookup("infra")
    seen_infra_instance_ids: set[str] = set()
    seen_global_instance_ids: set[str] = set()
    cluster_target_refs: set[str] = set()
    for index, raw_component in enumerate(infra_components):
        if not isinstance(raw_component, Mapping):
            raise ValueError(f"infra.components[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key)
            for key in raw_component
            if str(key) not in {"id", "instance_id", "enabled", "source", "version", "inputs"}
        )
        if unknown_keys:
            raise ValueError(
                f"infra.components[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        component_id = component_type_id(raw_component)
        if not component_id:
            raise ValueError(f"infra.components[{index}].id is required")
        if not _ID_PATTERN.fullmatch(component_id):
            raise ValueError(
                f"infra.components[{index}].id must use lowercase letters, digits, and hyphens"
            )
        raw_instance_id = _as_text(raw_component.get("instance_id")).lower()
        if raw_instance_id and not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"infra.components[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = component_instance_id(raw_component)
        if not instance_id:
            raise ValueError(f"infra.components[{index}].instance_id could not be derived")
        if instance_id in seen_infra_instance_ids:
            raise ValueError(f"infra.components[{index}].instance_id '{instance_id}' is duplicated")
        if instance_id in seen_global_instance_ids:
            raise ValueError(
                f"component instance_id '{instance_id}' is duplicated across infra/apps"
            )
        seen_infra_instance_ids.add(instance_id)
        seen_global_instance_ids.add(instance_id)

        if not isinstance(raw_component.get("enabled"), bool):
            raise ValueError(f"infra.components[{index}].enabled must be true or false")
        source_value = raw_component.get("source")
        if source_value is not None and not isinstance(source_value, str):
            raise ValueError(f"infra.components[{index}].source must be a string when set")
        version_value = raw_component.get("version")
        if version_value is not None and not isinstance(version_value, str):
            raise ValueError(f"infra.components[{index}].version must be a string when set")
        inputs = raw_component.get("inputs")
        if not isinstance(inputs, Mapping):
            raise ValueError(f"infra.components[{index}].inputs must be a mapping")
        if "module" in inputs:
            raise ValueError(
                f"infra.components[{index}].inputs.module is not supported; "
                "set module source at infra.components[].source and module vars directly under infra.components[].inputs"
            )
        if component_id == "mk8s" and "gpu_validation_overrides" in inputs:
            raise ValueError(
                "infra.components[].inputs.gpu_validation_overrides is no longer supported; "
                "use deploy.validations.mk8s_gpu.*"
            )
        entry = infra_lookup.get(component_id)
        if entry is not None and entry.handoff is not None and bool(raw_component.get("enabled", False)):
            cluster_target_refs.add(instance_id)

    seen_app_instance_ids: set[str] = set()
    for index, raw_chart in enumerate(apps_charts):
        if not isinstance(raw_chart, Mapping):
            raise ValueError(f"apps.charts[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key)
            for key in raw_chart
            if str(key)
            not in {
                "id",
                "instance_id",
                "group",
                "enabled",
                "repo",
                "version",
                TARGET_REF_FIELD,
                "namespace",
                "release-name",
                "values",
            }
        )
        if unknown_keys:
            raise ValueError(
                f"apps.charts[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        chart_id = component_type_id(raw_chart)
        if not chart_id:
            raise ValueError(f"apps.charts[{index}].id is required")
        if not _ID_PATTERN.fullmatch(chart_id):
            raise ValueError(
                f"apps.charts[{index}].id must use lowercase letters, digits, and hyphens"
            )
        raw_instance_id = _as_text(raw_chart.get("instance_id")).lower()
        if raw_instance_id and not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"apps.charts[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = component_instance_id(raw_chart)
        if not instance_id:
            raise ValueError(f"apps.charts[{index}].instance_id could not be derived")
        if instance_id in seen_app_instance_ids:
            raise ValueError(f"apps.charts[{index}].instance_id '{instance_id}' is duplicated")
        if instance_id in seen_global_instance_ids:
            raise ValueError(
                f"component instance_id '{instance_id}' is duplicated across infra/apps"
            )
        seen_app_instance_ids.add(instance_id)
        seen_global_instance_ids.add(instance_id)

        entry = app_lookup.get(chart_id)

        group = _as_text(raw_chart.get("group")).lower()
        if group and not _SECTION_PATTERN.fullmatch(group):
            raise ValueError(
                f"apps.charts[{index}].group must use lowercase letters, digits, and hyphens"
            )
        expected_group = _expected_app_group(entry.config_path) if entry else None
        if group and expected_group and group != expected_group:
            raise ValueError(
                f"apps.charts[{index}].group must be '{expected_group}' for chart '{chart_id}'"
            )

        if not isinstance(raw_chart.get("enabled"), bool):
            raise ValueError(f"apps.charts[{index}].enabled must be true or false")
        for key in ("repo", "version", "namespace"):
            value = raw_chart.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"apps.charts[{index}].{key} must be a string when set")
        target_ref = raw_chart.get(TARGET_REF_FIELD)
        if target_ref is not None and not isinstance(target_ref, str):
            raise ValueError(f"apps.charts[{index}].{TARGET_REF_FIELD} must be a string when set")
        normalized_target_ref = app_chart_target_ref(raw_chart)
        if target_ref and not INSTANCE_ID_PATTERN.fullmatch(normalized_target_ref):
            raise ValueError(
                f"apps.charts[{index}].{TARGET_REF_FIELD} must use lowercase letters, digits, and hyphens"
            )
        release_name = raw_chart.get("release-name")
        if release_name is not None and not isinstance(release_name, str):
            raise ValueError(f"apps.charts[{index}].release-name must be a string when set")
        if not isinstance(raw_chart.get("values"), Mapping):
            raise ValueError(f"apps.charts[{index}].values must be a mapping")
        if bool(raw_chart.get("enabled", False)):
            if cluster_target_refs:
                if not normalized_target_ref:
                    raise ValueError(
                        f"apps.charts[{index}].{TARGET_REF_FIELD} is required when cluster targets are enabled"
                    )
                if normalized_target_ref not in cluster_target_refs:
                    available = ", ".join(sorted(cluster_target_refs))
                    raise ValueError(
                        f"apps.charts[{index}].{TARGET_REF_FIELD} must reference one of the enabled cluster targets: {available}"
                    )
            elif normalized_target_ref:
                raise ValueError(
                    f"apps.charts[{index}].{TARGET_REF_FIELD} is not allowed when no built-in cluster target is enabled"
                )


def validate_runtime_payload(payload: Mapping[str, Any]) -> None:
    """Validate config payload with runtime checks."""
    if not isinstance(payload, Mapping):
        raise ValueError("config.yaml root must be a mapping")

    unknown_root = sorted(key for key in payload if key not in _ROOT_KEYS)
    if unknown_root:
        raise ValueError(f"unknown field(s) at root: {', '.join(unknown_root)}")

    if _as_text(payload.get("version")) not in {"", "v1"}:
        raise ValueError("version must be 'v1'")

    _validate_client_info(payload)
    _validate_deploy(payload)
    _validate_observability(payload)

    infra = payload.get("infra")
    if isinstance(infra, Mapping):
        legacy_shared_paths = [key for key in ("ssh_user_name", "ssh_public_key") if key in infra]
        if legacy_shared_paths:
            raise ValueError(
                "infra.ssh_user_name and infra.ssh_public_key are no longer root infra fields. "
                "Set ssh_user_name/ssh_public_key on the selected jump-host component inputs instead "
                "(for example infra.components[id=wireguard-jumphost].inputs.ssh_public_key). "
                "component_sources.yaml shared.admin_ssh.user_name remains available as a "
                "catalog-level seed that create/component add materialize into jump-host "
                "component inputs."
            )

    selected_by_scope: dict[ComponentScope, set[str]] = {
        "infra": _enabled_component_ids(payload, scope="infra"),
        "apps": _enabled_component_ids(payload, scope="apps"),
    }
    for scope in ("infra", "apps"):
        typed_scope: ComponentScope = scope
        lookup = {entry.id: entry for entry in component_entries(typed_scope)}
        for entry_id in sorted(selected_by_scope[typed_scope]):
            entry = lookup.get(entry_id)
            if entry is None:
                continue
            # Apps dependencies are resolved from Helm Chart.yaml at runtime.
            dependency_refs = entry.depends_on if typed_scope == "infra" else ()
            for raw_ref in dependency_refs:
                dep_scope, dep_id = parse_dependency_ref(raw_ref, default_scope=typed_scope)
                if dep_id not in selected_by_scope[dep_scope]:
                    raise ValueError(
                        f"component dependency '{typed_scope}:{entry_id}' requires "
                        f"'{dep_scope}:{dep_id}' to be enabled"
                    )
    gpu_issues = mk8s_gpu_dependency_issues(payload)
    if gpu_issues:
        raise ValueError(gpu_issues[0])
    observability_issues = observability_dependency_issues(payload)
    if observability_issues:
        raise ValueError(observability_issues[0])

    _validate_materialized_shared_defaults(payload)

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=_ID_PATTERN,
        env_var_pattern=_ENV_VAR_PATTERN,
    )
