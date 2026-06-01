"""Helpers for catalog-declared component output/input wiring."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .component_defaults import (
    component_path_has_material_value,
    read_component_path,
    resolve_component_defaults,
)
from .component_instances import (
    INSTANCE_ID_PATTERN,
    component_instance_id,
    component_type_id,
    normalize_component_token,
)
from .component_sources import ComponentInputBinding, component_input_binding_ref
from .components import ComponentEntry, component_entries
from .runtime_config import to_plain_data

_UNRESOLVED = object()
_ATTRIBUTE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _split_path(path: str) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in str(path).split(".") if segment.strip())


def _normalize_output_name(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    return "_".join(part for part in token.split("_") if part)


def component_output_ref(component_instance_id: str, output_name: str) -> str:
    return f"{str(component_instance_id).strip().lower()}.{str(output_name).strip().lower()}"


def output_lookup(entry: ComponentEntry) -> dict[str, Any]:
    return {output.name: output for output in entry.outputs}


def input_binding_conflicts(
    component_node: Mapping[str, Any],
    entry: ComponentEntry,
) -> tuple[tuple[str, str], ...]:
    conflicts: list[tuple[str, str]] = []
    for binding in entry.input_bindings:
        existing_value = read_component_path(component_node, binding.target_path)
        if component_path_has_material_value(existing_value):
            conflicts.append(
                (
                    binding.target_path,
                    component_input_binding_ref(binding),
                )
            )
    return tuple(conflicts)


def managed_input_binding_paths(entry: ComponentEntry) -> set[str]:
    return {binding.target_path for binding in entry.input_bindings}


def input_binding_leaf_names(entry: ComponentEntry) -> set[str]:
    result: set[str] = set()
    for binding in entry.input_bindings:
        segments = _split_path(binding.target_path)
        if len(segments) >= 2 and segments[0] == "inputs":
            result.add(segments[1].strip().lower().replace("-", "_"))
    return result


def row_input_bindings(
    component_node: Mapping[str, Any],
    *,
    field_label: str = "infra.components[]",
) -> tuple[ComponentInputBinding, ...]:
    raw = component_node.get("bindings")
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field_label}.bindings must be a mapping")

    bindings: list[ComponentInputBinding] = []
    for target_path_raw, raw_spec in raw.items():
        target_path = str(target_path_raw or "").strip()
        if not target_path:
            raise ValueError(f"{field_label}.bindings entries must use non-empty target paths")
        if not target_path.startswith("inputs."):
            raise ValueError(
                f"{field_label}.bindings.{target_path} must target an inputs.* path"
            )
        if not isinstance(raw_spec, Mapping):
            raise ValueError(f"{field_label}.bindings.{target_path} must be a mapping")

        source_component = normalize_component_token(raw_spec.get("source_component"))
        source_output = _normalize_output_name(raw_spec.get("source_output"))
        if not source_component or not source_output:
            raise ValueError(
                f"{field_label}.bindings.{target_path} must set source_component and source_output"
            )

        source_instance_raw = str(raw_spec.get("source_instance") or "").strip()
        source_instance = normalize_component_token(source_instance_raw) if source_instance_raw else None
        if source_instance is not None and not INSTANCE_ID_PATTERN.fullmatch(source_instance):
            raise ValueError(
                f"{field_label}.bindings.{target_path}.source_instance must use lowercase letters, "
                "digits, and hyphens"
            )

        key_raw = raw_spec.get("key")
        attr_raw = raw_spec.get("attribute")
        key = str(key_raw).strip() if key_raw is not None else None
        attribute = str(attr_raw).strip() if attr_raw is not None else None
        if bool(key) != bool(attribute):
            raise ValueError(
                f"{field_label}.bindings.{target_path} must set key and attribute together"
            )
        if attribute and not _ATTRIBUTE_PATTERN.fullmatch(attribute):
            raise ValueError(
                f"{field_label}.bindings.{target_path}.attribute must be a simple attribute name"
            )

        bindings.append(
            ComponentInputBinding(
                target_path=target_path,
                source_component_id=source_component,
                source_output_name=source_output,
                source_instance_id=source_instance,
                key=key,
                attribute=attribute,
            )
        )
    return tuple(bindings)


def managed_input_binding_payload_paths(
    component_path: tuple[str | int, ...],
    entry: ComponentEntry,
) -> set[tuple[str | int, ...]]:
    result: set[tuple[str | int, ...]] = set()
    for target_path in managed_input_binding_paths(entry):
        segments = _split_path(target_path)
        if segments:
            result.add(component_path + tuple(segments))
    return result


def component_entry_lookup() -> dict[str, ComponentEntry]:
    merged: dict[str, ComponentEntry] = {}
    for entry in (*component_entries("infra"), *component_entries("apps")):
        existing = merged.get(entry.id)
        if existing is not None and existing.scope != entry.scope:
            raise RuntimeError(
                f"Component id '{entry.id}' is declared in both infra and apps component sources. "
                "Cross-component bindings require globally unique component ids."
            )
        merged[entry.id] = entry
    return merged


def _component_rows(
    payload: Mapping[str, Any],
    *,
    scope: str,
) -> list[dict[str, Any]]:
    if scope == "infra":
        rows = payload.get("infra", {}).get("components", [])
    else:
        rows = payload.get("apps", {}).get("charts", [])
    if not isinstance(rows, list):
        return []

    resolved: list[dict[str, Any]] = []
    entry_by_id = {
        entry.id: entry for entry in component_entries("infra" if scope == "infra" else "apps")
    }
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        component_id = component_type_id(row)
        if not component_id:
            continue
        entry = entry_by_id.get(component_id)
        resolved_row = copy.deepcopy(dict(row))
        if entry is not None and entry.defaults:
            resolved_row = resolve_component_defaults(
                payload=payload,
                component_node=resolved_row,
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
                include_shared=False,
            )
        instance_id = component_instance_id(resolved_row)
        if not instance_id:
            continue
        resolved.append(resolved_row)
    return resolved


def resolved_component_row(
    payload: Mapping[str, Any],
    *,
    component_id: str,
    instance_id: str | None = None,
) -> tuple[ComponentEntry | None, dict[str, Any] | None]:
    entry_lookup = component_entry_lookup()
    entry = entry_lookup.get(component_id)
    if entry is None:
        return None, None
    rows = _component_rows(payload, scope=entry.scope)
    if instance_id is not None:
        matched_instance = str(instance_id).strip().lower()
        return entry, next(
            (
                row
                for row in rows
                if component_type_id(row) == component_id
                and component_instance_id(row) == matched_instance
            ),
            None,
        )
    matched_rows = [row for row in rows if component_type_id(row) == component_id]
    if len(matched_rows) > 1:
        raise ValueError(
            f"Component '{component_id}' has multiple enabled instances. "
            "Resolve it by instance_id instead."
        )
    return entry, matched_rows[0] if matched_rows else None


def resolve_input_binding_source(
    payload: Mapping[str, Any],
    *,
    binding: ComponentInputBinding,
) -> tuple[ComponentEntry | None, dict[str, Any] | None, str]:
    entry, row = resolved_component_row(
        payload,
        component_id=binding.source_component_id,
        instance_id=binding.source_instance_id,
    )
    instance_id = component_instance_id(row) if row is not None else ""
    return entry, row, instance_id


def resolve_component_output_value(
    payload: Mapping[str, Any],
    *,
    component_id: str,
    output_name: str,
    instance_id: str | None = None,
) -> Any:
    entry, row = resolved_component_row(payload, component_id=component_id, instance_id=instance_id)
    if entry is None or row is None:
        return _UNRESOLVED
    output_spec = output_lookup(entry).get(output_name)
    if output_spec is None:
        return _UNRESOLVED
    if output_spec.kind == "input":
        value = read_component_path(row, output_spec.source_path)
        if value is None:
            return _UNRESOLVED
        return to_plain_data(value)
    if output_spec.kind == "literal":
        return to_plain_data(output_spec.value)
    return _UNRESOLVED


__all__ = [
    "_UNRESOLVED",
    "component_entry_lookup",
    "component_output_ref",
    "input_binding_conflicts",
    "input_binding_leaf_names",
    "managed_input_binding_payload_paths",
    "managed_input_binding_paths",
    "output_lookup",
    "resolve_component_output_value",
    "resolve_input_binding_source",
    "resolved_component_row",
    "row_input_bindings",
]
