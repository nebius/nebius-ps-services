"""Canonical PostgreSQL binding for managed Grafana installations.

Only Secret references are rendered. Credentials belong to the runtime bootstrap.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import yaml

from .component_defaults import resolve_component_defaults
from .component_instances import component_type_id
from .component_sources import load_component_sources
from .components import component_entries
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data

OWNER_ANNOTATION = "cxcli.nebius.com/app-owner"
CLIENT_LABEL = "cxcli.nebius.com/grafana-database-client"


def database_component_id() -> str:
    for module in load_component_sources().tf_modules:
        if module.observability.grafana.database_chart_component_id:
            return module.observability.grafana.database_chart_component_id
    return ""


def app_owner(config: Any, target_ref: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [to_plain_data(config).get("client_info", {}), target_ref], sort_keys=True
        ).encode()
    ).hexdigest()


def database_rows(config: Any, target_ref: str | None = None) -> list[dict[str, Any]]:
    component_id = database_component_id()
    payload = to_plain_data(config)
    rows = [
        row
        for row in payload.get("apps", {}).get("charts", [])
        if component_id
        and row.get("enabled")
        and component_type_id(row) == component_id
        and (target_ref is None or app_chart_target_ref(row) == target_ref)
    ]
    if not rows:
        return []
    entry = next((item for item in component_entries("apps") if item.id == component_id), None)
    if entry is None:
        return []
    return [
        resolve_component_defaults(
            payload=payload,
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            preserve_existing_shared=False,
            include_shared=False,
        )
        for row in rows
    ]


def database_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("namespace") or "observability"), str(
        row.get("release-name") or "postgresql"
    )


def database_secret_names(row: Mapping[str, Any]) -> tuple[str, str]:
    _, release = database_identity(row)
    return f"{release}-admin", f"{release}-grafana"


def validate_database_values(values: Mapping[str, Any]) -> None:
    persistence = values.get("persistence", {})
    if persistence.get("existingClaim") or persistence.get("enabled") is False:
        raise ValueError(
            "Managed PostgreSQL requires its own retained PVC; existingClaim is unsupported"
        )
    if values.get("replicaCount", 1) != 1 or values.get("replication", {}).get("enabled"):
        raise ValueError("Managed PostgreSQL supports exactly one primary")
    if values.get("service", {}).get("type", "ClusterIP") != "ClusterIP":
        raise ValueError("Managed PostgreSQL requires a private ClusterIP service")
    if (
        values.get("service", {}).get("port", 5432) != 5432
        or values.get("service", {}).get("targetPort", 5432) != 5432
    ):
        raise ValueError("Managed PostgreSQL requires port 5432")
    if values.get("namespaceOverride") or values.get("config", {}).get("existingConfigmap"):
        raise ValueError(
            "Managed PostgreSQL does not accept namespace or configuration indirection"
        )
    retention = values.get("persistentVolumeClaimRetentionPolicy", {})
    if retention.get("enabled") is False or any(
        retention.get(key, "Retain") != "Retain" for key in ("whenDeleted", "whenScaled")
    ):
        raise ValueError("Managed PostgreSQL PVC retention must be Retain")


def materialize_database_values(payload: dict[str, Any]) -> bool:
    """Derive chart identity/authentication and a namespaced ingress policy."""
    changed = False
    component_id = database_component_id()
    for row in payload.get("apps", {}).get("charts", []):
        if not component_id or not row.get("enabled") or component_type_id(row) != component_id:
            continue
        namespace, release = database_identity(row)
        admin, application = database_secret_names(row)
        owner = app_owner(payload, app_chart_target_ref(row))
        values = copy.deepcopy(row.get("values", {}))
        validate_database_values(values)
        values["fullnameOverride"] = release
        values["auth"] = {
            "username": "postgres",
            "database": "postgres",
            "existingSecret": admin,
            "secretKeys": {"adminPasswordKey": "password"},
        }
        values["customUser"] = {
            "name": "grafana",
            "database": "grafana",
            "existingSecret": application,
            "secretKeys": {"name": "", "database": "", "password": "password"},
        }
        values.setdefault("commonAnnotations", {})[OWNER_ANNOTATION] = owner
        values.setdefault("persistence", {}).setdefault("annotations", {})[OWNER_ANNOTATION] = owner
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": f"{release}-grafana", "namespace": namespace},
            "spec": {
                "podSelector": {"matchLabels": {"app.kubernetes.io/instance": release}},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [{"podSelector": {"matchLabels": {CLIENT_LABEL: "true"}}}],
                        "ports": [{"protocol": "TCP", "port": 5432}],
                    }
                ],
            },
        }
        extras = [
            item
            for item in values.get("extraObjects", [])
            if not (
                isinstance(item, dict)
                and item.get("kind") == "NetworkPolicy"
                and item.get("metadata", {}).get("name") == f"{release}-grafana"
            )
        ]
        values["extraObjects"] = [*extras, policy]
        if row.get("values") != values:
            row["values"] = values
            changed = True
    return changed


def validate_grafana_startup(values: Mapping[str, Any]) -> None:
    """Keep managed Grafana on the pinned image's canonical configuration path."""
    if (
        values.get("command")
        or values.get("args")
        or any(
            key.startswith("GF_PATHS_CONFIG")
            for field in ("env", "envValueFrom")
            for key in values.get(field, {})
        )
    ):
        raise ValueError("Managed Grafana configuration path and startup cannot be overridden")


