"""Soperator child chart value materialization."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .component_defaults import read_component_path, set_component_path
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data
from .slack_notifier_runtime import soperator_notifier_mysterybox_secret_refs

SOPERATOR_APP_ID = "soperator"
SOPERATOR_CHECKS_VALUES_KEY = "soperator-checks"
SOPERATOR_ACTIVECHECKS_VALUES_KEY = "soperator-activechecks"
ACTIVECHECKS_ENABLED_PATH = f"values.{SOPERATOR_ACTIVECHECKS_VALUES_KEY}.enabled"
CHECKS_ENABLED_PATH = f"values.{SOPERATOR_CHECKS_VALUES_KEY}.enabled"
DCGM_EXPORTER_VALUES_KEY = "soperator-dcgm-exporter"
DCGM_EXPORTER_ENABLED_PATH = f"values.{DCGM_EXPORTER_VALUES_KEY}.enabled"
REBOOTER_ENABLED_PATH = "values.rebooter.enabled"
ACTIVECHECKS_CLUSTER_REF_PATH = f"values.{SOPERATOR_ACTIVECHECKS_VALUES_KEY}.slurmClusterRefName"
SSH_CHECK_LOGIN_ENV_PATH = (
    f"values.{SOPERATOR_ACTIVECHECKS_VALUES_KEY}.checks.ssh-check.k8sJobSpec.jobContainer.env"
)
SSH_CHECK_LOGIN_ENV_NAME = "NUM_OF_LOGIN_NODES"
GENERATED_NULL_HELM_DEFAULT_PATHS = (
    ("certManager", "enabled"),
    ("mariadb-operator", "webhook", "enabled"),
    ("mariadb-operator", "webhook", "cert", "certManager", "enabled"),
)


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


def _deploy_target_rows(payload: Mapping[str, Any]) -> list[Any]:
    deploy = payload.get("deploy")
    targets = _mapping(deploy).get("targets")
    return targets if isinstance(targets, list) else []


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


def _activechecks_enabled(soperator_row: Mapping[str, Any]) -> bool:
    return read_component_path(soperator_row, ACTIVECHECKS_ENABLED_PATH) is True


def _checks_enabled(soperator_row: Mapping[str, Any]) -> bool:
    return read_component_path(soperator_row, CHECKS_ENABLED_PATH) is True


def _dcgm_exporter_enabled(soperator_row: Mapping[str, Any]) -> bool:
    return read_component_path(soperator_row, DCGM_EXPORTER_ENABLED_PATH) is True


def _rebooter_enabled(soperator_row: Mapping[str, Any]) -> bool:
    return read_component_path(soperator_row, REBOOTER_ENABLED_PATH) is True


def _warning_target_label(soperator_row: Mapping[str, Any]) -> str:
    keys = _row_target_keys(soperator_row, app_id=SOPERATOR_APP_ID)
    return keys[0] if keys else "unknown target"


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


def _merge_login_env(soperator_row: dict[str, Any], *, login_count: int) -> None:
    existing = read_component_path(soperator_row, SSH_CHECK_LOGIN_ENV_PATH)
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
    set_component_path(soperator_row, SSH_CHECK_LOGIN_ENV_PATH, merged)


def _delete_none_path(mapping: dict[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = mapping
    for segment in path[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(segment)
    if not isinstance(current, dict):
        return False
    leaf = path[-1]
    if leaf not in current or current[leaf] is not None:
        return False
    del current[leaf]
    return True


def _prune_generated_null_helm_defaults(value: Any) -> bool:
    changed = False
    if not isinstance(value, dict):
        return False
    for path in GENERATED_NULL_HELM_DEFAULT_PATHS:
        if _delete_none_path(value, path):
            changed = True
    return changed


def _target_row_by_ref(payload: Mapping[str, Any], target_ref: str) -> dict[str, Any] | None:
    if not target_ref:
        return None
    for row in _deploy_target_rows(payload):
        if not isinstance(row, dict):
            continue
        if component_instance_id(row) == target_ref:
            return row
    return None


def _append_unique_text(items: list[Any], value: str) -> bool:
    token = str(value or "").strip()
    if not token or token in {str(item).strip() for item in items}:
        return False
    items.append(token)
    return True


def _materialize_notifier_mysterybox_sync(payload: dict[str, Any]) -> bool:
    changed = False
    for ref in soperator_notifier_mysterybox_secret_refs(payload):
        target_ref = str(ref.get("target_ref") or "").strip()
        namespace = str(ref.get("namespace") or "").strip()
        target_row = _target_row_by_ref(payload, target_ref)
        if target_row is None or not namespace:
            continue
        before = copy.deepcopy(target_row)
        secrets = target_row.setdefault("secrets", {})
        if not isinstance(secrets, dict):
            secrets = {}
            target_row["secrets"] = secrets
        mysterybox = secrets.setdefault("mysterybox", {})
        if not isinstance(mysterybox, dict):
            mysterybox = {}
            secrets["mysterybox"] = mysterybox
        if mysterybox.get("enabled") is False:
            continue
        mysterybox.setdefault("enabled", True)
        sync_namespaces = mysterybox.get("sync_namespaces")
        if not isinstance(sync_namespaces, list):
            sync_namespaces = []
            mysterybox["sync_namespaces"] = sync_namespaces
        _append_unique_text(sync_namespaces, namespace)
        if target_row != before:
            changed = True
    return changed


def materialize_soperator_child_chart_values(payload_or_config: Any) -> bool:
    """Keep Soperator child chart defaults aligned with the Soperator row."""
    payload = _as_payload(payload_or_config)
    soperator_rows = _enabled_app_rows(payload, SOPERATOR_APP_ID)
    if not soperator_rows:
        return False

    changed = False
    if _materialize_notifier_mysterybox_sync(payload):
        changed = True
    for soperator_row in soperator_rows:
        values = soperator_row.get("values")
        if isinstance(values, dict) and _prune_generated_null_helm_defaults(values):
            changed = True
        if not _activechecks_enabled(soperator_row):
            continue
        login_count = _login_node_count(soperator_row)
        cluster_name = _slurm_cluster_name(soperator_row)
        before = copy.deepcopy(soperator_row)
        set_component_path(soperator_row, CHECKS_ENABLED_PATH, True)
        if cluster_name:
            set_component_path(soperator_row, ACTIVECHECKS_CLUSTER_REF_PATH, cluster_name)
        if login_count is not None:
            _merge_login_env(soperator_row, login_count=login_count)
        if soperator_row != before:
            changed = True
    return changed


def soperator_child_chart_warnings(payload_or_config: Any) -> tuple[str, ...]:
    """Return warnings for opt-in Soperator services with production impact."""
    payload = _as_payload(payload_or_config)
    warnings: list[str] = []
    for soperator_row in _enabled_app_rows(payload, SOPERATOR_APP_ID):
        target_label = _warning_target_label(soperator_row)
        activechecks_enabled = _activechecks_enabled(soperator_row)
        if activechecks_enabled:
            warnings.append(
                "Soperator ActiveChecks are enabled "
                f"for target {target_label}; they install recurring Slurm checks "
                "including NCCL, CUDA, GPU stress, RDMA, and maintenance jobs that "
                "can consume full GPU nodes and RDMA bandwidth while training is "
                "running. Use them only for benchmarking/diagnostic clusters or "
                "maintenance windows, not production training clusters."
            )
        if _checks_enabled(soperator_row) and not activechecks_enabled:
            warnings.append(
                "Soperator checks controller is enabled "
                f"for target {target_label}; it does not run GPU benchmarks by "
                "itself, but it reconciles ActiveCheck resources and node "
                "maintenance/degraded status into SlurmNodeDrain and "
                "SlurmNodeReboot conditions. NebiusMaintenanceScheduled is a "
                "graceful maintenance drain/node handoff signal; SlurmNodeReboot "
                "is the actual host reboot signal when the rebooter is enabled. "
                "Keep it disabled unless this cluster intentionally uses "
                "Soperator ActiveChecks or advanced Soperator-managed node "
                "maintenance automation."
            )
        if _rebooter_enabled(soperator_row):
            warnings.append(
                "Soperator NodeConfigurator rebooter is enabled "
                f"for target {target_label}; it installs a privileged host-level "
                "helper with node/pod RBAC that can cordon, NoExecute-drain, and "
                "reboot worker nodes. SlurmNodeDrain only performs graceful "
                "maintenance drain/node handoff; actual host reboot happens only "
                "after SlurmNodeReboot is set. Keep it disabled unless advanced "
                "Soperator-managed node maintenance is intentionally configured."
            )
        if _dcgm_exporter_enabled(soperator_row):
            warnings.append(
                "Soperator DCGM job-mapping exporter is enabled "
                f"for target {target_label}; cxcli's standard GPU telemetry path "
                "uses the NVIDIA GPU Operator DCGM exporter plus the Nebius "
                "Observability Agent. Enable the Soperator exporter only when "
                "Slurm per-job DCGM labels are required, and avoid duplicate DCGM "
                "scraping."
            )
    return tuple(dict.fromkeys(warnings))


__all__ = [
    "SOPERATOR_ACTIVECHECKS_VALUES_KEY",
    "SOPERATOR_APP_ID",
    "materialize_soperator_child_chart_values",
    "soperator_child_chart_warnings",
]
