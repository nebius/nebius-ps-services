from __future__ import annotations

from pathlib import Path

import pytest

import nebius_cxcli.email_settings as email_settings
from nebius_cxcli.email_settings import (
    EmailSettings,
    disable_email_settings,
    email_environment_variables,
    email_runtime_settings,
    email_secret_values,
    load_email_settings,
    write_email_settings,
)


def test_load_email_settings_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "email.yaml"

    settings = load_email_settings(explicit=config_path)

    assert settings == EmailSettings()
    assert settings.enabled is False


def test_write_and_load_email_settings_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "email.yaml"
    settings = EmailSettings(
        host="smtp.example.com",
        port=2525,
        starttls=True,
        from_addr="deployments@example.com",
        username="mailer",
        password="secret",
    )

    written_path = write_email_settings(settings, explicit=config_path)
    loaded = load_email_settings(explicit=config_path)

    assert written_path == config_path.resolve()
    assert loaded == settings
    assert written_path.stat().st_mode & 0o777 == 0o600


def test_write_email_settings_preserves_existing_file_when_temp_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "email.yaml"
    original = "smtp:\n  host: old.example.com\n"
    config_path.write_text(original, encoding="utf-8")
    config_path.chmod(0o644)

    def _fail_mkstemp(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(email_settings.tempfile, "mkstemp", _fail_mkstemp)

    with pytest.raises(OSError, match="disk full"):
        write_email_settings(
            EmailSettings(host="smtp.example.com", port=587),
            explicit=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == original
    assert config_path.stat().st_mode & 0o777 == 0o644


def test_load_email_settings_accepts_legacy_starttls_disabled_file(tmp_path: Path) -> None:
    config_path = tmp_path / "email.yaml"
    config_path.write_text(
        "smtp:\n  host: smtp.example.com\n  port: 2525\n  starttls: false\n",
        encoding="utf-8",
    )

    settings = load_email_settings(explicit=config_path)

    assert settings == EmailSettings(host="smtp.example.com", port=2525, starttls=False)


def test_enabled_email_settings_require_starttls_for_write_and_runtime() -> None:
    settings = EmailSettings(host="smtp.example.com", port=587, starttls=False)

    with pytest.raises(ValueError, match="deploy report email requires STARTTLS"):
        write_email_settings(settings, explicit=Path("/tmp/email.yaml"))
    with pytest.raises(ValueError, match="deploy report email requires STARTTLS"):
        email_environment_variables(settings)
    with pytest.raises(ValueError, match="deploy report email requires STARTTLS"):
        email_runtime_settings(settings)


def test_load_email_settings_rejects_partial_credentials(tmp_path: Path) -> None:
    config_path = tmp_path / "email.yaml"
    config_path.write_text(
        "smtp:\n  host: smtp.example.com\n  username: mailer\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="smtp.username and smtp.password must be set together"):
        load_email_settings(explicit=config_path)


def test_email_helpers_emit_environment_runtime_and_secret_payloads() -> None:
    settings = EmailSettings(
        host="smtp.example.com",
        port=587,
        starttls=True,
        from_addr="deployments@example.com",
        username="mailer",
        password="secret",
    )

    assert email_environment_variables(settings) == {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_STARTTLS": "true",
        "SMTP_FROM": "deployments@example.com",
    }
    assert email_secret_values(settings) == {
        "SMTP_USERNAME": "mailer",
        "SMTP_PASSWORD": "secret",
    }
    assert email_runtime_settings(settings) == {
        "host": "smtp.example.com",
        "port": 587,
        "starttls": True,
        "from": "deployments@example.com",
        "username": "mailer",
        "password": "secret",
    }


def test_disable_email_settings_removes_file(tmp_path: Path) -> None:
    config_path = tmp_path / "email.yaml"
    write_email_settings(
        EmailSettings(host="smtp.example.com", port=587),
        explicit=config_path,
    )

    removed_path = disable_email_settings(explicit=config_path)

    assert removed_path == config_path.resolve()
    assert not config_path.exists()
