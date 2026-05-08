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
    INSTANCE_ID_FIELD,
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
from .mk8s_gpu import mk8s_gpu_dependency_issues
from .mysterybox_eso import mysterybox_eso_dependency_issues
from .observability import observability_dependency_issues
from .runtime_config import read_path_with_catalog
from .runtime_plugin_validation import run_runtime_validation_plugins

_ROOT_KEYS = frozenset({"version", "client_info", "deploy", "infra", "apps"})
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

    supported_deploy_keys = {"observability", "targets"}
    unknown_deploy_keys = sorted(
        str(key) for key in deploy if str(key) not in supported_deploy_keys
    )
    if unknown_deploy_keys:
        raise ValueError("deploy has unsupported field(s): " + ", ".join(unknown_deploy_keys))

    _validate_observability(
        deploy.get("observability"),
        field_label="deploy.observability",
        allow_kubernetes=False,
    )

    targets = deploy.get("targets")
    if targets is not None:
        if not isinstance(targets, list):
            raise ValueError("deploy.targets must be a list")
        seen_target_refs: set[str] = set()
        for index, raw_target in enumerate(targets):
            if not isinstance(raw_target, Mapping):
                raise ValueError(f"deploy.targets[{index}] must be a mapping")
            unknown_target_keys = sorted(
                str(key)
                for key in raw_target
                if str(key) not in {INSTANCE_ID_FIELD, "observability", "secrets", "validations"}
            )
            if unknown_target_keys:
                raise ValueError(
                    f"deploy.targets[{index}] has unsupported field(s): "
                    + ", ".join(unknown_target_keys)
                )
            target_ref = _as_text(raw_target.get(INSTANCE_ID_FIELD)).lower()
            if not target_ref:
                raise ValueError(f"deploy.targets[{index}].{INSTANCE_ID_FIELD} is required")
            if not INSTANCE_ID_PATTERN.fullmatch(target_ref):
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} must use lowercase letters, digits, and hyphens"
                )
            if target_ref in seen_target_refs:
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} '{target_ref}' is duplicated"
                )
            seen_target_refs.add(target_ref)
            _validate_observability(
                raw_target.get("observability"),
                field_label=f"deploy.targets[{index}].observability",
                allow_vm=False,
            )
            _validate_deploy_target_secrets(
                raw_target.get("secrets"),
                field_label=f"deploy.targets[{index}].secrets",
            )
            target_validations = raw_target.get("validations")
            if target_validations is None:
                continue
            if not isinstance(target_validations, Mapping):
                raise ValueError(f"deploy.targets[{index}].validations must be a mapping")
            unknown_target_validation_keys = sorted(
                str(key) for key in target_validations if str(key) not in {"mk8s_gpu"}
            )
            if unknown_target_validation_keys:
                raise ValueError(
                    f"deploy.targets[{index}].validations has unsupported field(s): "
                    + ", ".join(unknown_target_validation_keys)
                )
            mk8s_gpu = target_validations.get("mk8s_gpu")
            if mk8s_gpu is not None and not isinstance(mk8s_gpu, Mapping):
                raise ValueError(f"deploy.targets[{index}].validations.mk8s_gpu must be a mapping")


