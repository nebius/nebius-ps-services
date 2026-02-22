"""Config loading and strict schema validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import ConfigV1
from .schema import validate_config as validate_config_dict


def _format_validation_error(exc: ValidationError) -> str:
    details = []
    for err in exc.errors():
        location = " -> ".join(str(item) for item in err["loc"])
        details.append(f"  - {location}: {err['msg']}")
    return "\n".join(details)


def validate_config(payload: dict[str, Any]) -> ConfigV1:
    """Validate a parsed mapping against schema."""
    try:
        return validate_config_dict(payload)
    except ValidationError as exc:
        joined = _format_validation_error(exc)
        raise ValueError(f"Configuration validation failed:\n{joined}") from exc


def load_config(path: Path) -> ConfigV1:
    """Load and validate one config.yaml file."""
    if not path.exists():
        raise ValueError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    return validate_config(payload)


def dump_yaml(data: dict[str, Any]) -> str:
    """Serialize data to deterministic YAML output."""
    return yaml.safe_dump(data, sort_keys=False)
