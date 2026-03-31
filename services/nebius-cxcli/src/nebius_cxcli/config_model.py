"""Config model adapters between runtime and dynamic payload shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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


def to_dynamic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return dynamic payload copy. Static payloads are rejected."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config payload must use dynamic model with 'infra.components[]' and "
            "'apps.charts[]'"
        )
    return _deep_copy(dict(payload))


def to_runtime_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize payload into runtime shape expected by render/ops."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config payload must use dynamic model with 'infra.components[]' and "
            "'apps.charts[]'"
        )

    dynamic_payload = _deep_copy(dict(payload))
    runtime: dict[str, Any] = {
        "version": dynamic_payload.get("version", "v1"),
        "client_info": _deep_copy(dynamic_payload.get("client_info", {})),
        "infra": {},
        "apps": {},
    }

    infra_source = dynamic_payload.get("infra", {})
    if isinstance(infra_source, Mapping):
        runtime_infra = runtime["infra"]
        if isinstance(runtime_infra, dict):
            raw_components = infra_source.get("components")
            if isinstance(raw_components, list):
                runtime_infra["components"] = _deep_copy(raw_components)
            for key, value in infra_source.items():
                if key == "components":
                    continue
                runtime_infra[key] = _deep_copy(value)
            for item in infra_source.get("components", []):  # type: ignore[union-attr]
                if not isinstance(item, Mapping):
                    continue
                component_id = str(item.get("id", "")).strip().lower()
                if not component_id:
                    continue
                enabled = bool(item.get("enabled", False))
                inputs = item.get("inputs", {})
                if not isinstance(inputs, Mapping):
                    inputs = {}
                runtime_infra[component_id.replace("-", "_")] = {
                    "enabled": enabled,
                    **_deep_copy(dict(inputs)),
                }

    apps_source = dynamic_payload.get("apps", {})
    if isinstance(apps_source, Mapping):
        runtime_apps = runtime["apps"]
        if isinstance(runtime_apps, dict):
            raw_charts = apps_source.get("charts")
            if isinstance(raw_charts, list):
                runtime_apps["charts"] = _deep_copy(raw_charts)
            for item in apps_source.get("charts", []):  # type: ignore[union-attr]
                if not isinstance(item, Mapping):
                    continue
                chart_id = str(item.get("id", "")).strip().lower()
                if not chart_id:
                    continue
                group = _normalize_group(item.get("group"))
                enabled = bool(item.get("enabled", False))
                values = item.get("values", {})
                if not isinstance(values, Mapping):
                    values = {}
                group_node = runtime_apps.get(group)
                if not isinstance(group_node, dict):
                    group_node = {}
                    runtime_apps[group] = group_node
                group_node[chart_id.replace("-", "_")] = {
                    "enabled": enabled,
                    "repo": str(item.get("repo", "")).strip(),
                    "version": str(item.get("version", "")).strip(),
                    "namespace": str(item.get("namespace", "")).strip(),
                    "release_name": str(item.get("release-name", chart_id)).strip() or chart_id,
                    **_deep_copy(dict(values)),
                }
    return runtime
