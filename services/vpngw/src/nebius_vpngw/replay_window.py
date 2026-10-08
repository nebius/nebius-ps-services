"""Selected-artifact admission for the optional receive replay window."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any
from zipfile import ZipFile

REPLAY_WINDOW_CAPABILITY = "ipsec-replay-window-v1"
REPLAY_WINDOW_SOURCES = (
    "schema.py",
    "config_loader.py",
    "tunnel_state.py",
    "agent/strongswan_renderer.py",
    "agent/main.py",
    "replay_window.py",
)


class ReplayWindowCapabilityError(RuntimeError):
    """No selected agent has proven support for an explicit receive window."""


def wheel_supports_replay_window(archive: ZipFile) -> bool:
    """Bind parsing, projection, rendering and reachable advertisement to this CLI."""
    for source in REPLAY_WINDOW_SOURCES:
        local = (Path(__file__).parent / source).read_bytes()
        try:
            member = archive.getinfo("nebius_vpngw/" + source)
        except KeyError:
            return False
        if member.file_size != len(local) or archive.read(member) != local:
            return False
    return True


def has_replay_window(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    return any(
        "replay_window" in tunnel
        for connection in config.get("connections", [])
        for tunnel in connection.get("tunnels", [])
    )


def require_replay_window_capability(config: Any, capabilities: Collection[str]) -> None:
    if has_replay_window(config) and REPLAY_WINDOW_CAPABILITY not in capabilities:
        raise ReplayWindowCapabilityError(
            "Selected agent wheel does not support this CLI's replay_window setting; "
            "build or select the matching wheel before applying"
        )
