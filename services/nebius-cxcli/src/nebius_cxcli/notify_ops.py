"""Notification utilities."""

from __future__ import annotations

import os
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .paths import InstancePaths


@dataclass(frozen=True)
class InventoryEmailResult:
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


def _mask_identifier(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return token
    if len(token) <= 4:
        return token
    return ("*" * (len(token) - 4)) + token[-4:]


def send_inventory_email(
    config: Any,
    paths: InstancePaths,
    *,
    smtp_settings: Mapping[str, Any] | None = None,
) -> InventoryEmailResult:
    """Send inventory email when notifications.email_enabled is enabled and email is set."""
    if not bool(config.client_info.notifications.email_enabled):
        return InventoryEmailResult(
            sent=False,
            reason="disabled",
            message="Inventory email disabled (`client_info.notifications.email_enabled=false`); nothing sent.",
        )

    recipient = str(config.client_info.notifications.email or "").strip()
    if not recipient:
        return InventoryEmailResult(
            sent=False,
            reason="recipient_missing",
            message=(
                "Inventory email enabled but `client_info.notifications.email` is empty; "
                "nothing sent."
            ),
        )

    host = _env_or_config_text("SMTP_HOST", smtp_settings, "host")
    if not host:
        return InventoryEmailResult(
            sent=False,
            reason="smtp_unconfigured",
            message=(
                "Inventory email enabled but SMTP is not configured. "
                "Run `nebius-cxcli email --setup` and `nebius-cxcli bootstrap-ci <config.yaml>` "
                "to sync the GitHub environment, or set SMTP_HOST."
            ),
        )

    port = _env_or_config_int("SMTP_PORT", smtp_settings, "port", default=587)
    username = _env_or_config_text("SMTP_USERNAME", smtp_settings, "username")
    password = _env_or_config_text("SMTP_PASSWORD", smtp_settings, "password")
    from_addr = (
        _env_or_config_text("SMTP_FROM", smtp_settings, "from")
        or username
        or "noreply@localhost"
    )

    project_id = (
        str(config.client_info.nebius.project_id or "").strip()
        or paths.path_project_id
        or paths.instance_slug
    )
    tenant_id = str(config.client_info.nebius.tenant_id or "").strip() or paths.path_tenant_id
    masked_project_id = _mask_identifier(project_id)

    markdown_path = paths.inventory_dir / "inventory.md"
    if not markdown_path.exists():
        raise RuntimeError(
            f"Inventory markdown is missing: {markdown_path}. "
            "Run `nebius-cxcli inventory write <generated-dir>` or rerender first."
        )
    body = markdown_path.read_text(encoding="utf-8")
    if tenant_id:
        body = body.replace(tenant_id, _mask_identifier(tenant_id))
    if project_id:
        body = body.replace(project_id, masked_project_id)

    message = EmailMessage()
    message["Subject"] = f"Nebius inventory: {masked_project_id}"
    message["From"] = from_addr
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        starttls = _env_or_config_bool(
            "SMTP_STARTTLS",
            smtp_settings,
            "starttls",
            default=True,
        )
        if starttls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)

    return InventoryEmailResult(
        sent=True,
        reason="sent",
        message="Inventory email sent",
    )


__all__ = ["InventoryEmailResult", "send_inventory_email"]
