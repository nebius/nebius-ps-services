"""Runtime validation for config payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .components import ComponentScope, component_entries, component_lookup, parse_dependency_ref
from .runtime_plugin_validation import run_runtime_validation_plugins

_ROOT_KEYS = frozenset({"version", "client_info", "infra", "apps", "component_sources"})
_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_SECTION_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return default
        candidates = (segment, segment.replace("-", "_"), segment.replace("_", "-"))
        matched = None
        for candidate in candidates:
            if candidate in current:
                matched = current[candidate]
                break
        if matched is None:
            return default
        current = matched
    return current


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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
    releases = apps.get("releases")
    if not isinstance(releases, list):
        return selected
    for item in releases:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        release_id = _as_text(item.get("id")).lower()
        if release_id:
            selected.add(release_id)
    return selected


def _expected_app_section(config_path: str) -> str | None:
    parts = config_path.split(".")
    if len(parts) < 3:
        return None
    if parts[0] != "apps":
        return None
    return parts[1]


def validate_dynamic_payload_structure(payload: Mapping[str, Any]) -> None:
    """Validate dynamic model sections (`infra.components[]`, `apps.releases[]`)."""
    infra = payload.get("infra")
    apps = payload.get("apps")
    if not isinstance(infra, Mapping) or not isinstance(apps, Mapping):
        return

    infra_components = infra.get("components")
    apps_releases = apps.get("releases")
    if infra_components is None and apps_releases is None:
        return

    if not isinstance(infra_components, list):
        raise ValueError("infra.components must be a list in dynamic config mode")
    if not isinstance(apps_releases, list):
        raise ValueError("apps.releases must be a list in dynamic config mode")

    app_lookup = component_lookup("apps")
    seen_infra_ids: set[str] = set()
    for index, raw_component in enumerate(infra_components):
        if not isinstance(raw_component, Mapping):
            raise ValueError(f"infra.components[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key) for key in raw_component if str(key) not in {"id", "enabled", "source", "version", "inputs"}
        )
        if unknown_keys:
            raise ValueError(
                f"infra.components[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        component_id = _as_text(raw_component.get("id")).lower()
        if not component_id:
            raise ValueError(f"infra.components[{index}].id is required")
        if not _ID_PATTERN.fullmatch(component_id):
            raise ValueError(
                f"infra.components[{index}].id must use lowercase letters, digits, and hyphens"
            )
        if component_id in seen_infra_ids:
            raise ValueError(f"infra.components[{index}].id '{component_id}' is duplicated")
        seen_infra_ids.add(component_id)

        if not isinstance(raw_component.get("enabled"), bool):
            raise ValueError(f"infra.components[{index}].enabled must be true or false")
        source_value = raw_component.get("source")
        if source_value is not None and not isinstance(source_value, str):
            raise ValueError(f"infra.components[{index}].source must be a string when set")
        version_value = raw_component.get("version")
        if version_value is not None and not isinstance(version_value, str):
            raise ValueError(f"infra.components[{index}].version must be a string when set")
        if not isinstance(raw_component.get("inputs"), Mapping):
            raise ValueError(f"infra.components[{index}].inputs must be a mapping")
        if isinstance(raw_component.get("inputs"), Mapping) and "module" in raw_component.get("inputs", {}):
            raise ValueError(
                f"infra.components[{index}].inputs.module is not supported; "
                "set module source at infra.components[].source and module vars directly under infra.components[].inputs"
            )

    seen_app_ids: set[str] = set()
    for index, raw_release in enumerate(apps_releases):
        if not isinstance(raw_release, Mapping):
            raise ValueError(f"apps.releases[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key) for key in raw_release if str(key) not in {"id", "section", "enabled", "values"}
        )
        if unknown_keys:
            raise ValueError(
                f"apps.releases[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        release_id = _as_text(raw_release.get("id")).lower()
        if not release_id:
            raise ValueError(f"apps.releases[{index}].id is required")
        if not _ID_PATTERN.fullmatch(release_id):
            raise ValueError(
                f"apps.releases[{index}].id must use lowercase letters, digits, and hyphens"
            )
        if release_id in seen_app_ids:
            raise ValueError(f"apps.releases[{index}].id '{release_id}' is duplicated")
        seen_app_ids.add(release_id)

        entry = app_lookup.get(release_id)

        section = _as_text(raw_release.get("section")).lower()
        if section and not _SECTION_PATTERN.fullmatch(section):
            raise ValueError(
                f"apps.releases[{index}].section must use lowercase letters, digits, and hyphens"
            )
        expected_section = _expected_app_section(entry.config_path) if entry else None
        if section and expected_section and section != expected_section:
            raise ValueError(
                f"apps.releases[{index}].section must be '{expected_section}' for release '{release_id}'"
            )

        if not isinstance(raw_release.get("enabled"), bool):
            raise ValueError(f"apps.releases[{index}].enabled must be true or false")
        if not isinstance(raw_release.get("values"), Mapping):
            raise ValueError(f"apps.releases[{index}].values must be a mapping")


def validate_runtime_payload(payload: Mapping[str, Any]) -> None:
    """Validate config payload with runtime checks."""
    if not isinstance(payload, Mapping):
        raise ValueError("config.yaml root must be a mapping")

    unknown_root = sorted(key for key in payload if key not in _ROOT_KEYS)
    if unknown_root:
        raise ValueError(f"unknown field(s) at root: {', '.join(unknown_root)}")

    if _as_text(payload.get("version")) not in {"", "v1"}:
        raise ValueError("version must be 'v1'")

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

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=_ID_PATTERN,
        env_var_pattern=_ENV_VAR_PATTERN,
    )
