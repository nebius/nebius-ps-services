"""Canonical configuration owners; returned rows are the actual source records.

Infrastructure components are Terraform subjects. External clusters never enter
that collection. Application policy stays on its chart, outside Helm values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONFIG_VERSION = "v2"


def validate_source_contract(payload: Any) -> None:
    """Reject unsupported source contracts before defaults or external work."""
    if not isinstance(payload, Mapping):
        raise ValueError("config.yaml root must be a mapping")
    if "deploy" in payload:
        raise ValueError(
            "The deploy section is unsupported. Move cluster settings to infra components "
            "or infra.external_clusters, and application settings to apps.charts; use version: v2."
        )
    if payload.get("version") != CONFIG_VERSION:
        raise ValueError(
            "config.yaml requires version: v2; explicitly update the source configuration"
        )
    unknown = set(payload) - {"version", "client_info", "infra", "apps", "compatibility"}
    if unknown:
        raise ValueError(
            "unknown field(s) at root: " + ", ".join(sorted(str(key) for key in unknown))
        )


def external_clusters(payload: Mapping[str, Any]) -> list[Any]:
    infra = payload.get("infra", {})
    rows = infra.get("external_clusters", []) if isinstance(infra, Mapping) else []
    return rows if isinstance(rows, list) else []


def cluster_rows(payload: Mapping[str, Any], *, enabled_only: bool = True) -> list[Any]:
    infra = payload.get("infra", {})
    components = infra.get("components", []) if isinstance(infra, Mapping) else []
    return [
        row
        for row in components
        if isinstance(row, Mapping)
        and row.get("id") == "mk8s"
        and (not enabled_only or row.get("enabled") is True)
    ] + external_clusters(payload)


def cluster_row(payload: Mapping[str, Any], target: str) -> Any:
    rows = [row for row in cluster_rows(payload) if row.get("instance_id") == target]
    if len(rows) > 1:
        raise ValueError(f"Cluster identity {target!r} is duplicated")
    return rows[0] if rows else None


def require_cluster_row(payload: Mapping[str, Any], target: str) -> Any:
    row = cluster_row(payload, target)
    if row is None:
        raise ValueError(f"Cluster {target!r} is not declared in infra")
    return row


def soperator_row(payload: Mapping[str, Any], target: str) -> Any:
    apps = payload.get("apps", {})
    rows = apps.get("charts", []) if isinstance(apps, Mapping) else []
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("id") == "soperator"
        and row.get("instance_id") == target
    ]
    if len(matches) > 1:
        raise ValueError(f"Soperator application identity {target!r} is duplicated")
    return matches[0] if matches else None


def require_soperator_row(payload: Mapping[str, Any], target: str) -> Any:
    row = soperator_row(payload, target)
    if row is None:
        raise ValueError(f"Soperator application for cluster {target!r} is not declared")
    return row
