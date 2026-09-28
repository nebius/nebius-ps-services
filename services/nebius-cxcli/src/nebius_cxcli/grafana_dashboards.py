"""Portable dashboard inputs and explicit datasource mapping (no network or writes)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

UID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
MAX_JSON_BYTES = 20 * 1024 * 1024
IMPORTS_FIELD = "dashboard_imports"
IMPORTS_PROVIDER = "cxcli-api-imports"
INTERNAL_SOURCES = {"-- Grafana --", "grafana", "__expr__", "-- Mixed --"}


class DatasourceMappingRequired(ValueError):
    def __init__(
        self,
        source: str,
        *,
        expected_type: str,
        candidates: Sequence[Mapping[str, str]],
    ) -> None:
        self.source = source
        self.expected_type = expected_type
        self.candidates = tuple(dict(item) for item in candidates)
        super().__init__(
            f"Unresolved datasource {source!r}; supply --datasource-map {source or 'SOURCE'}=UID"
        )


def dashboard_uid(value: object) -> str:
    if not isinstance(value, str) or not UID_PATTERN.fullmatch(value):
        raise ValueError("Dashboard/folder UID must contain 1–40 letters, digits, '_' or '-'")
    return value


def normalize_dashboard(payload: object) -> dict[str, Any]:
    """Accept classic JSON, the API export wrapper, and a v1 resource envelope."""
    if not isinstance(payload, dict):
        raise ValueError("Dashboard JSON must be an object")
    raw: Any = copy.deepcopy(payload)
    if "apiVersion" in raw:
        if raw.get("apiVersion") != "dashboard.grafana.app/v1":
            raise ValueError(
                "Export this dashboard as Classic JSON or a dashboard.grafana.app/v1 resource"
            )
        metadata = raw.get("metadata", {})
        raw = raw.get("spec")
        if not isinstance(raw, dict) or not isinstance(metadata, dict):
            raise ValueError("Invalid v1 dashboard resource")
        name = metadata.get("name")
        if raw.get("uid") and raw["uid"] != name:
            raise ValueError("Dashboard uid differs from resource metadata.name")
        raw["uid"] = name
    elif "dashboard" in raw:
        raw = raw["dashboard"]
    if not isinstance(raw, dict):
        raise ValueError("Dashboard must contain an object")
    dashboard_uid(raw.get("uid"))
    if not isinstance(raw.get("title"), str) or not raw["title"].strip():
        raise ValueError("Dashboard requires a nonempty title")
    if "panels" in raw and not isinstance(raw["panels"], list):
        raise ValueError("Dashboard panels must be an array")
    if "templating" in raw and (
        not isinstance(raw["templating"], dict)
        or not isinstance(raw["templating"].get("list", []), list)
    ):
        raise ValueError("Dashboard templating must contain a list")
    if "__inputs" in raw and (
        not isinstance(raw["__inputs"], list)
        or any(not isinstance(item, dict) for item in raw["__inputs"])
    ):
        raise ValueError("Dashboard __inputs must be an array of objects")
    for key in ("id", "version", "iteration"):
        raw.pop(key, None)
    return raw


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def content_digest(dashboard: Mapping[str, Any], folder_uid: str = "") -> str:
    return hashlib.sha256(
        json_bytes({"dashboard": normalize_dashboard(dict(dashboard)), "folder": folder_uid})
    ).hexdigest()


def read_dashboard(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Dashboard input must be a regular non-symlink file: {path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"Dashboard exceeds the 20 MiB limit: {path.name}")
    try:
        raw = json.loads(
            path.read_bytes(),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number")),
        )
        return normalize_dashboard(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid dashboard {path.name}: {exc}") from None


def load_dashboards(paths: Sequence[Path], *, recursive: bool = False) -> list[dict[str, Any]]:
    files: list[Path] = []
    for raw in paths:
        path = raw.expanduser()
        if path.is_symlink():
            raise ValueError("Dashboard inputs must not be symlinks")
        if path.is_dir():
            candidates = path.rglob("*.json") if recursive else path.glob("*.json")
            for candidate in sorted(candidates):
                if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
                    raise ValueError("Dashboard inputs must not traverse symlinks")
                files.append(candidate)
        else:
            files.append(path)
    if not files:
        raise ValueError("No dashboard JSON files selected")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        dashboard = read_dashboard(path)
        uid = dashboard["uid"]
        if uid in seen:
            raise ValueError(f"Duplicate dashboard UID in input batch: {uid}")
        seen.add(uid)
        result.append(dashboard)
    return sorted(result, key=lambda item: item["uid"])


def parse_mappings(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        source, sep, uid = value.partition("=")
        if not sep or not source or not uid or source in result:
            raise ValueError("Use each --datasource-map SOURCE=UID once with nonempty values")
        result[source] = dashboard_uid(uid)
    return result


def datasource_refs(value: object) -> Iterable[object]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "datasource":
                yield item
            else:
                yield from datasource_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from datasource_refs(item)


def map_datasources(
    dashboard: dict[str, Any],
    mappings: Mapping[str, str],
    available: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Preserve distinct sources and dashboard variables; never rewrite query text."""
    result = copy.deepcopy(dashboard)
    variables = {
        str(item.get("name"))
        for item in result.get("templating", {}).get("list", [])
        if isinstance(item, dict) and item.get("type") == "datasource"
    }
    inputs = {
        str(item.get("name")): item for item in result.get("__inputs", []) if isinstance(item, dict)
    }
    inventory = {str(item["uid"]): item for item in (available or []) if item.get("uid")}

    def rewrite(ref: object) -> object:
        if ref is None:
            return ref
        name = str(ref.get("uid") or ref.get("name") or "") if isinstance(ref, dict) else str(ref)
        kind = str(ref.get("type") or "") if isinstance(ref, dict) else ""
        variable = (
            name.removeprefix("${").removesuffix("}")
            if name.startswith("${")
            else name.removeprefix("$")
        )
        if name in INTERNAL_SOURCES or kind in {"grafana", "__expr__", "mixed"}:
            return ref
        target = mappings.get(name) or mappings.get(variable)
        if not target and name.startswith("$") and variable in variables and variable not in inputs:
            return ref
        if not target and available is None:
            return ref
        if not target and name in inventory:
            target = name
        if not target:
            matches = [item for item in inventory.values() if item.get("name") == name]
            if len(matches) == 1:
                target = str(matches[0]["uid"])
        expected = kind or str(inputs.get(variable, {}).get("pluginId") or "")
        if expected.startswith("$"):
            expected = ""
        if not target:
            raise DatasourceMappingRequired(
                variable or name,
                expected_type=expected,
                candidates=[
                    {
                        "uid": uid,
                        "name": str(item.get("name") or uid),
                        "type": str(item.get("type") or ""),
                    }
                    for uid, item in inventory.items()
                    if not expected or item.get("type") == expected
                ],
            )
        if available is not None:
            selected = inventory.get(target)
            if selected is None:
                raise ValueError(f"Datasource mapping references unavailable UID: {target}")
            if expected and selected.get("type") != expected:
                raise ValueError(f"Datasource type mismatch for {name!r}")
            kind = str(selected.get("type") or kind)
        return (
            {**ref, "uid": target, **({"type": kind} if kind else {})}
            if isinstance(ref, dict)
            else target
        )

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key == "datasource":
                    value[key] = rewrite(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    if "__inputs" in result:
        remaining = [item for item in result["__inputs"] if item.get("type") != "datasource"]
        if available is None:
            remaining += [
                item
                for item in result["__inputs"]
                if item.get("type") == "datasource"
                and item.get("name") not in mappings
                and f"${{{item.get('name')}}}" not in mappings
            ]
        if remaining and available is not None:
            raise ValueError("Dashboard has unresolved non-datasource import inputs")
        if remaining:
            result["__inputs"] = remaining
        else:
            result.pop("__inputs")
    return result


def validate_import_declarations(row: Mapping[str, Any], *, grafana_id: str) -> None:
    if IMPORTS_FIELD not in row:
        return
    if row.get("id") != grafana_id:
        raise ValueError("dashboard_imports is reserved for the Grafana component")
    entries = row[IMPORTS_FIELD]
    if not isinstance(entries, list):
        raise ValueError("dashboard_imports must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {
            "uid",
            "json_file",
            "folder_uid",
            "catalog_key",
            "sha256",
            "replay",
            "management_sha256",
        }:
            raise ValueError("Invalid dashboard_imports entry fields")
        uid = dashboard_uid(entry.get("uid"))
        if uid in seen:
            raise ValueError("Duplicate dashboard_imports UID")
        seen.add(uid)
        if type(entry.get("replay", True)) is not bool:
            raise ValueError("dashboard_imports.replay must be a boolean")
        if entry.get("replay", True):
            if "management_sha256" in entry:
                raise ValueError("Replayable dashboards cannot carry managed update provenance")
        elif (
            "catalog_key" in entry
            or not isinstance(entry.get("management_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["management_sha256"])
        ):
            raise ValueError("Manual dashboard copies require management_sha256 and no catalog_key")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            raise ValueError("dashboard_imports.sha256 must bind the imported content")
        path = entry.get("json_file")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ValueError("dashboard_imports.json_file must be a project-relative path")
        folder = entry.get("folder_uid", "")
        if folder:
            dashboard_uid(folder)
        if "catalog_key" in entry:
            key = entry["catalog_key"]
            if (
                not isinstance(key, str)
                or len(key.split("/")) != 2
                or any(not UID_PATTERN.fullmatch(part) for part in key.split("/"))
            ):
                raise ValueError("dashboard_imports.catalog_key must be provider/dashboard")
