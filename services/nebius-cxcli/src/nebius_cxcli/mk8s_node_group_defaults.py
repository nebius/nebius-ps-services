"""MK8s node-group default helper pruning."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .components import ComponentEntry, component_entries
from .deploy_targets import app_chart_target_ref
from .runtime_introspection import module_variables

_SOPERATOR_INSTALL_MODE_PRODUCTION = "production-cluster"
_LOGGER = logging.getLogger(__name__)


def _normalize_leaf_name(value: str) -> str:
    return value.strip().replace("-", "_")


def _non_empty_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _profile_mapping(profile: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = profile.get(key)
    return value if isinstance(value, Mapping) else {}


def _profile_list(profile: Mapping[str, Any], key: str) -> list[Any]:
    value = profile.get(key)
    return list(value) if isinstance(value, list) else []


def _entry_metadata_source(entry: ComponentEntry | None) -> str:
    if entry is None:
        return ""
    return str(entry.metadata_source or entry.source or "").strip()


def _entry_declares_module_input(entry: ComponentEntry | None, input_name: str) -> bool:
    source = _entry_metadata_source(entry)
    if not source:
        return False
    try:
        module_input_names = {
            _normalize_leaf_name(variable.name) for variable in module_variables(source)
        }
    except (OSError, RuntimeError, ValueError) as exc:
        _LOGGER.warning(
            "Unable to inspect module inputs for %s while pruning MK8s node-group defaults: %s",
            source,
            exc,
        )
        return True
    return _normalize_leaf_name(input_name) in module_input_names


def _entry_seeds_node_group_defaults(entry: ComponentEntry | None) -> bool:
    if entry is None:
        return False
    return any(
        str(default.target_path).startswith("inputs.node_group_defaults")
        for default in entry.defaults
    )


def _soperator_production_target_refs(payload: dict[str, Any]) -> set[str]:
    apps = payload.get("apps")
    charts = apps.get("charts") if isinstance(apps, dict) else None
    if not isinstance(charts, list):
        return set()
    targets: set[str] = set()
    for row in charts:
        if not isinstance(row, dict):
            continue
        if component_type_id(row) != "soperator" or row.get("enabled") is False:
            continue
        install_mode = str(row.get("install_mode") or _SOPERATOR_INSTALL_MODE_PRODUCTION)
        if install_mode != _SOPERATOR_INSTALL_MODE_PRODUCTION:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_ref:
            targets.add(target_ref)
    return targets


def _soperator_nodesets_profiles() -> tuple[str, Mapping[str, Mapping[str, Any]]]:
    from .component_sources import helm_chart_source_by_id

    chart = helm_chart_source_by_id("soperator")
    settings = getattr(chart, "soperator_nodesets", None)
    default = _non_empty_text(getattr(settings, "default", "")) if settings is not None else ""
    profiles = getattr(settings, "profiles", {}) if settings is not None else {}
    if not isinstance(profiles, Mapping):
        profiles = {}
    return default, profiles


def _soperator_profile_uses_gpu(profile: Mapping[str, Any]) -> bool:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    for group in _profile_mapping(mk8s_profile, "node_groups").values():
        if isinstance(group, Mapping) and bool(group.get("gpu", False)):
            return True
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        node_group = raw_worker.get("node_group")
        if isinstance(node_group, Mapping) and bool(node_group.get("gpu", False)):
            return True
        if raw_worker.get("gpu", True) is not False:
            return True
    return False


def _soperator_production_profile_by_target(
    payload: dict[str, Any],
) -> dict[str, Mapping[str, Any]]:
    apps = payload.get("apps")
    charts = apps.get("charts") if isinstance(apps, dict) else None
    if not isinstance(charts, list):
        return {}

    default_profile, profiles = _soperator_nodesets_profiles()
    selected: dict[str, Mapping[str, Any]] = {}
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if component_type_id(row) != "soperator" or row.get("enabled") is False:
            continue
        install_mode = str(row.get("install_mode") or _SOPERATOR_INSTALL_MODE_PRODUCTION)
        if install_mode != _SOPERATOR_INSTALL_MODE_PRODUCTION:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if not target_ref:
            continue
        profile_name = _non_empty_text(row.get("profile")) or default_profile
        if not profile_name:
            selected[target_ref] = {}
            continue
        profile = profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            available = ", ".join(sorted(str(name) for name in profiles)) or "(none)"
            raise ValueError(
                f"apps.charts[{target_ref}].profile references unknown Soperator "
                f"nodesets profile '{profile_name}'. Available profiles: {available}"
            )
        selected[target_ref] = profile
    return selected


def _prune_soperator_inactive_node_group_default_scopes(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
) -> bool:
    if not profile or _soperator_profile_uses_gpu(profile):
        return False
    defaults = inputs.get("node_group_defaults")
    if not isinstance(defaults, dict) or "gpu" not in defaults:
        return False
    defaults.pop("gpu", None)
    if not defaults:
        inputs.pop("node_group_defaults", None)
    return True


def _infra_entry_by_id(
    infra_entries: Sequence[ComponentEntry] | None,
) -> dict[str, ComponentEntry]:
    if infra_entries is not None:
        return {entry.id: entry for entry in infra_entries}
    try:
        return {entry.id: entry for entry in component_entries("infra")}
    except (OSError, RuntimeError, ValueError) as exc:
        _LOGGER.warning(
            "Unable to load infra component sources while pruning MK8s node-group defaults: %s",
            exc,
        )
        return {}


def prune_inactive_mk8s_node_group_defaults(
    payload: dict[str, Any],
    *,
    infra_entries: Sequence[ComponentEntry] | None = None,
) -> bool:
    """Remove bundled-MK8s profile helpers when no production profile consumes them."""
    entry_by_id = _infra_entry_by_id(infra_entries)
    if not entry_by_id:
        return False
    production_soperator_targets = _soperator_production_target_refs(payload)
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False

    changed = False
    production_profile_by_target = _soperator_production_profile_by_target(payload)
    for row in components:
        if not isinstance(row, dict) or component_type_id(row) != "mk8s":
            continue
        target_ref = component_instance_id(row)
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if target_ref in production_soperator_targets:
            profile = production_profile_by_target.get(target_ref)
            if isinstance(profile, Mapping) and _prune_soperator_inactive_node_group_default_scopes(
                inputs=inputs,
                profile=profile,
            ):
                changed = True
            continue
        entry = entry_by_id.get("mk8s")
        if _entry_declares_module_input(
            entry,
            "node_group_defaults",
        ) or _entry_seeds_node_group_defaults(entry):
            continue
        if "node_group_defaults" in inputs:
            inputs.pop("node_group_defaults", None)
            changed = True
    return changed
