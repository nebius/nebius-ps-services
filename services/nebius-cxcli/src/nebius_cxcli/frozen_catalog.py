"""Operation-scoped, JSON-only snapshots of resolved rendering dependencies."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields, is_dataclass, replace
from typing import Any

from . import component_sources as sources
from .cluster_handoffs import Handoff
from .compatibility_matrix import (
    EVALUATOR_VERSION,
    FROZEN_MATRIX,
    digest,
    load_matrix,
    validate_matrix,
)

_SCHEMA = "nebius-cxcli.render-inputs/v1"
_ACTIVE: ContextVar[sources.ComponentSources | None] = ContextVar("frozen_catalog", default=None)
_TYPES = {
    cls.__name__: cls
    for cls in (*vars(sources).values(), Handoff)
    if isinstance(cls, type) and is_dataclass(cls)
}


def active_catalog() -> sources.ComponentSources | None:
    return _ACTIVE.get()


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {"$map": {key: _encode(item) for key, item in value.items()}}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"Unsupported frozen catalog value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$tuple"} and isinstance(value["$tuple"], list):
            return tuple(_decode(item) for item in value["$tuple"])
        if set(value) == {"$map"} and isinstance(value["$map"], dict):
            return {key: _decode(item) for key, item in value["$map"].items()}
        if set(value) == {"$type", "fields"}:
            cls = _TYPES.get(value["$type"])
            if cls and set(value["fields"]) == {field.name for field in fields(cls)}:
                return cls(**{key: _decode(item) for key, item in value["fields"].items()})
        raise ValueError("Unrecognized frozen catalog type or fields; rerender required")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Invalid frozen catalog value")


def freeze_catalog(config: Mapping[str, Any]) -> dict[str, Any]:
    catalog = sources.load_component_sources()
    from .mysterybox_eso import materialize_mysterybox_eso_app_values
    from .nfs_csi import ensure_nfs_csi_app_rows

    config = copy.deepcopy(dict(config))
    ensure_nfs_csi_app_rows(config)
    materialize_mysterybox_eso_app_values(config)
    selected = {
        str(row.get("id", ""))
        for section, key in (("infra", "components"), ("apps", "charts"))
        for row in config.get(section, {}).get(key, [])
        if isinstance(row, Mapping)
    }
    # Include binding dependencies even when a derived row is materialized only
    # after Terraform outputs become available.
    entries: dict[str, sources.TFModuleSource | sources.HelmChartSource] = {
        entry.module: entry for entry in catalog.tf_modules
    }
    entries.update({entry.name: entry for entry in catalog.helm_charts})
    while True:
        dependencies = {
            binding.source_component_id
            for key in selected
            if key in entries
            for binding in entries[key].input_bindings
        }
        dependencies.update(
            dependency
            for entry in entries.values()
            if isinstance(entry, sources.HelmChartSource) and entry.name in selected
            for dependency in entry.release_install_after
        )
        if dependencies <= selected:
            break
        selected |= dependencies
    frozen = replace(
        catalog,
        tf_modules=tuple(e for e in catalog.tf_modules if e.module in selected),
        helm_charts=tuple(e for e in catalog.helm_charts if e.name in selected),
    )
    payload = {
        "schema": _SCHEMA,
        "evaluator": EVALUATOR_VERSION,
        "catalog": _encode(frozen),
        "cli": _encode(sources.load_cli_settings()),
        "matrix": load_matrix(),
    }
    if len(json.dumps(payload)) > 4 * 1024 * 1024:
        raise ValueError("Selected rendering inputs exceed the snapshot size limit")
    return {**payload, "sha256": digest(payload)}


def verify_snapshot(snapshot: Mapping[str, Any]) -> None:
    if len(json.dumps(snapshot)) > 4 * 1024 * 1024:
        raise ValueError("Frozen rendering inputs exceed the snapshot size limit")
    if set(snapshot) != {"schema", "evaluator", "catalog", "cli", "matrix", "sha256"}:
        raise ValueError("Frozen rendering inputs are missing or malformed; rerender required")
    payload = {key: value for key, value in snapshot.items() if key != "sha256"}
    if snapshot["schema"] != _SCHEMA or snapshot["evaluator"] != EVALUATOR_VERSION:
        raise ValueError("Unsupported frozen rendering contract; rerender required")
    if snapshot["sha256"] != digest(payload):
        raise ValueError("Frozen rendering input integrity mismatch")
    validate_matrix(snapshot["matrix"])


@contextmanager
def use_frozen_catalog(snapshot: Mapping[str, Any]) -> Iterator[None]:
    verify_snapshot(snapshot)
    catalog = _decode(snapshot["catalog"])
    cli = _decode(snapshot["cli"])
    if not isinstance(catalog, sources.ComponentSources) or not isinstance(
        cli, sources.CliSettings
    ):
        raise ValueError("Invalid frozen catalog root")
    token = _ACTIVE.set(replace(catalog, cli=cli))
    matrix_token = FROZEN_MATRIX.set(snapshot["matrix"])
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        FROZEN_MATRIX.reset(matrix_token)
