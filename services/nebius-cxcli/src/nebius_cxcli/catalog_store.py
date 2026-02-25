"""Catalog registry persistence and dynamic resolution helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .catalog import CatalogEntry, CatalogScope
from .catalog import catalog_entries as builtin_catalog_entries

CATALOG_SCHEMA_VERSION = "v1"


def default_catalog_file() -> Path:
    return Path.home() / ".config" / "nebius-cxcli" / "catalog.yaml"


def resolve_catalog_file_path(
    *,
    explicit: Path | None = None,
    deployments_root: Path | None = None,
) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()

    env_path = os.environ.get("NEBIUS_CXCLI_CATALOG_FILE")
    if env_path:
        return Path(env_path).expanduser().resolve()

    if deployments_root is not None:
        local_file = deployments_root / "catalogs" / "catalog.yaml"
        if local_file.exists():
            return local_file.resolve()

    return default_catalog_file().resolve()


def _empty_registry_payload() -> dict[str, Any]:
    return {
        "version": CATALOG_SCHEMA_VERSION,
        "infra": [],
        "apps": [],
    }


def _enabled_path_to_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        return tuple(part for part in normalized.split(".") if part)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError("enabled_path must be a dot-path string or list of strings")


def _enabled_path_to_serializable(path: tuple[str, ...] | None) -> str | None:
    if path is None:
        return None
    return ".".join(path)


def _entry_from_payload(scope: CatalogScope, payload: dict[str, Any]) -> CatalogEntry:
    if "id" not in payload:
        raise ValueError(f"Catalog '{scope}' entry is missing required key 'id'")
    if "description" not in payload:
        raise ValueError(f"Catalog '{scope}' entry '{payload.get('id')}' is missing 'description'")

    entry_id = str(payload["id"]).strip().lower()
    if not entry_id:
        raise ValueError(f"Catalog '{scope}' entry has empty id")

    schema_path = str(payload.get("schema_path") or f"catalog.{scope}.{entry_id}").strip()
    if not schema_path:
        raise ValueError(f"Catalog '{scope}' entry '{entry_id}' has empty schema_path")

    return CatalogEntry(
        id=entry_id,
        scope=scope,
        schema_path=schema_path,
        description=str(payload["description"]).strip(),
        default_enabled=bool(payload.get("default_enabled", False)),
        selectable=bool(payload.get("selectable", True)),
        enabled_path=_enabled_path_to_tuple(payload.get("enabled_path")),
        name=str(payload.get("name")).strip() if payload.get("name") is not None else None,
        engine_type=str(payload.get("engine_type", "custom")).strip() or "custom",
        source=str(payload.get("source")).strip() if payload.get("source") is not None else None,
        version=str(payload.get("version")).strip() if payload.get("version") is not None else None,
        origin="custom",
    )


def _entry_to_payload(entry: CatalogEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": entry.id,
        "name": entry.name,
        "description": entry.description,
        "schema_path": entry.schema_path,
        "default_enabled": entry.default_enabled,
        "selectable": entry.selectable,
        "enabled_path": _enabled_path_to_serializable(entry.enabled_path),
        "engine_type": entry.engine_type,
        "source": entry.source,
        "version": entry.version,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _load_registry_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_registry_payload()

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Catalog file must be a mapping: {path}")

    version = payload.get("version")
    if version is None:
        payload["version"] = CATALOG_SCHEMA_VERSION
    elif str(version) != CATALOG_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported catalog file version '{version}' in {path}; expected {CATALOG_SCHEMA_VERSION}"
        )

    for scope in ("infra", "apps"):
        entries = payload.get(scope)
        if entries is None:
            payload[scope] = []
            continue
        if not isinstance(entries, list):
            raise ValueError(f"Catalog '{scope}' in {path} must be a list")

    return payload


def load_custom_catalog_entries(path: Path) -> dict[CatalogScope, tuple[CatalogEntry, ...]]:
    payload = _load_registry_payload(path)
    resolved: dict[CatalogScope, tuple[CatalogEntry, ...]] = {"infra": (), "apps": ()}
    for scope in ("infra", "apps"):
        items = payload.get(scope, [])
        parsed: list[CatalogEntry] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Catalog '{scope}' entries in {path} must be mappings")
            parsed.append(_entry_from_payload(scope, item))
        resolved[scope] = tuple(parsed)
    return resolved


def resolved_catalog_entries(
    *,
    scope: CatalogScope,
    catalog_file: Path,
) -> tuple[CatalogEntry, ...]:
    merged: list[CatalogEntry] = []
    seen: set[str] = set()
    for entry in builtin_catalog_entries(scope):
        merged.append(entry)
        seen.add(entry.id)

    custom = load_custom_catalog_entries(catalog_file)[scope]
    for entry in custom:
        if entry.id in seen:
            for index, existing in enumerate(merged):
                if existing.id == entry.id:
                    merged[index] = entry
                    break
        else:
            merged.append(entry)
            seen.add(entry.id)
    return tuple(merged)


def upsert_catalog_entry(*, catalog_file: Path, entry: CatalogEntry) -> None:
    payload = _load_registry_payload(catalog_file)
    scope_entries = payload[entry.scope]
    assert isinstance(scope_entries, list)  # narrowed by _load_registry_payload

    updated = False
    for index, existing in enumerate(scope_entries):
        if isinstance(existing, dict) and str(existing.get("id", "")).strip().lower() == entry.id:
            scope_entries[index] = _entry_to_payload(entry)
            updated = True
            break
    if not updated:
        scope_entries.append(_entry_to_payload(entry))

    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    catalog_file.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
