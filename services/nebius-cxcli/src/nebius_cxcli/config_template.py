"""Starter template generator for canonical `config.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .components import ComponentEntry

CONFIG_VERSION = "v1"
DEFAULT_SSH_USER_NAME = "ubuntu"
DEFAULT_SSH_PUBLIC_KEY = "ssh-ed25519 AAAA-REPLACE-WITH-YOUR-KEY"


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


def _normalize_app_section(group: str | None) -> str:
    raw = str(group or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return token or "workloads"


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
        releases = apps_node.get("releases")
        if isinstance(releases, list):
            for item in releases:
                if not isinstance(item, dict):
                    continue
                release_id = str(item.get("id", "")).strip().lower()
                if not release_id:
                    continue
                item["enabled"] = release_id in resolved_selected_apps


def _starter_payload(
    *,
    client_name: str,
    tenant_id: str,
    env: str,
    cluster_name: str,
    project_id: str,
    region_id: str,
    email: str | None,
    infra_entries: tuple[ComponentEntry, ...] | None,
    app_entries: tuple[ComponentEntry, ...] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": CONFIG_VERSION,
        "client_info": {
            "client_name": client_name,
            "env": env,
            "cluster_name": cluster_name,
            "nebius": {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "region_id": region_id,
            },
            "notifications": {
                "inventory_markdown": True,
                "email": email,
            },
        },
        "infra": {
            "ssh_user_name": DEFAULT_SSH_USER_NAME,
            "ssh_public_key": DEFAULT_SSH_PUBLIC_KEY,
            "components": [],
        },
        "apps": {"releases": []},
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
            "enabled": bool(entry.default_enabled),
            "inputs": {},
        }
        if entry.source:
            component_row["source"] = str(entry.source)
        if entry.version:
            component_row["version"] = str(entry.version)
        components_node.append(component_row)

    apps_node = payload["apps"]
    if not isinstance(apps_node, dict):
        raise ValueError("apps payload must be a mapping")
    releases_node = apps_node.get("releases")
    if not isinstance(releases_node, list):
        releases_node = []
        apps_node["releases"] = releases_node
    for entry in app_entries or ():
        chart_repo = str(entry.chart_repo or "").strip()
        chart_name = str(entry.chart_name or "").strip()
        if not chart_name:
            chart_repo, chart_name = _chart_source_parts(entry.source)
        namespace = str(entry.default_namespace or "").strip() or entry.id
        release_name = str(entry.default_release_name or "").strip() or entry.id
        releases_node.append(
            {
                "id": entry.id,
                "section": _normalize_app_section(entry.group),
                "enabled": bool(entry.default_enabled),
                "values": {
                    "namespace": namespace,
                    "release_name": release_name,
                    "chart": {
                        "repo": chart_repo,
                        "name": chart_name,
                        "version": str(entry.version or ""),
                    },
                    "values": {},
                },
            }
        )

    return payload


def starter_config_yaml(
    *,
    client_name: str,
    tenant_id: str,
    env: str,
    cluster_name: str,
    project_id: str,
    region_id: str,
    subnet_id: str,  # kept for API stability; source-only template does not preseed subnet.
    email: str | None,
    selected_infra: set[str] | None = None,
    selected_apps: set[str] | None = None,
    infra_entries: tuple[ComponentEntry, ...] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> str:
    """Render a starter instance config.yaml with source-driven component defaults."""
    _ = subnet_id
    payload = _starter_payload(
        client_name=client_name,
        tenant_id=tenant_id,
        env=env,
        cluster_name=cluster_name,
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
