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
    return isinstance(infra.get("components"), list) or isinstance(apps.get("releases"), list)


def _normalize_section(value: Any, *, default: str = "workloads") -> str:
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
            "'apps.releases[]'"
        )
    return _deep_copy(dict(payload))


def to_runtime_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize payload into runtime shape expected by render/ops."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config payload must use dynamic model with 'infra.components[]' and "
            "'apps.releases[]'"
        )

    dynamic_payload = _deep_copy(dict(payload))
    runtime: dict[str, Any] = {
        "version": dynamic_payload.get("version", "v1"),
        "client_info": _deep_copy(dynamic_payload.get("client_info", {})),
        "infra": {},
        "apps": {},
    }
    if isinstance(dynamic_payload.get("component_sources"), Mapping):
        runtime["component_sources"] = _deep_copy(dynamic_payload.get("component_sources", {}))

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
            raw_releases = apps_source.get("releases")
            if isinstance(raw_releases, list):
                runtime_apps["releases"] = _deep_copy(raw_releases)
            for item in apps_source.get("releases", []):  # type: ignore[union-attr]
                if not isinstance(item, Mapping):
                    continue
                release_id = str(item.get("id", "")).strip().lower()
                if not release_id:
                    continue
                section = _normalize_section(item.get("section"))
                enabled = bool(item.get("enabled", False))
                values = item.get("values", {})
                if not isinstance(values, Mapping):
                    values = {}
                section_node = runtime_apps.get(section)
                if not isinstance(section_node, dict):
                    section_node = {}
                    runtime_apps[section] = section_node
                section_node[release_id.replace("-", "_")] = {
                    "enabled": enabled,
                    **_deep_copy(dict(values)),
                }
    return runtime
