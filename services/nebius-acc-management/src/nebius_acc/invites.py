from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config_template import INVITE_SCHEMA_VERSION
from .errors import ConfigError


@dataclass(frozen=True)
class InviteSpec:
    tenant_id: str
    invites: dict[str, list[str]]


def load_invite_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Invite file not found: {path}")
    text = path.read_text()
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ConfigError("Invite file must be .json, .yaml, or .yml")
    if data is None:
        raise ConfigError("Invite file is empty")
    if not isinstance(data, dict):
        raise ConfigError("Invite file must contain a JSON/YAML object")
    return data


def parse_invite_file_spec(
    data: dict[str, Any], *, expected_version: int = INVITE_SCHEMA_VERSION
) -> InviteSpec:
    version = data.get("version")
    if version is None:
        raise ConfigError("Invite file version is required")
    if not isinstance(version, int) or version < 1:
        raise ConfigError("Invite file version must be an integer >= 1")
    if version != expected_version:
        raise ConfigError(
            f"Unsupported invite file version: {version}. Expected version {expected_version}."
        )

    tenant_id = data.get("tenant_id")
    if not tenant_id or not isinstance(tenant_id, str):
        raise ConfigError("Invite file requires tenant_id")

    raw_invites = data.get("invites")
    if raw_invites is None or not isinstance(raw_invites, dict):
        raise ConfigError("Invite file requires an 'invites' mapping")

    invites: dict[str, list[str]] = {}
    for project_name, entries in raw_invites.items():
        if not isinstance(project_name, str) or not project_name.strip():
            raise ConfigError("Invite project names must be non-empty strings")
        invites[project_name] = _parse_invite_entries(entries, context=f"invites.{project_name}")

    if not invites:
        raise ConfigError("Invite file must include at least one project invite")

    return InviteSpec(tenant_id=tenant_id, invites=invites)


def _parse_invite_entries(raw: Any, *, context: str) -> list[str]:
    if raw in (None, {}):
        return []
    if isinstance(raw, list):
        items = raw
    else:
        raise ConfigError(f"{context} must be a list of invite entries")

    emails: list[str] = []
    for item in items:
        if isinstance(item, str):
            email = item.strip()
        elif isinstance(item, dict):
            email = str(item.get("email") or "").strip()
        else:
            raise ConfigError(f"{context} entries must be strings or objects with 'email'")
        if not email:
            raise ConfigError(f"{context} entries require a non-empty email")
        emails.append(email)
    return emails
