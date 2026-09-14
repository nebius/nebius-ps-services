"""Fresh-install root SSH choices; rendering never reads the local SSH directory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .soperator_values import explicit_values, mark_explicit_value, soperator_rows
from .ssh_public_keys import discover_ssh_public_key_files

ROOT_KEY_PATH = ("slurmNodes", "login", "sshRootPublicKeys")


def explicit_root_keys(row: Mapping[str, Any]) -> list[str] | None:
    login = explicit_values(row).get("slurmNodes", {}).get("login", {})
    if "sshRootPublicKeys" not in login:
        return None
    value = login["sshRootPublicKeys"]
    if not isinstance(value, list) or any(
        not isinstance(key, str) or not key.strip() for key in value
    ):
        raise ValueError("Soperator root SSH keys must be a list of nonempty public keys")
    return value


def select_headless_root_keys(payload: dict[str, Any]) -> None:
    """Resolve once during fresh headless install, preserving even an explicit []."""
    for row in soperator_rows(payload):
        if explicit_root_keys(row) is not None:
            continue
        candidates = discover_ssh_public_key_files()
        if not candidates:
            raise ValueError(
                "No supported local SSH public key found under ~/.ssh. "
                "Use interactive install to select a key, or set "
                "slurmNodes.login.sshRootPublicKeys in --values-file "
                "(use [] to deliberately disable root SSH keys)."
            )
        login = row.setdefault("values", {}).setdefault("slurmNodes", {}).setdefault("login", {})
        login["sshRootPublicKeys"] = [candidates[0].public_key]
        mark_explicit_value(row, ROOT_KEY_PATH)


def prompt_root_keys(
    current: object,
    *,
    choose_action: Callable[[int], tuple[object, bool]],
    choose_key: Callable[[], tuple[object, bool]],
    backtrack: object,
) -> tuple[object, bool]:
    """Keep the complete explicit list unless replacement is deliberately chosen."""
    if isinstance(current, list):
        action, stopped = choose_action(len(current))
        if stopped or action is backtrack:
            return (current if stopped else action), stopped
        if action == "keep":
            return current, False
        if action == "disable":
            return [], False
    selected, stopped = choose_key()
    if stopped or selected is backtrack:
        return (current if stopped else selected), stopped
    return [selected], False
