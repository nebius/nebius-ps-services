from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from nebius_cxcli import sdk_auth


def _install_fake_nebius_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    nebius_module = ModuleType("nebius")
    sdk_module = ModuleType("nebius.sdk")
    aio_module = ModuleType("nebius.aio")
    cli_config_module = ModuleType("nebius.aio.cli_config")

    class FakeSDK:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    class FakeConfig:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    sdk_module.SDK = FakeSDK  # type: ignore[attr-defined]
    cli_config_module.Config = FakeConfig  # type: ignore[attr-defined]
    nebius_module.sdk = sdk_module  # type: ignore[attr-defined]
    aio_module.cli_config = cli_config_module  # type: ignore[attr-defined]
    nebius_module.aio = aio_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nebius", nebius_module)
    monkeypatch.setitem(sys.modules, "nebius.sdk", sdk_module)
    monkeypatch.setitem(sys.modules, "nebius.aio", aio_module)
    monkeypatch.setitem(sys.modules, "nebius.aio.cli_config", cli_config_module)


def test_init_nebius_sdk_prefers_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    credentials_file = tmp_path / "auth.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)

    sdk = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials_file_name"] == credentials_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_uses_service_account_key_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))

    sdk = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_falls_back_to_sdk_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)

    sdk = sdk_auth.init_nebius_sdk(
        profile="dev",
        endpoint="api.example.invalid",
        parent_id="project-1",
        context="test",
    )

    assert sdk.kwargs["parent_id"] == "project-1"
    config = sdk.kwargs["config_reader"]
    assert config.kwargs == {"profile": "dev", "endpoint": "api.example.invalid"}
