"""Soperator companion app value materialization."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .component_defaults import read_component_path, set_component_path
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data

SOPERATOR_APP_ID = "soperator"
SOPERATOR_ACTIVECHECKS_APP_ID = "soperator-activechecks"
ACTIVECHECKS_CLUSTER_REF_PATH = "values.slurmClusterRefName"
SSH_CHECK_LOGIN_ENV_PATH = "values.checks.ssh-check.k8sJobSpec.jobContainer.env"
SSH_CHECK_LOGIN_ENV_NAME = "NUM_OF_LOGIN_NODES"


def _as_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    payload = to_plain_data(value)
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _app_chart_rows(payload: Mapping[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    charts = _mapping(apps).get("charts")
    return charts if isinstance(charts, list) else []


def _enabled_app_rows(payload: Mapping[str, Any], app_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in _app_chart_rows(payload)
        if isinstance(row, dict)
        and bool(row.get("enabled", False))
        and component_type_id(row) == app_id
    )


def _row_target_keys(row: Mapping[str, Any], *, app_id: str) -> tuple[str, ...]:
    keys: list[str] = []
    target_ref = app_chart_target_ref(row)
    instance_id = component_instance_id(row)
    if target_ref:
        keys.append(target_ref)
    if instance_id and instance_id != app_id and instance_id not in keys:
        keys.append(instance_id)
    return tuple(keys)


def _soperator_row_for_activechecks(
    activechecks_row: Mapping[str, Any],
    *,
    soperator_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    active_keys = _row_target_keys(activechecks_row, app_id=SOPERATOR_ACTIVECHECKS_APP_ID)
    if active_keys:
        for row in soperator_rows:
            soperator_keys = _row_target_keys(row, app_id=SOPERATOR_APP_ID)
            if any(key in soperator_keys for key in active_keys):
                return row
    if len(soperator_rows) == 1:
        return soperator_rows[0]
    return None


def _positive_count(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _login_node_count(soperator_row: Mapping[str, Any]) -> int | None:
    return _positive_count(read_component_path(soperator_row, "values.slurmNodes.login.size"))


def _slurm_cluster_name(soperator_row: Mapping[str, Any]) -> str:
    cluster_name = str(read_component_path(soperator_row, "values.clusterName") or "").strip()
    if cluster_name:
        return cluster_name
    keys = _row_target_keys(soperator_row, app_id=SOPERATOR_APP_ID)
    return keys[0] if keys else ""


def _merge_login_env(activechecks_row: dict[str, Any], *, login_count: int) -> None:
    existing = read_component_path(activechecks_row, SSH_CHECK_LOGIN_ENV_PATH)
    env: list[Any] = copy.deepcopy(existing) if isinstance(existing, list) else []
    managed_item = {"name": SSH_CHECK_LOGIN_ENV_NAME, "value": str(login_count)}

    replaced = False
    merged: list[Any] = []
    for item in env:
        if isinstance(item, Mapping) and str(item.get("name") or "") == SSH_CHECK_LOGIN_ENV_NAME:
            if not replaced:
                merged.append(managed_item)
                replaced = True
            continue
        merged.append(item)
    if not replaced:
        merged.append(managed_item)
    set_component_path(activechecks_row, SSH_CHECK_LOGIN_ENV_PATH, merged)


def materialize_soperator_companion_app_values(payload_or_config: Any) -> bool:
    """Keep Soperator companion chart defaults aligned with the Soperator row."""
    payload = _as_payload(payload_or_config)
    soperator_rows = _enabled_app_rows(payload, SOPERATOR_APP_ID)
    activechecks_rows = _enabled_app_rows(payload, SOPERATOR_ACTIVECHECKS_APP_ID)
    if not soperator_rows or not activechecks_rows:
        return False

    changed = False
    for activechecks_row in activechecks_rows:
        soperator_row = _soperator_row_for_activechecks(
            activechecks_row,
            soperator_rows=soperator_rows,
        )
        if soperator_row is None:
            continue
        login_count = _login_node_count(soperator_row)
        cluster_name = _slurm_cluster_name(soperator_row)
        before = copy.deepcopy(activechecks_row)
        if cluster_name:
            set_component_path(activechecks_row, ACTIVECHECKS_CLUSTER_REF_PATH, cluster_name)
        if login_count is not None:
            _merge_login_env(activechecks_row, login_count=login_count)
        if activechecks_row != before:
            changed = True
    return changed


__all__ = [
    "SOPERATOR_ACTIVECHECKS_APP_ID",
    "SOPERATOR_APP_ID",
    "materialize_soperator_companion_app_values",
]
