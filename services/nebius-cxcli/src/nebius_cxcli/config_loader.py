"""Config loading helpers (runtime source-driven mode)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config_model import is_dynamic_payload, to_runtime_payload
from .runtime_config import AttrDict, to_plain_data, wrap_runtime_config
from .runtime_validation import validate_dynamic_payload_structure, validate_runtime_payload


def validate_config(payload: dict[str, Any]) -> AttrDict:
    """Validate payload with runtime rules and wrap for attribute access."""
    if not is_dynamic_payload(payload):
        raise ValueError(
            "config.yaml must use dynamic model with 'infra.components[]' and "
            "'apps.releases[]'"
        )
    validate_dynamic_payload_structure(payload)
    validate_runtime_payload(payload)
    normalized = to_runtime_payload(payload)
    return wrap_runtime_config(normalized)


def load_config(path: Path) -> AttrDict:
    """Load one config.yaml file and return runtime-wrapped config."""
    if not path.exists():
        raise ValueError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    return validate_config(payload)


def dump_yaml(data: dict[str, Any]) -> str:
    """Serialize data to deterministic YAML output."""
    return yaml.safe_dump(to_plain_data(data), sort_keys=False)
