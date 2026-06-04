"""Notification utilities."""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .deploy_validation_report import DEPLOY_REPORT_FILENAME
from .paths import ProjectPaths


@dataclass(frozen=True)
class DeployReportEmailResult:
    sent: bool
    reason: str
    message: str


def _env_or_config_text(
    env_name: str,
    config: Mapping[str, Any] | None,
    key: str,
) -> str:
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    if config is None:
        return ""
    return str(config.get(key, "") or "").strip()


def _env_or_config_int(
    env_name: str,
    config: Mapping[str, Any] | None,
    key: str,
    *,
    default: int,
) -> int:
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return int(env_value)
    if config is None:
        return default
    raw = config.get(key, default)
    return int(raw)


def _env_or_config_bool(
    env_name: str,
    config: Mapping[str, Any] | None,
    key: str,
    *,
    default: bool,
) -> bool:
    env_value = os.environ.get(env_name, "").strip().lower()
    if env_value:
        return env_value in {"1", "true", "yes"}
    if config is None:
        return default
    raw = config.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes"}
    return bool(raw)


_IDENTIFIER_BOUNDARY_CHARS = "A-Za-z0-9_-"


def _redacted_identifier(label: str) -> str:
    return f"[redacted {label}]"


def _redact_identifier_occurrences(body: str, value: str, *, label: str) -> str:
    token = str(value or "").strip()
    if not token:
        return body
    pattern = re.compile(
        rf"(?<![{_IDENTIFIER_BOUNDARY_CHARS}]){re.escape(token)}"
        rf"(?![{_IDENTIFIER_BOUNDARY_CHARS}])"
    )
    return pattern.sub(_redacted_identifier(label), body)


def send_deploy_report_email(
    config: Any,
    paths: ProjectPaths,
    *,
    smtp_settings: Mapping[str, Any] | None = None,
) -> DeployReportEmailResult:
    """Send the existing deploy report email when notifications are enabled."""
    if not bool(config.client_info.notifications.email_enabled):
        return DeployReportEmailResult(
            sent=False,
            reason="disabled",
            message="Deploy report email disabled (`client_info.notifications.email_enabled=false`); nothing sent.",
        )

    recipient = str(config.client_info.notifications.email or "").strip()
    if not recipient:
        return DeployReportEmailResult(
            sent=False,
            reason="recipient_missing",
            message=(
                "Deploy report email enabled but `client_info.notifications.email` is empty; "
                "nothing sent."
            ),
        )

    host = _env_or_config_text("SMTP_HOST", smtp_settings, "host")
    if not host:
        return DeployReportEmailResult(
            sent=False,
            reason="smtp_unconfigured",
            message=(
                "Deploy report email enabled but SMTP is not configured. "
                "Run `nebius-cxcli email --setup` and `nebius-cxcli bootstrap-ci <config.yaml>` "
                "to sync the GitHub environment, or set SMTP_HOST."
            ),
        )

    port = _env_or_config_int("SMTP_PORT", smtp_settings, "port", default=587)
    username = _env_or_config_text("SMTP_USERNAME", smtp_settings, "username")
    password = _env_or_config_text("SMTP_PASSWORD", smtp_settings, "password")
    from_addr = (
        _env_or_config_text("SMTP_FROM", smtp_settings, "from") or username or "noreply@localhost"
    )

    project_id = str(config.client_info.nebius.project_id or "").strip()
    tenant_id = str(config.client_info.nebius.tenant_id or "").strip()
    if not project_id:
        raise RuntimeError(
            "Deploy report email requires `client_info.nebius.project_id` from config.yaml; "
            "folder names are not used as an identity fallback."
        )
    redacted_project_id = _redacted_identifier("project_id")
    starttls = _env_or_config_bool(
        "SMTP_STARTTLS",
        smtp_settings,
        "starttls",
        default=True,
    )
    if not starttls:
        raise RuntimeError(
            "Deploy report email requires SMTP STARTTLS so report contents and SMTP credentials "
            "are not sent in plaintext. Set SMTP_STARTTLS=true or enable STARTTLS in "
            "`nebius-cxcli email --setup`."
        )

    markdown_path = paths.inventory_dir / DEPLOY_REPORT_FILENAME
    if not markdown_path.exists():
        raise RuntimeError(
            f"Deploy report markdown is missing: {markdown_path}. "
            "Run `nebius-cxcli render <config.yaml>` or deploy/apply again first."
        )
    body = markdown_path.read_text(encoding="utf-8")
    if tenant_id:
        body = _redact_identifier_occurrences(body, tenant_id, label="tenant_id")
    if project_id:
        body = _redact_identifier_occurrences(body, project_id, label="project_id")

    message = EmailMessage()
    message["Subject"] = f"Nebius deploy report: {redacted_project_id}"
    message["From"] = from_addr
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)

    return DeployReportEmailResult(
        sent=True,
        reason="sent",
        message="Deploy report email sent",
    )


__all__ = ["DeployReportEmailResult", "send_deploy_report_email"]
