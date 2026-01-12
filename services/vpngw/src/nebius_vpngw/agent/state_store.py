from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

RENDER_VERSION = 3


def _get_package_version() -> str:
    """Get the installed package version for diagnostics."""
    try:
        from importlib.metadata import version

        return version("nebius-vpngw")
    except Exception:
        return "unknown"


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_last_applied(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _hash_cfg(self, resolved_config: dict) -> str:
        # Include render version to force reapply only when rendering logic changes
        s = json.dumps(
            {"config": resolved_config, "render_version": RENDER_VERSION},
            sort_keys=True,
        ).encode()
        return hashlib.sha256(s).hexdigest()

    def is_changed(self, resolved_config: dict) -> bool:
        last = self.load_last_applied()
        new_hash = self._hash_cfg(resolved_config)
        return last is None or last.get("config_hash") != new_hash

    def save_last_applied(self, resolved_config: dict) -> None:
        payload = {
            "config_hash": self._hash_cfg(resolved_config),
            "package_version": _get_package_version(),
            "render_version": RENDER_VERSION,
            "timestamp": dt.datetime.utcnow().isoformat() + "Z",
            "resolved_config": resolved_config,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
