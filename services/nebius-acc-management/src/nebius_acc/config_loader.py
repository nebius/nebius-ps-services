from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from .config_template import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_CONFIG_TEMPLATE,
    DEFAULT_INVITE_SUFFIX,
    DEFAULT_INVITE_TEMPLATE,
    DEFAULT_QUOTA_SUFFIX,
    DEFAULT_QUOTA_TEMPLATE,
)
from .errors import ConfigError


@dataclass(frozen=True)
class ConfigFile:
    path: Path
    data: dict[str, Any]


def load_config(path: Path) -> ConfigFile:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = path.read_text()
    data = yaml.safe_load(raw)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a YAML object at the top level")
    validate_config_data(data, path)
    return ConfigFile(path=path, data=data)


def validate_config_data(data: dict[str, Any], path: Path | None = None) -> None:
    schema = _load_schema()
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: err.path)
    if errors:
        formatted = "\n".join(_format_error(err) for err in errors[:10])
        location = f" in {path}" if path else ""
        raise ConfigError(f"Config schema validation failed{location}:\n{formatted}")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise ConfigError("Config version must be an integer >= 1")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported config version: {version}. Expected version {CONFIG_SCHEMA_VERSION}."
        )


def write_default_config(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigError(f"Config file already exists: {path}")
    path.write_text(DEFAULT_CONFIG_TEMPLATE)


def write_quota_template(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigError(f"Quota file already exists: {path}")
    path.write_text(DEFAULT_QUOTA_TEMPLATE)


def write_invite_template(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigError(f"Invite file already exists: {path}")
    path.write_text(DEFAULT_INVITE_TEMPLATE)


def derive_quota_path(config_path: Path) -> Path:
    name = config_path.name
    if name.endswith(".config.yaml"):
        base = name[: -len(".config.yaml")]
        return config_path.with_name(f"{base}{DEFAULT_QUOTA_SUFFIX}")
    if name.endswith(".config.yml"):
        base = name[: -len(".config.yml")]
        return config_path.with_name(f"{base}-quota.config.yml")
    if config_path.suffix in {".yaml", ".yml"}:
        return config_path.with_name(f"{config_path.stem}{DEFAULT_QUOTA_SUFFIX}")
    return config_path.with_name(f"{config_path.stem}{DEFAULT_QUOTA_SUFFIX}")


def derive_invite_path(config_path: Path) -> Path:
    name = config_path.name
    if name.endswith(".config.yaml"):
        base = name[: -len(".config.yaml")]
        return config_path.with_name(f"{base}{DEFAULT_INVITE_SUFFIX}")
    if name.endswith(".config.yml"):
        base = name[: -len(".config.yml")]
        return config_path.with_name(f"{base}-invite.config.yml")
    if config_path.suffix in {".yaml", ".yml"}:
        return config_path.with_name(f"{config_path.stem}{DEFAULT_INVITE_SUFFIX}")
    return config_path.with_name(f"{config_path.stem}{DEFAULT_INVITE_SUFFIX}")


def resolve_config_path(path: Path | None) -> Path:
    return path or Path.cwd() / DEFAULT_CONFIG_FILENAME


def _load_schema() -> dict[str, Any]:
    schema_path = Path(__file__).with_name("config_schema.json")
    if not schema_path.exists():
        raise ConfigError("Config schema file is missing from the package")
    return json.loads(schema_path.read_text())


def _format_error(error: Exception) -> str:
    try:
        err = error  # type: ignore[assignment]
        path = ".".join(str(p) for p in err.path) or "<root>"
        return f"- {path}: {err.message}"
    except Exception:
        return f"- {error}"
