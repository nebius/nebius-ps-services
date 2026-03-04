from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pytest

from nebius_cxcli.runtime_plugin_validation import run_runtime_validation_plugins


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return default
        if segment not in current:
            return default
        current = current[segment]
    return current


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def test_runtime_validation_plugins_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS", raising=False)
    payload = {"infra": {"ssh_user_name": "BAD USER"}}

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS", "none")
    payload = {"infra": {"ssh_user_name": "BAD USER"}}

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
        env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
    )


def test_runtime_validation_plugins_can_be_enabled_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS",
        "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules",
    )
    payload = {"infra": {"ssh_user_name": "BAD USER"}}

    with pytest.raises(ValueError, match="infra.ssh_user_name"):
        run_runtime_validation_plugins(
            payload=payload,
            get_path=_get_path,
            as_text=_as_text,
            id_pattern=re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"),
            env_var_pattern=re.compile(r"^[A-Z_][A-Z0-9_]*$"),
        )
