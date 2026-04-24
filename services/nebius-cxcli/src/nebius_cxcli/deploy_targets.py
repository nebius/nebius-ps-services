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


def app_chart_target_ref(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(TARGET_REF_FIELD))


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
    "TARGET_REF_FIELD",
    "app_chart_target_ref",
    "enabled_cluster_target_refs",
    "flux_target_dir",
    "flux_targets_dir",
]
