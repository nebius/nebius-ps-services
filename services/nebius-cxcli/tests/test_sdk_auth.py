from __future__ import annotations

import logging
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
    monkeypatch.setattr(cli_config_module, "Config", FakeConfig, raising=False)
    nebius_module.sdk = sdk_module  # type: ignore[attr-defined]
    aio_module.cli_config = cli_config_module  # type: ignore[attr-defined]
    nebius_module.aio = aio_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nebius", nebius_module)
    monkeypatch.setitem(sys.modules, "nebius.sdk", sdk_module)
    monkeypatch.setitem(sys.modules, "nebius.aio", aio_module)
    monkeypatch.setitem(sys.modules, "nebius.aio.cli_config", cli_config_module)


def test_deleted_key_refresh_log_filter_only_suppresses_expected_traceback() -> None:
    expected_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: Public Key not exists: JwtKeyNotExists",
        (),
        None,
    )
    unrelated_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: network unavailable",
        (),
        None,
    )

    assert sdk_auth.deleted_key_refresh_log(expected_record)
    assert not sdk_auth.deleted_key_refresh_log(unrelated_record)


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


def test_init_nebius_sdk_uses_iam_token_env_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "iam-token-123")

    sdk = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials"] == "iam-token-123"
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_fetches_iam_token_from_cli_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: type("CP", (), {"stdout": "cli-token-456\n"})(),
    )

    sdk = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials"] == "cli-token-456"
    assert sdk.kwargs["parent_id"] == "project-1"
    assert sdk_auth.os.environ["NEBIUS_IAM_TOKEN"] == "cli-token-456"


def test_init_nebius_sdk_can_disable_cli_token_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token fallback should be disabled"),
    )

    with pytest.raises(RuntimeError, match="Failed to initialize Nebius SDK credentials"):
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="test",
            allow_cli_token=False,
        )


def test_init_nebius_sdk_falls_back_to_sdk_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("nebius not found")),
    )

    sdk = sdk_auth.init_nebius_sdk(
        profile="dev",
        endpoint="api.example.invalid",
        parent_id="project-1",
        context="test",
    )

    assert sdk.kwargs["parent_id"] == "project-1"
    config = sdk.kwargs["config_reader"]
    assert config.kwargs == {"profile": "dev", "endpoint": "api.example.invalid"}


def test_init_nebius_sdk_prefer_operator_auth_uses_sdk_config_before_service_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("SDK config should be tried before CLI token"),
    )

    sdk = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert "config_reader" in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_falls_back_to_service_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("nebius not found")),
    )

    sdk = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_logs_config_auth_failure_at_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    class FailingConfig:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("config boom")

    cli_config_module = sys.modules["nebius.aio.cli_config"]
    monkeypatch.setattr(cli_config_module, "Config", FailingConfig)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("service account fallback should win"),
    )

    with caplog.at_level(logging.DEBUG, logger="nebius_cxcli.sdk_auth"):
        sdk = sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="quota assessment",
            prefer_operator_auth=True,
        )

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert (
        "Nebius SDK config auth attempt failed for quota assessment: config boom"
        in caplog.text
    )
