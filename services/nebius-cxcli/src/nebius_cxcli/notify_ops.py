"""Notification utilities."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any

from .paths import InstancePaths


def send_inventory_email(config: Any, paths: InstancePaths) -> bool:
    """Send inventory notification email when client_info.notifications.email is set."""
    recipient = config.client_info.notifications.email
    if not recipient:
        return False

    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST is required for email command")

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("SMTP_FROM", username or "noreply@localhost")

    subject_target = (
        str(config.client_info.nebius.project_id or "").strip()
        or paths.path_project_id
        or paths.instance_slug
    )

    markdown_path = paths.inventory_dir / "inventory.md"
    body = (
        markdown_path.read_text(encoding="utf-8")
        if markdown_path.exists()
        else f"Inventory generated for {subject_target}."
    )

    message = EmailMessage()
    message["Subject"] = f"Nebius inventory: {subject_target}"
    message["From"] = from_addr
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        starttls = os.environ.get("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}
        if starttls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)

    return True
