"""Track generated worker values without mistaking operator edits for defaults."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

WORKER_DEFAULTS_FIELD = "worker-defaults"
_PATHS = {
    "cpu": ("slurmd", "resources", "cpu"),
    "memory": ("slurmd", "resources", "memory"),
    "static": ("nodeConfig", "static"),
    "gresConfig": ("nodeConfig", "gresConfig"),
}


def validate_worker_defaults(row: Mapping[str, Any]) -> None:
    saved = row.get(WORKER_DEFAULTS_FIELD, {})
    if not isinstance(saved, dict):
        raise ValueError("Soperator worker-defaults must be a mapping")
    for name, fields in saved.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
            raise ValueError("Soperator worker-defaults requires valid NodeSet names")
        if not isinstance(fields, dict) or not fields or not fields.keys() <= _PATHS.keys():
            raise ValueError(
                "Soperator worker-defaults accepts only cpu, memory, static and gresConfig fields"
            )
        for field, value in fields.items():
            if field == "gresConfig":
                if not isinstance(value, list) or len(value) != 1:
                    raise ValueError(
                        "Soperator worker-defaults gresConfig requires one device line"
                    )
                value = value[0]
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError("Soperator worker-defaults requires bounded string values")


def _nodes(row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        node["name"]: node
        for node in row.get("values", {}).get("nodesets", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }


def _parent(node: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = node
    for key in path[:-1]:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def prepare_worker_defaults(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Clear only unchanged generated fields; return remaining explicit fields."""
    validate_worker_defaults(row)
    saved = row.get(WORKER_DEFAULTS_FIELD, {})
    explicit_nodes = "/nodesets" in row.get("values-explicit-paths", [])
    before: dict[str, dict[str, Any]] = {}
    for name, node in _nodes(row).items():
        fields = before.setdefault(name, {})
        for field, path in _PATHS.items():
            parent = _parent(node, path)
            if not isinstance(parent, dict) or path[-1] not in parent:
                continue
            value = parent[path[-1]]
            if not explicit_nodes and field in saved.get(name, {}) and value == saved[name][field]:
                del parent[path[-1]]
            else:
                fields[field] = value
    return before


def remember_worker_defaults(row: dict[str, Any], before: Mapping[str, Mapping[str, Any]]) -> None:
    saved: dict[str, dict[str, Any]] = {}
    for name, node in _nodes(row).items():
        for field, path in _PATHS.items():
            parent = _parent(node, path)
            if (
                field not in before.get(name, {})
                and isinstance(parent, Mapping)
                and path[-1] in parent
            ):
                saved.setdefault(name, {})[field] = copy.deepcopy(parent[path[-1]])
    if saved:
        row[WORKER_DEFAULTS_FIELD] = saved
    else:
        row.pop(WORKER_DEFAULTS_FIELD, None)
