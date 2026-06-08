"""Local email configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

EMAIL_CONFIG_DIR = (Path.home() / ".config" / "nebius-cxcli").resolve()
EMAIL_CONFIG_FILE = (EMAIL_CONFIG_DIR / "email.yaml").resolve()


@dataclass(frozen=True)
class EmailSettings:
    host: str = ""
    port: int = 587
    starttls: bool = True
    from_addr: str = ""
    username: str = ""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.host.strip())


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_port(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("smtp.port must be a positive integer")
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("smtp.port must be a positive integer") from exc
    if port <= 0:
        raise ValueError("smtp.port must be a positive integer")
    return port


def _parse_bool(raw: Any, *, field_name: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in {"1", "true", "yes"}:
            return True
        if token in {"0", "false", "no"}:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _settings_from_payload(payload: Any) -> EmailSettings:
    if payload is None:
        return EmailSettings()
    if not isinstance(payload, dict):
        raise ValueError("email config root must be a mapping")
    smtp_raw = payload.get("smtp", {})
    if smtp_raw is None:
        smtp_raw = {}
    if not isinstance(smtp_raw, dict):
        raise ValueError("smtp must be a mapping")
    supported_keys = {"host", "port", "starttls", "from", "username", "password"}
    unknown = sorted(str(key) for key in smtp_raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError("smtp has unsupported field(s): " + ", ".join(unknown))

    host = _as_text(smtp_raw.get("host"))
    port = _parse_port(smtp_raw.get("port", 587))
    starttls = _parse_bool(smtp_raw.get("starttls", True), field_name="smtp.starttls")
    from_addr = _as_text(smtp_raw.get("from"))
    username = _as_text(smtp_raw.get("username"))
    password = _as_text(smtp_raw.get("password"))
    if bool(username) != bool(password):
        raise ValueError("smtp.username and smtp.password must be set together or both left blank")
    return EmailSettings(
        host=host,
        port=port,
        starttls=starttls,
        from_addr=from_addr,
        username=username,
        password=password,
    )


def _require_starttls_for_enabled_settings(settings: EmailSettings) -> None:
    if settings.enabled and not settings.starttls:
        raise ValueError(
            "smtp.starttls must be true when smtp.host is set; deploy report email requires STARTTLS"
        )


def resolve_email_config_file(*, explicit: Path | None = None) -> Path:
    return (explicit or EMAIL_CONFIG_FILE).expanduser().resolve()


def load_email_settings(*, explicit: Path | None = None) -> EmailSettings:
    path = resolve_email_config_file(explicit=explicit)
    if not path.exists():
        return EmailSettings()
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _settings_from_payload(payload)


def write_email_settings(settings: EmailSettings, *, explicit: Path | None = None) -> Path:
    _require_starttls_for_enabled_settings(settings)
    path = resolve_email_config_file(explicit=explicit)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "smtp": {
            "host": settings.host,
            "port": settings.port,
            "starttls": settings.starttls,
            "from": settings.from_addr,
            "username": settings.username,
            "password": settings.password,
        }
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    path.chmod(0o600)
    return path


def disable_email_settings(*, explicit: Path | None = None) -> Path:
    path = resolve_email_config_file(explicit=explicit)
    path.unlink(missing_ok=True)
    return path


def email_environment_variables(settings: EmailSettings) -> dict[str, str]:
    if not settings.enabled:
        return {}
    _require_starttls_for_enabled_settings(settings)
    payload = {
        "SMTP_HOST": settings.host,
        "SMTP_PORT": str(settings.port),
        "SMTP_STARTTLS": "true",
    }
    if settings.from_addr:
        payload["SMTP_FROM"] = settings.from_addr
    return payload


def email_secret_values(settings: EmailSettings) -> dict[str, str]:
    payload: dict[str, str] = {}
    if settings.username:
        payload["SMTP_USERNAME"] = settings.username
    if settings.password:
        payload["SMTP_PASSWORD"] = settings.password
    return payload


def email_runtime_settings(settings: EmailSettings) -> dict[str, Any]:
    if not settings.enabled:
        return {}
    _require_starttls_for_enabled_settings(settings)
    payload: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "starttls": True,
    }
    if settings.from_addr:
        payload["from"] = settings.from_addr
    if settings.username:
        payload["username"] = settings.username
    if settings.password:
        payload["password"] = settings.password
    return payload


__all__ = [
    "EMAIL_CONFIG_DIR",
    "EMAIL_CONFIG_FILE",
    "EmailSettings",
    "disable_email_settings",
    "email_environment_variables",
    "email_runtime_settings",
    "email_secret_values",
    "load_email_settings",
    "resolve_email_config_file",
    "write_email_settings",
]
