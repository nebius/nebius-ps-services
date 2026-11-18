"""One stdlib-only package predecessor contract for approval and guest admission."""

from __future__ import annotations

from typing import Any

PACKAGE_FIELDS = (
    "boot_id",
    "config_sha256",
    "ha_config_digest",
    "files",
    "assets",
    "asset_modes",
    "dependencies",
    "requirements",
    "markers",
    "wheel_tags",
    "python",
    "machine",
    "package_version",
    "runtime_plan",
    "asset_retirement",
)


def package_predecessor(observed: dict[str, Any]) -> dict[str, Any]:
    return {key: observed[key] for key in PACKAGE_FIELDS}
