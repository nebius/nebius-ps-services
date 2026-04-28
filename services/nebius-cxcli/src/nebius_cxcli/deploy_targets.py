"""Helpers for explicit cluster targets and target-scoped Flux layout."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_type_id, normalize_component_token
from .components import component_entries
from .paths import ProjectPaths
from .runtime_config import to_plain_data

TARGET_REF_FIELD = "target_ref"
DEPLOY_TARGET_INSTANCE_ID_FIELD = "instance_id"


def app_chart_target_ref(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(TARGET_REF_FIELD))


def deploy_target_instance_id(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(DEPLOY_TARGET_INSTANCE_ID_FIELD))


def target_scoped_app_instance_id(app_id: Any, *, target_ref: Any) -> str:
    """Return the canonical app instance id for one chart installed on one target."""
    normalized_target_ref = normalize_component_token(target_ref)
    if normalized_target_ref:
        return normalized_target_ref
    return normalize_component_token(app_id)


def is_auto_target_scoped_app_instance_id(
    instance_id: Any,
    *,
    app_id: Any,
    target_ref: Any,
) -> bool:
    """Return true for the canonical target-bound chart instance id."""
    normalized_instance_id = normalize_component_token(instance_id)
    normalized_target_ref = normalize_component_token(target_ref)
    _ = app_id
    if not normalized_instance_id or not normalized_target_ref:
        return False
    return normalized_instance_id == normalized_target_ref


def enabled_cluster_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        return ()
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    handoff_component_ids = {
        entry.id for entry in component_entries("infra") if entry.handoff is not None
    }
    refs: list[str] = []
    seen: set[str] = set()
    for row in components:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) not in handoff_component_ids:
            continue
        instance_id = component_instance_id(row)
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        refs.append(instance_id)
    return tuple(refs)


def flux_targets_dir(paths: ProjectPaths) -> Path:
    return paths.flux_dir / "targets"


def flux_target_dir(paths: ProjectPaths, target_ref: str) -> Path:
    normalized = normalize_component_token(target_ref)
    if not normalized:
        raise ValueError("target_ref is required to resolve a target-scoped Flux directory")
    return flux_targets_dir(paths) / normalized


__all__ = [
    "DEPLOY_TARGET_INSTANCE_ID_FIELD",
    "TARGET_REF_FIELD",
    "app_chart_target_ref",
    "deploy_target_instance_id",
    "enabled_cluster_target_refs",
    "flux_target_dir",
    "flux_targets_dir",
    "is_auto_target_scoped_app_instance_id",
    "target_scoped_app_instance_id",
]
