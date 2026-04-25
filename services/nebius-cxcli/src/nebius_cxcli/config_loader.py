"""Config loading helpers (runtime source-driven mode)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config_model import is_dynamic_payload, to_runtime_payload
from .mk8s_gpu import (
    ensure_mk8s_gpu_app_rows,
    materialize_mk8s_gpu_app_values,
    normalize_mk8s_gpu_project_validation_settings,
)
from .observability import (
    ensure_observability_app_rows,
    materialize_observability_app_values,
    materialize_observability_infra_values,
    normalize_observability_project_settings,
)
from .runtime_config import AttrDict, to_plain_data, wrap_runtime_config
from .runtime_validation import validate_dynamic_payload_structure, validate_runtime_payload
from .ssh_public_keys import normalize_runtime_ssh_public_key_inputs


def normalize_runtime_config_payload(
    payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> bool:
    changed = normalize_runtime_ssh_public_key_inputs(payload, base_dir=base_dir)
    if normalize_mk8s_gpu_project_validation_settings(payload):
        changed = True
    if ensure_mk8s_gpu_app_rows(payload):
        changed = True
    if materialize_mk8s_gpu_app_values(payload):
        changed = True
    if normalize_observability_project_settings(payload):
        changed = True
    if ensure_observability_app_rows(payload):
        changed = True
    if materialize_observability_infra_values(payload):
        changed = True
    if materialize_observability_app_values(payload):
        changed = True
    return changed


def validate_config(payload: dict[str, Any], *, base_dir: Path | None = None) -> AttrDict:
    """Validate payload with runtime rules and wrap for attribute access."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config.yaml must use dynamic model with 'infra.components[]' and 'apps.charts[]'"
        )
    normalize_runtime_config_payload(payload, base_dir=base_dir)
    validate_dynamic_payload_structure(payload)
    validate_runtime_payload(payload)
    normalized = to_runtime_payload(payload)
    return wrap_runtime_config(normalized)


def load_config(path: Path, *, persist_normalized: bool = False) -> AttrDict:
    """Load one config.yaml file and return runtime-wrapped config."""
    if not path.exists():
        raise ValueError(f"Config file not found: {path}")
    if path.is_dir():
        raise ValueError(
            "Expected a project config.yaml file path, but got a directory: "
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
    changed = normalize_runtime_config_payload(payload, base_dir=path.parent)
    if changed and persist_normalized:
        path.write_text(dump_yaml(payload), encoding="utf-8")
    return validate_config(payload, base_dir=path.parent)


def dump_yaml(data: dict[str, Any]) -> str:
    """Serialize data to deterministic YAML output."""
    return yaml.safe_dump(to_plain_data(data), sort_keys=False)
