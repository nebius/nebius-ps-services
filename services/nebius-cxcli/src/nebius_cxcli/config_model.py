"""Config model adapters between runtime and dynamic payload shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .component_instances import component_instance_id, ensure_component_instance_id
from .deploy_targets import TARGET_REF_FIELD, enabled_cluster_target_refs


def _deep_copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _deep_copy(item) for key, item in value.items()}
    return value


def is_dynamic_payload(payload: Mapping[str, Any]) -> bool:
    infra = payload.get("infra")
    apps = payload.get("apps")
    if not isinstance(infra, Mapping) or not isinstance(apps, Mapping):
        return False
    return isinstance(infra.get("components"), list) or isinstance(apps.get("charts"), list)


def _normalize_group(value: Any, *, default: str = "workloads") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    token = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)
    token = "-".join(part for part in token.split("-") if part)
    return token or default


def _runtime_component_key(component_id: str, instance_id: str) -> str:
    normalized_component = str(component_id or "").strip().lower().replace("-", "_")
    normalized_instance = str(instance_id or "").strip().lower().replace("-", "_")
    if not normalized_instance or normalized_instance == normalized_component:
        return normalized_component
    if not normalized_component:
        return normalized_instance
    return f"{normalized_component}_{normalized_instance}"


def to_dynamic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return dynamic payload copy. Static payloads are rejected."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config payload must use dynamic model with 'infra.components[]' and 'apps.charts[]'"
        )
    return _deep_copy(dict(payload))


def to_runtime_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize payload into runtime shape expected by render/ops."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config payload must use dynamic model with 'infra.components[]' and 'apps.charts[]'"
        )

    dynamic_payload = _deep_copy(dict(payload))
    runtime: dict[str, Any] = {
        "version": dynamic_payload.get("version", "v1"),
        "client_info": _deep_copy(dynamic_payload.get("client_info", {})),
        "deploy": _deep_copy(dynamic_payload.get("deploy", {})),
        "infra": {},
        "apps": {},
    }

    infra_source = dynamic_payload.get("infra", {})
    if isinstance(infra_source, Mapping):
        runtime_infra = runtime["infra"]
        if isinstance(runtime_infra, dict):
            raw_components = infra_source.get("components")
            if isinstance(raw_components, list):
                normalized_components: list[dict[str, Any]] = []
                for item in raw_components:
                    if not isinstance(item, Mapping):
                        continue
                    copied_item = _deep_copy(dict(item))
                    if not isinstance(copied_item, dict):
                        continue
                    component_id = str(copied_item.get("id", "")).strip().lower()
                    if component_id:
                        ensure_component_instance_id(copied_item, default_component_id=component_id)
                    normalized_components.append(copied_item)
                runtime_infra["components"] = normalized_components
            for key, value in infra_source.items():
                if key == "components":
                    continue
                runtime_infra[key] = _deep_copy(value)
            for item in infra_source.get("components", []):  # type: ignore[union-attr]
                if not isinstance(item, Mapping):
                    continue
                copied_item = _deep_copy(dict(item))
                if not isinstance(copied_item, dict):
                    continue
                component_id = str(copied_item.get("id", "")).strip().lower()
                if not component_id:
                    continue
                ensure_component_instance_id(copied_item, default_component_id=component_id)
                enabled = bool(copied_item.get("enabled", False))
                inputs = copied_item.get("inputs", {})
                if not isinstance(inputs, Mapping):
                    inputs = {}
                runtime_infra[component_instance_id(copied_item).replace("-", "_")] = {
                    "enabled": enabled,
                    **_deep_copy(dict(inputs)),
                }

    apps_source = dynamic_payload.get("apps", {})
    if isinstance(apps_source, Mapping):
        runtime_apps = runtime["apps"]
        if isinstance(runtime_apps, dict):
            cluster_target_refs = set(enabled_cluster_target_refs(dynamic_payload))
            raw_charts = apps_source.get("charts")
            if isinstance(raw_charts, list):
                normalized_charts: list[dict[str, Any]] = []
                for item in raw_charts:
                    if not isinstance(item, Mapping):
                        continue
                    copied_item = _deep_copy(dict(item))
                    if not isinstance(copied_item, dict):
                        continue
                    chart_id = str(copied_item.get("id", "")).strip().lower()
                    if chart_id:
                        ensure_component_instance_id(copied_item, default_component_id=chart_id)
                        instance_id = component_instance_id(copied_item)
                        if instance_id in cluster_target_refs:
                            copied_item[TARGET_REF_FIELD] = instance_id
                    normalized_charts.append(copied_item)
                runtime_apps["charts"] = normalized_charts
            for item in apps_source.get("charts", []):  # type: ignore[union-attr]
                if not isinstance(item, Mapping):
                    continue
                copied_item = _deep_copy(dict(item))
                if not isinstance(copied_item, dict):
                    continue
                chart_id = str(copied_item.get("id", "")).strip().lower()
                if not chart_id:
                    continue
                ensure_component_instance_id(copied_item, default_component_id=chart_id)
                instance_id = component_instance_id(copied_item)
                target_ref = instance_id if instance_id in cluster_target_refs else ""
                group = _normalize_group(copied_item.get("group"))
                enabled = bool(copied_item.get("enabled", False))
                values = copied_item.get("values", {})
                if not isinstance(values, Mapping):
                    values = {}
                group_node = runtime_apps.get(group)
                if not isinstance(group_node, dict):
                    group_node = {}
                    runtime_apps[group] = group_node
                group_node[_runtime_component_key(chart_id, instance_id)] = {
                    "enabled": enabled,
                    "repo": str(copied_item.get("repo", "")).strip(),
                    "version": str(copied_item.get("version", "")).strip(),
                    TARGET_REF_FIELD: target_ref,
                    "namespace": str(copied_item.get("namespace", "")).strip(),
                    "release_name": str(copied_item.get("release-name", instance_id)).strip()
                    or instance_id,
                    **_deep_copy(dict(values)),
                }
    return runtime
