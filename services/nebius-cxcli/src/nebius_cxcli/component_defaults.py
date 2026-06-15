"""Helpers for source-defined component default values."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .components import ComponentEntry
from .runtime_config import read_path_with_catalog


def _split_path(path: str) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in str(path).split(".") if segment.strip())


def _path_segment_candidates(segment: str) -> tuple[str, str, str]:
    return (segment, segment.replace("-", "_"), segment.replace("_", "-"))


def _has_material_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def read_component_path(component_node: Mapping[str, Any], path: str) -> Any:
    current: Any = component_node
    for segment in _split_path(path):
        if not isinstance(current, Mapping):
            return None
        matched = next(
            (candidate for candidate in _path_segment_candidates(segment) if candidate in current),
            None,
        )
        if matched is None:
            return None
        current = current[matched]
    return current


def set_component_path(component_node: dict[str, Any], path: str, value: Any) -> None:
    segments = _split_path(path)
    if not segments:
        return
    current = component_node
    for segment in segments[:-1]:
        matched = next(
            (candidate for candidate in _path_segment_candidates(segment) if candidate in current),
            segment,
        )
        next_value = current.get(matched)
        if not isinstance(next_value, dict):
            next_value = {}
            current[matched] = next_value
        current = next_value
    leaf = next(
        (candidate for candidate in _path_segment_candidates(segments[-1]) if candidate in current),
        segments[-1],
    )
    current[leaf] = copy.deepcopy(value)


def component_path_has_material_value(value: Any) -> bool:
    return _has_material_value(value)


def default_target_paths(entry: ComponentEntry) -> set[str]:
    return {default.target_path for default in entry.defaults}


def literal_default_target_paths(entry: ComponentEntry) -> set[str]:
    return {default.target_path for default in entry.defaults if default.kind == "literal"}


def shared_default_target_paths(entry: ComponentEntry) -> set[str]:
    return {default.target_path for default in entry.defaults if default.kind == "shared"}


def default_input_leaf_names(entry: ComponentEntry) -> set[str]:
    result: set[str] = set()
    for default in entry.defaults:
        segments = _split_path(default.target_path)
        if len(segments) >= 2 and segments[0] == "inputs":
            result.add(segments[1].strip().lower().replace("-", "_"))
    return result


def literal_default_input_leaf_names(entry: ComponentEntry) -> set[str]:
    result: set[str] = set()
    for default in entry.defaults:
        if default.kind != "literal":
            continue
        segments = _split_path(default.target_path)
        if len(segments) >= 2 and segments[0] == "inputs":
            result.add(segments[1].strip().lower().replace("-", "_"))
    return result


def shared_default_input_sources(entry: ComponentEntry) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for default in entry.defaults:
        if default.kind != "shared":
            continue
        segments = _split_path(default.target_path)
        if len(segments) != 2 or segments[0] != "inputs":
            continue
        bindings[segments[1].strip().lower().replace("-", "_")] = default.source_path
    return bindings


def managed_default_payload_paths(
    component_path: tuple[str | int, ...],
    entry: ComponentEntry,
) -> set[tuple[str | int, ...]]:
    result: set[tuple[str | int, ...]] = set()
    for target_path in default_target_paths(entry):
        segments = _split_path(target_path)
        if segments:
            result.add(component_path + tuple(segments))
    return result


def shared_default_payload_paths(
    component_path: tuple[str | int, ...],
    entry: ComponentEntry,
) -> set[tuple[str | int, ...]]:
    result: set[tuple[str | int, ...]] = set()
    for target_path in shared_default_target_paths(entry):
        segments = _split_path(target_path)
        if segments:
            result.add(component_path + tuple(segments))
    return result


def resolve_component_defaults(
    *,
    payload: Mapping[str, Any] | None = None,
    component_node: dict[str, Any] | Any,
    entry: ComponentEntry,
    preserve_existing_literal: bool = True,
    preserve_existing_shared: bool = False,
    include_literal: bool = True,
    include_shared: bool = False,
) -> dict[str, Any]:
    resolved = copy.deepcopy(dict(component_node)) if isinstance(component_node, dict) else {}
    for default in entry.defaults:
        if default.kind == "literal":
            if not include_literal:
                continue
            existing_value = read_component_path(resolved, default.target_path)
            if preserve_existing_literal and _has_material_value(existing_value):
                continue
            set_component_path(resolved, default.target_path, default.value)
            continue
        if default.kind != "shared" or not include_shared or payload is None:
            continue
        source_value = read_path_with_catalog(payload, default.source_path)
        if not _has_material_value(source_value):
            continue
        existing_value = read_component_path(resolved, default.target_path)
        if preserve_existing_shared and _has_material_value(existing_value):
            continue
        set_component_path(resolved, default.target_path, source_value)
    return resolved


def _materialize_scope_shared_defaults(
    *,
    payload: dict[str, Any],
    section: str,
    collection: str,
    entries: tuple[ComponentEntry, ...],
) -> bool:
    section_node = payload.get(section)
    if not isinstance(section_node, dict):
        return False
    rows = section_node.get(collection)
    if not isinstance(rows, list):
        return False

    entry_by_id = {entry.id: entry for entry in entries}
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry_id = str(row.get("id", "")).strip().lower()
        entry = entry_by_id.get(entry_id)
        if entry is None or not entry.defaults:
            continue
        resolved = resolve_component_defaults(
            payload=payload,
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            preserve_existing_shared=True,
            include_literal=False,
            include_shared=True,
        )
        if resolved == row:
            continue
        row.clear()
        row.update(resolved)
        changed = True
    return changed


def materialize_shared_defaults(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> bool:
    changed = _materialize_scope_shared_defaults(
        payload=payload,
        section="infra",
        collection="components",
        entries=infra_entries,
    )
    changed |= _materialize_scope_shared_defaults(
        payload=payload,
        section="apps",
        collection="charts",
        entries=app_entries,
    )
    return changed


__all__ = [
    "component_path_has_material_value",
    "default_input_leaf_names",
    "default_target_paths",
    "literal_default_input_leaf_names",
    "literal_default_target_paths",
    "managed_default_payload_paths",
    "materialize_shared_defaults",
    "read_component_path",
    "resolve_component_defaults",
    "set_component_path",
    "shared_default_payload_paths",
    "shared_default_input_sources",
    "shared_default_target_paths",
]