def grafana_database_values(payload: dict[str, Any], chart_row: dict[str, Any]) -> dict[str, Any]:
    if not database_component_id():
        return {}
    values = chart_row.get("values", {})
    validate_grafana_startup(values)
    release = str(chart_row.get("release-name") or "grafana")
    if values.get("persistence", {}).get("enabled") or values.get("useStatefulSet"):
        raise ValueError("Managed Grafana uses PostgreSQL and stateless Deployment replicas")
    if (
        (values.get("fullnameOverride") and values["fullnameOverride"] != release)
        or values.get("namespaceOverride")
        or values.get("autoscaling", {}).get("enabled")
    ):
        raise ValueError(
            "Managed Grafana identity and replica count must come from its release row"
        )
    if (
        values.get("envFromSecret")
        or values.get("envRenderSecret")
        or values.get("envFromSecrets")
        or values.get("envFromConfigMaps")
        or any(
            key.startswith(("GF_DATABASE_", "GF_SECURITY_SECRET_KEY"))
            for key in values.get("env", {})
        )
    ):
        raise ValueError(
            "Managed Grafana database and encryption settings cannot be overridden by environment"
        )
    rows = database_rows(payload, app_chart_target_ref(chart_row))
    if len(rows) != 1:
        raise ValueError("Managed Grafana requires exactly one PostgreSQL component on its target")
    namespace, database_release = database_identity(rows[0])
    if namespace != str(chart_row.get("namespace") or "observability"):
        raise ValueError("Managed Grafana and PostgreSQL must use the same namespace")
    _, application = database_secret_names(rows[0])
    return {
        "values.fullnameOverride": release,
        "values.annotations": {
            OWNER_ANNOTATION: app_owner(payload, app_chart_target_ref(chart_row))
        },
        "values.envValueFrom": {
            "GF_DATABASE_PASSWORD": {"secretKeyRef": {"name": application, "key": "password"}},
            "GF_SECURITY_SECRET_KEY": {
                "secretKeyRef": {"name": f"{release}-encryption", "key": "secret-key"}
            },
        },
        "values.grafana\\.ini.database": {
            "type": "postgres",
            "host": f"{database_release}.{namespace}.svc:5432",
            "name": "grafana",
            "user": "grafana",
            "ssl_mode": "disable",
        },
        "values.grafana\\.ini.unified_alerting": {
            # The upstream chart already injects POD_IP through the Downward API.
            "enabled": True,
            "ha_listen_address": "${POD_IP}:9094",
            "ha_advertise_address": "${POD_IP}:9094",
            "ha_peers": f"{release}-headless.{namespace}.svc:9094",
            "ha_reconnect_timeout": "2m",
        },
    }


def database_readiness_patch(release: str) -> dict[str, Any]:
    # Chart 0.20.6 exposes probe timings but hardcodes pg_isready. SQL must use
    # loopback: probing the Service would deadlock its own endpoint readiness.
    return {
        "target": {"group": "apps", "version": "v1", "kind": "StatefulSet", "name": release},
        "patch": yaml.safe_dump(
            [
                {
                    "op": "replace",
                    "path": "/spec/template/spec/containers/0/readinessProbe/exec/command",
                    "value": [
                        "/bin/sh",
                        "-ec",
                        'PGPASSWORD="$CUSTOM_PASSWORD" psql -h 127.0.0.1 '
                        '-U "$CUSTOM_USER" -d "$CUSTOM_DB" -v ON_ERROR_STOP=1 -Atqc "SELECT 1" '
                        "| grep -qx 1",
                    ],
                }
            ],
            sort_keys=False,
        ),
    }
