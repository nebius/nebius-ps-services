"""Config loading helpers (runtime source-driven mode)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .config_model import is_dynamic_payload, to_runtime_payload
from .deploy_targets import (
    TARGET_REF_FIELD,
    materialize_app_chart_target_refs,
    strip_app_chart_target_refs,
)
from .mk8s_gpu import (
    ensure_mk8s_gpu_app_rows,
    materialize_mk8s_gpu_app_values,
    normalize_mk8s_gpu_project_deployment_testing_settings,
    prune_inactive_mk8s_gpu_app_rows,
)
from .mk8s_node_group_defaults import prune_inactive_mk8s_node_group_defaults
from .mysterybox_eso import (
    ensure_mysterybox_eso_app_rows,
    normalize_mysterybox_eso_project_settings,
    strip_mysterybox_eso_app_values,
)
from .nfs_csi import ensure_nfs_csi_app_rows
from .observability import (
    ensure_observability_app_rows,
    materialize_observability_infra_values,
    normalize_observability_project_settings,
    strip_observability_generated_app_values,
)
from .runtime_config import AttrDict, to_plain_data, wrap_runtime_config
from .runtime_validation import validate_dynamic_payload_structure, validate_runtime_payload
from .soperator_child_charts import materialize_soperator_child_chart_values
from .soperator_config_materialization import _materialize_soperator_component_defaults
from .soperator_validation import normalize_soperator_project_deployment_testing_settings
from .ssh_public_keys import normalize_runtime_ssh_public_key_inputs


def _write_text_atomic(path: Path, content: str, *, file_mode: int) -> None:
    fd = -1
    temporary: Path | None = None
    try:
        fd, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            os.fchmod(handle.fileno(), file_mode)
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def normalize_runtime_config_payload(
    payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> bool:
    changed = normalize_runtime_ssh_public_key_inputs(payload, base_dir=base_dir)
    apps_node = payload.get("apps")
    chart_rows = apps_node.get("charts", []) if isinstance(apps_node, dict) else []
    preexisting_target_ref_row_ids = {
        id(row) for row in chart_rows if isinstance(row, dict) and TARGET_REF_FIELD in row
    }
    if materialize_app_chart_target_refs(payload):
        changed = True
    if any(
        isinstance(row, dict) and row.get("id") == "soperator" and row.get("enabled") is True
        for row in chart_rows
    ) and _materialize_soperator_component_defaults(payload):
        changed = True
    if normalize_mk8s_gpu_project_deployment_testing_settings(payload):
        changed = True
    if normalize_soperator_project_deployment_testing_settings(payload):
        changed = True
    if prune_inactive_mk8s_gpu_app_rows(
        payload,
        preexisting_target_ref_row_ids=preexisting_target_ref_row_ids,
    ):
        changed = True
    if ensure_mk8s_gpu_app_rows(payload):
        changed = True
    if materialize_mk8s_gpu_app_values(payload):
        changed = True
    if materialize_soperator_child_chart_values(payload):
        changed = True
    if ensure_nfs_csi_app_rows(payload):
        changed = True
    if normalize_observability_project_settings(payload):
        changed = True
    if ensure_observability_app_rows(payload):
        changed = True
    if materialize_observability_infra_values(payload):
        changed = True
    if prune_inactive_mk8s_node_group_defaults(payload):
        changed = True
    if strip_observability_generated_app_values(payload):
        changed = True
    if normalize_mysterybox_eso_project_settings(payload):
        changed = True
    if ensure_mysterybox_eso_app_rows(payload):
        changed = True
    if strip_mysterybox_eso_app_values(payload):
        changed = True
    if strip_app_chart_target_refs(payload):
        changed = True
    return changed


def validate_config(payload: dict[str, Any], *, base_dir: Path | None = None) -> AttrDict:
    """Validate payload with runtime rules and wrap for attribute access."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config.yaml must use dynamic model with 'infra.components[]' and 'apps.charts[]'"
        )
    validate_dynamic_payload_structure(payload)
    normalize_runtime_config_payload(payload, base_dir=base_dir)
    validate_dynamic_payload_structure(payload)
    validate_runtime_payload(payload)
    normalized = to_runtime_payload(payload)
    return wrap_runtime_config(normalized)


def load_config(path: Path, *, persist_normalized: bool = False) -> AttrDict:
    """Load one config.yaml file and return runtime-wrapped config."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        raise ValueError(f"Config file not found: {path}") from None
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        raise ValueError(
            "Expected a single-link regular project config.yaml file path: "
            f"{path}. Pass <tenant-folder>/<project-folder>/config.yaml."
        )
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config.yaml must use dynamic model with 'infra.components[]' and 'apps.charts[]'"
        )
    before = dump_yaml(payload) if persist_normalized else ""
    config = validate_config(payload, base_dir=path.parent)
    if persist_normalized and dump_yaml(payload) != before:
        _write_text_atomic(
            path,
            dump_yaml(payload),
            file_mode=stat.S_IMODE(path_stat.st_mode),
        )
    return config


def dump_yaml(data: dict[str, Any]) -> str:
    """Serialize data to deterministic YAML output."""
    return yaml.safe_dump(to_plain_data(data), sort_keys=False)
