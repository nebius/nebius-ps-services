"""Helpers for per-instance component identity in dynamic config payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

INSTANCE_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
INSTANCE_ID_FIELD = "instance_id"


def normalize_component_token(value: Any) -> str:
    """Normalize catalog ids / instance ids from config payloads."""
    return str(value or "").strip().lower().replace("_", "-")


def component_type_id(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get("id"))


def component_instance_id(row: Mapping[str, Any]) -> str:
    explicit = normalize_component_token(row.get(INSTANCE_ID_FIELD))
    if explicit:
        return explicit
    return component_type_id(row)


def component_instance_label(component_id: str, instance_id: str) -> str:
    normalized_component = normalize_component_token(component_id)
    normalized_instance = normalize_component_token(instance_id)
    if not normalized_component:
        return normalized_instance
    if normalized_instance == normalized_component:
        return normalized_component
    return f"{normalized_component}@{normalized_instance}"


def ensure_component_instance_id(
    row: dict[str, Any],
    *,
    default_component_id: str | None = None,
) -> str:
    component_id = normalize_component_token(row.get("id") or default_component_id)
    instance_id = component_instance_id(row)
    if not instance_id:
        instance_id = component_id
    if instance_id:
        row[INSTANCE_ID_FIELD] = instance_id
    return instance_id


def _used_instance_ids(rows: Sequence[Any]) -> set[str]:
    used: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        instance_id = component_instance_id(item)
        if instance_id:
            used.add(instance_id)
    return used


def next_component_instance_id(
    *,
    component_id: str,
    rows: Sequence[Any],
    requested_instance_id: str | None = None,
) -> str:
    normalized_component = normalize_component_token(component_id)
    if not normalized_component:
        raise ValueError("component_id is required to allocate a component instance id")
    desired_instance_id = normalize_component_token(requested_instance_id)
    used = _used_instance_ids(rows)
    if desired_instance_id:
        if desired_instance_id in used:
            raise ValueError(f"component instance_id '{desired_instance_id}' already exists")
        return desired_instance_id
    if normalized_component not in used:
        return normalized_component
    suffix = 2
    while True:
        candidate = f"{normalized_component}-{suffix}"
        if candidate not in used:
            return candidate
        suffix += 1


__all__ = [
    "INSTANCE_ID_FIELD",
    "INSTANCE_ID_PATTERN",
    "component_instance_id",
    "component_instance_label",
    "component_type_id",
    "ensure_component_instance_id",
    "next_component_instance_id",
    "normalize_component_token",
]
