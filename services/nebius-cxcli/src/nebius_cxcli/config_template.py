"""Starter template generator for canonical `config.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from .component_defaults import resolve_component_defaults
from .component_instances import INSTANCE_ID_FIELD

if TYPE_CHECKING:
    from .components import ComponentEntry

CONFIG_VERSION = "v1"


@dataclass(frozen=True)
class _SelectionEntry:
    id: str
    default_enabled: bool


def _coerce_selection_entries(
    *,
    entries: tuple[ComponentEntry, ...] | None,
) -> tuple[_SelectionEntry, ...]:
    if entries is None:
        return ()
    coerced: list[_SelectionEntry] = []
    for entry in entries:
        coerced.append(
            _SelectionEntry(
                id=entry.id,
                default_enabled=bool(entry.default_enabled),
            )
        )
    return tuple(coerced)


def _chart_source_parts(source: str | None) -> tuple[str, str]:
    raw = str(source or "").strip().rstrip("/")
    if "/" not in raw:
        return "", raw
    repo, chart = raw.rsplit("/", maxsplit=1)
    return repo, chart


def _normalize_app_group(group: str | None) -> str:
    raw = str(group or "").strip().lower()
    token = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)
    token = "-".join(part for part in token.split("-") if part)
    return token or "workloads"


def _canonical_chart_repo(*, chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    if repo.startswith("oci://"):
        tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if tail != chart_name.strip().lower():
            return f"{repo}/{chart_name.strip()}"
    return repo


def _apply_component_selection(
    payload: dict[str, object],
    *,
    selected_infra: set[str] | None,
    selected_apps: set[str] | None,
    infra_entries: tuple[ComponentEntry, ...] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> None:
    resolved_infra_entries = _coerce_selection_entries(entries=infra_entries)
    resolved_app_entries = _coerce_selection_entries(entries=app_entries)

    resolved_selected_infra = (
        set(selected_infra)
        if selected_infra is not None
        else {entry.id for entry in resolved_infra_entries if entry.default_enabled}
    )
    resolved_selected_apps = (
        set(selected_apps)
        if selected_apps is not None
        else {entry.id for entry in resolved_app_entries if entry.default_enabled}
    )

    infra_node = payload.get("infra")
    if isinstance(infra_node, dict):
        components = infra_node.get("components")
        if isinstance(components, list):
            for item in components:
                if not isinstance(item, dict):
                    continue
                component_id = str(item.get("id", "")).strip().lower()
                if not component_id:
                    continue
                item["enabled"] = component_id in resolved_selected_infra

    apps_node = payload.get("apps")
    if isinstance(apps_node, dict):
        charts = apps_node.get("charts")
        if isinstance(charts, list):
            for item in charts:
                if not isinstance(item, dict):
                    continue
                chart_id = str(item.get("id", "")).strip().lower()
                if not chart_id:
                    continue
                item["enabled"] = chart_id in resolved_selected_apps


def _starter_payload(
    *,
    client_name: str,
    tenant_id: str,
    project_id: str,
    region_id: str,
    email: str | None,
    infra_entries: tuple[ComponentEntry, ...] | None,
    app_entries: tuple[ComponentEntry, ...] | None,
) -> dict[str, object]:
    normalized_email = str(email).strip() if email is not None else ""
    payload: dict[str, object] = {
        "version": CONFIG_VERSION,
        "client_info": {
            "client_name": client_name,
            "nebius": {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "region_id": region_id,
            },
            "notifications": {
                "email_enabled": bool(normalized_email),
                "email": normalized_email or None,
            },
        },
        "infra": {
            "components": [],
        },
        "apps": {"charts": []},
    }

    infra_node = payload["infra"]
    if not isinstance(infra_node, dict):
        raise ValueError("infra payload must be a mapping")
    components_node = infra_node.get("components")
    if not isinstance(components_node, list):
        components_node = []
        infra_node["components"] = components_node
    for entry in infra_entries or ():
        component_row: dict[str, object] = {
            "id": entry.id,
            INSTANCE_ID_FIELD: entry.id,
            "enabled": bool(entry.default_enabled),
            "inputs": {},
        }
        if entry.defaults:
            component_row = resolve_component_defaults(
                component_node=component_row,
                entry=entry,
                preserve_existing_literal=True,
                include_shared=False,
            )
        components_node.append(component_row)

    apps_node = payload["apps"]
    if not isinstance(apps_node, dict):
        raise ValueError("apps payload must be a mapping")
    charts_node = apps_node.get("charts")
    if not isinstance(charts_node, list):
        charts_node = []
        apps_node["charts"] = charts_node
    for entry in app_entries or ():
        chart_repo = str(entry.chart_repo or "").strip()
        chart_name = str(entry.chart_name or "").strip()
        if not chart_name:
            chart_repo, chart_name = _chart_source_parts(entry.source)
        namespace = str(entry.default_namespace or "").strip() or entry.id
        release_name = str(entry.default_release_name or "").strip() or entry.id
        chart_repo = _canonical_chart_repo(chart_repo=chart_repo, chart_name=chart_name)
        chart_row: dict[str, object] = {
            "id": entry.id,
            INSTANCE_ID_FIELD: entry.id,
            "group": _normalize_app_group(entry.group),
            "enabled": bool(entry.default_enabled),
            "repo": chart_repo,
            "version": str(entry.version or ""),
            "namespace": namespace,
            "release-name": release_name,
            "values": {},
        }
        if entry.defaults:
            chart_row = resolve_component_defaults(
                component_node=chart_row,
                entry=entry,
                preserve_existing_literal=True,
                include_shared=False,
            )
        charts_node.append(
            chart_row
        )

    return payload


def starter_config_yaml(
    *,
    client_name: str,
    tenant_id: str,
    project_id: str,
    region_id: str,
    email: str | None,
    selected_infra: set[str] | None = None,
    selected_apps: set[str] | None = None,
    infra_entries: tuple[ComponentEntry, ...] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> str:
    """Render a starter project config.yaml with source-driven component defaults."""
    payload = _starter_payload(
        client_name=client_name,
        tenant_id=tenant_id,
        project_id=project_id,
        region_id=region_id,
        email=email,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    _apply_component_selection(
        payload,
        selected_infra=selected_infra,
        selected_apps=selected_apps,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    return yaml.safe_dump(payload, sort_keys=False)