def _validate_deploy_target_secrets(secrets: Any, *, field_label: str) -> None:
    if secrets is None:
        return
    if not isinstance(secrets, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    unknown_keys = sorted(str(key) for key in secrets if str(key) not in {"mysterybox"})
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    mysterybox = secrets.get("mysterybox")
    if mysterybox is None:
        return
    if not isinstance(mysterybox, Mapping):
        raise ValueError(f"{field_label}.mysterybox must be a mapping")
    supported_keys = {
        "enabled",
        "store_name",
        "api_domain",
        "credentials_secret",
        "allow_all_namespaces",
        "sync_namespaces",
    }
    unknown_mysterybox_keys = sorted(
        str(key) for key in mysterybox if str(key) not in supported_keys
    )
    if unknown_mysterybox_keys:
        raise ValueError(
            f"{field_label}.mysterybox has unsupported field(s): "
            + ", ".join(unknown_mysterybox_keys)
        )
    enabled = mysterybox.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.mysterybox.enabled must be true or false")
    if enabled is not True:
        return

    allow_all_namespaces = mysterybox.get("allow_all_namespaces")
    if allow_all_namespaces is not None and not isinstance(allow_all_namespaces, bool):
        raise ValueError(f"{field_label}.mysterybox.allow_all_namespaces must be true or false")

    for key in ("store_name", "api_domain"):
        value = _as_text(mysterybox.get(key))
        if not value:
            raise ValueError(f"{field_label}.mysterybox.{key} is required when enabled")

    _validate_mysterybox_credentials_secret(
        mysterybox.get("credentials_secret"),
        field_label=f"{field_label}.mysterybox.credentials_secret",
    )

    sync_namespaces = mysterybox.get("sync_namespaces")
    if not isinstance(sync_namespaces, list) or not sync_namespaces:
        raise ValueError(
            f"{field_label}.mysterybox.sync_namespaces must be a non-empty list of strings"
        )
    for index, namespace in enumerate(sync_namespaces):
        if not isinstance(namespace, str) or not _ID_PATTERN.fullmatch(namespace):
            raise ValueError(
                f"{field_label}.mysterybox.sync_namespaces[{index}] must be a Kubernetes namespace name"
            )


def _validate_mysterybox_credentials_secret(value: Any, *, field_label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"name", "namespace", "key"}
    unknown_keys = sorted(str(key) for key in value if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    for key in ("name", "namespace", "key"):
        current = _as_text(value.get(key))
        if not current:
            raise ValueError(f"{field_label}.{key} is required")
        if key != "key" and not _ID_PATTERN.fullmatch(current):
            raise ValueError(f"{field_label}.{key} must be a Kubernetes name")


def _validate_observability(
    observability: Any,
    *,
    field_label: str,
    allow_vm: bool = True,
    allow_kubernetes: bool = True,
) -> None:
    if observability is None:
        return
    if not isinstance(observability, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled"}
    if allow_kubernetes:
        supported_keys.add("kubernetes")
    if allow_vm:
        supported_keys.add("vm")
    unknown_keys = sorted(str(key) for key in observability if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    enabled = observability.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.enabled must be true or false")

    kubernetes = observability.get("kubernetes")
    if kubernetes is not None:
        if not isinstance(kubernetes, Mapping):
            raise ValueError(f"{field_label}.kubernetes must be a mapping")
        supported_kubernetes_keys = {"logs", "metrics", "traces"}
        unknown_kubernetes_keys = sorted(
            str(key) for key in kubernetes if str(key) not in supported_kubernetes_keys
        )
        if unknown_kubernetes_keys:
            raise ValueError(
                f"{field_label}.kubernetes has unsupported field(s): "
                + ", ".join(unknown_kubernetes_keys)
            )

        logs = kubernetes.get("logs")
        if logs is not None:
            if not isinstance(logs, Mapping):
                raise ValueError(f"{field_label}.kubernetes.logs must be a mapping")
            supported_log_keys = {"enabled", "collect_agent_logs", "excluded_namespaces"}
            unknown_log_keys = sorted(
                str(key) for key in logs if str(key) not in supported_log_keys
            )
            if unknown_log_keys:
                raise ValueError(
                    f"{field_label}.kubernetes.logs has unsupported field(s): "
                    + ", ".join(unknown_log_keys)
                )
            for field in ("enabled", "collect_agent_logs"):
                value = logs.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f"{field_label}.kubernetes.logs.{field} must be true or false")
            excluded_namespaces = logs.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    f"{field_label}.kubernetes.logs.excluded_namespaces must be a list of strings"
                )

        metrics = kubernetes.get("metrics")
        if metrics is not None:
            if not isinstance(metrics, Mapping):
                raise ValueError(f"{field_label}.kubernetes.metrics must be a mapping")
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
                    f"{field_label}.kubernetes.metrics has unsupported field(s): "
                    + ", ".join(unknown_metric_keys)
                )
            for field in ("enabled", "collect_agent_metrics", "collect_k8s_cluster_metrics"):
                value = metrics.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(
                        f"{field_label}.kubernetes.metrics.{field} must be true or false"
                    )
            excluded_namespaces = metrics.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    f"{field_label}.kubernetes.metrics.excluded_namespaces must be a list of strings"
                )

        traces = kubernetes.get("traces")
        if traces is not None:
            if not isinstance(traces, Mapping):
                raise ValueError(f"{field_label}.kubernetes.traces must be a mapping")
            supported_trace_keys = {"enabled"}
            unknown_trace_keys = sorted(
                str(key) for key in traces if str(key) not in supported_trace_keys
            )
            if unknown_trace_keys:
                raise ValueError(
                    f"{field_label}.kubernetes.traces has unsupported field(s): "
                    + ", ".join(unknown_trace_keys)
                )
            value = traces.get("enabled")
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_label}.kubernetes.traces.enabled must be true or false")

    vm = observability.get("vm")
    if vm is None:
        return
    if not isinstance(vm, Mapping):
        raise ValueError(f"{field_label}.vm must be a mapping")
    supported_vm_keys = {"logs", "collector"}
    unknown_vm_keys = sorted(str(key) for key in vm if str(key) not in supported_vm_keys)
    if unknown_vm_keys:
        raise ValueError(
            f"{field_label}.vm has unsupported field(s): " + ", ".join(unknown_vm_keys)
        )

    logs = vm.get("logs")
    if logs is None:
        return
    if not isinstance(logs, Mapping):
        raise ValueError(f"{field_label}.vm.logs must be a mapping")
    supported_vm_log_keys = {"enabled", "systemd_units"}
    unknown_vm_log_keys = sorted(str(key) for key in logs if str(key) not in supported_vm_log_keys)
    if unknown_vm_log_keys:
        raise ValueError(
            f"{field_label}.vm.logs has unsupported field(s): " + ", ".join(unknown_vm_log_keys)
        )
    enabled = logs.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.vm.logs.enabled must be true or false")
    systemd_units = logs.get("systemd_units")
    if systemd_units is not None and (
        not isinstance(systemd_units, list)
        or any(not isinstance(item, str) for item in systemd_units)
    ):
        raise ValueError(f"{field_label}.vm.logs.systemd_units must be a list of strings")

    collector = vm.get("collector")
    if collector is None:
        return
    if not isinstance(collector, Mapping):
        raise ValueError(f"{field_label}.vm.collector must be a mapping")
    supported_collector_keys = {"enabled", "logs", "metrics"}
    unknown_collector_keys = sorted(
        str(key) for key in collector if str(key) not in supported_collector_keys
    )
    if unknown_collector_keys:
        raise ValueError(
            f"{field_label}.vm.collector has unsupported field(s): "
            + ", ".join(unknown_collector_keys)
        )
    collector_enabled = collector.get("enabled")
    if collector_enabled is not None and not isinstance(collector_enabled, bool):
        raise ValueError(f"{field_label}.vm.collector.enabled must be true or false")

    collector_logs = collector.get("logs")
    if collector_logs is not None:
        if not isinstance(collector_logs, Mapping):
            raise ValueError(f"{field_label}.vm.collector.logs must be a mapping")
        supported_collector_log_keys = {"enabled", "systemd_units"}
        unknown_collector_log_keys = sorted(
            str(key) for key in collector_logs if str(key) not in supported_collector_log_keys
        )
        if unknown_collector_log_keys:
            raise ValueError(
                f"{field_label}.vm.collector.logs has unsupported field(s): "
                + ", ".join(unknown_collector_log_keys)
            )
        collector_logs_enabled = collector_logs.get("enabled")
        if collector_logs_enabled is not None and not isinstance(collector_logs_enabled, bool):
            raise ValueError(f"{field_label}.vm.collector.logs.enabled must be true or false")
        collector_units = collector_logs.get("systemd_units")
        if collector_units is not None and (
            not isinstance(collector_units, list)
            or any(not isinstance(item, str) for item in collector_units)
        ):
            raise ValueError(
                f"{field_label}.vm.collector.logs.systemd_units must be a list of strings"
            )

    collector_metrics = collector.get("metrics")
    if collector_metrics is not None:
        if not isinstance(collector_metrics, Mapping):
            raise ValueError(f"{field_label}.vm.collector.metrics must be a mapping")
        supported_collector_metric_keys = {"enabled"}
        unknown_collector_metric_keys = sorted(
            str(key) for key in collector_metrics if str(key) not in supported_collector_metric_keys
        )
        if unknown_collector_metric_keys:
            raise ValueError(
                f"{field_label}.vm.collector.metrics has unsupported field(s): "
                + ", ".join(unknown_collector_metric_keys)
            )
        collector_metrics_enabled = collector_metrics.get("enabled")
        if collector_metrics_enabled is not None and not isinstance(
            collector_metrics_enabled, bool
        ):
            raise ValueError(f"{field_label}.vm.collector.metrics.enabled must be true or false")


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
    cluster_target_refs: set[str] = set()
    enabled_vm_instance_ids: set[str] = set()
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
        if not raw_instance_id:
            raise ValueError(f"infra.components[{index}].instance_id is required")
        if not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"infra.components[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = raw_instance_id
        if instance_id in seen_infra_instance_ids:
            raise ValueError(f"infra.components[{index}].instance_id '{instance_id}' is duplicated")
        seen_infra_instance_ids.add(instance_id)

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
                "use deploy.targets[].validations.mk8s_gpu.*"
            )
        entry = infra_lookup.get(component_id)
        if (
            entry is not None
            and entry.handoff is not None
            and bool(raw_component.get("enabled", False))
        ):
            cluster_target_refs.add(instance_id)
        if component_id == "vm" and bool(raw_component.get("enabled", False)):
            enabled_vm_instance_ids.add(instance_id)

    deploy = payload.get("deploy")
    deploy_targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if isinstance(deploy_targets, list):
        if deploy_targets and not cluster_target_refs:
            raise ValueError("deploy.targets requires at least one enabled cluster target")
        for index, raw_target in enumerate(deploy_targets):
            if not isinstance(raw_target, Mapping):
                continue
            target_ref = _as_text(raw_target.get(INSTANCE_ID_FIELD)).lower()
            if target_ref and cluster_target_refs and target_ref not in cluster_target_refs:
                available = ", ".join(sorted(cluster_target_refs)) or "(none)"
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} must reference one of the enabled cluster targets: {available}"
                )
    root_observability = deploy.get("observability") if isinstance(deploy, Mapping) else None
    if root_observability is not None and not enabled_vm_instance_ids:
        raise ValueError(
            "deploy.observability is only supported for enabled infra:vm components; "
            "use deploy.targets[].observability for MK8s targets"
        )

    seen_app_instance_keys: set[tuple[str, str]] = set()
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
                "profile",
                "version",
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
        if not raw_instance_id:
            raise ValueError(f"apps.charts[{index}].instance_id is required")
        if not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"apps.charts[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = raw_instance_id
        instance_key = (chart_id, instance_id)
        if instance_key in seen_app_instance_keys:
            raise ValueError(
                f"apps.charts[{index}] duplicates chart '{chart_id}' instance_id '{instance_id}'"
            )
        seen_app_instance_keys.add(instance_key)

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
        for key in ("repo", "profile", "version", "namespace"):
            value = raw_chart.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"apps.charts[{index}].{key} must be a string when set")
        release_name = raw_chart.get("release-name")
        if release_name is not None and not isinstance(release_name, str):
            raise ValueError(f"apps.charts[{index}].release-name must be a string when set")
        if not isinstance(raw_chart.get("values"), Mapping):
            raise ValueError(f"apps.charts[{index}].values must be a mapping")
        if (
            bool(raw_chart.get("enabled", False))
            and cluster_target_refs
            and instance_id not in cluster_target_refs
        ):
            available = ", ".join(sorted(cluster_target_refs))
            raise ValueError(
                f"apps.charts[{index}].instance_id must reference one of the enabled cluster targets: {available}"
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
    mysterybox_issues = mysterybox_eso_dependency_issues(payload)
    if mysterybox_issues:
        raise ValueError(mysterybox_issues[0])

    _validate_materialized_shared_defaults(payload)

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=_ID_PATTERN,
        env_var_pattern=_ENV_VAR_PATTERN,
    )
