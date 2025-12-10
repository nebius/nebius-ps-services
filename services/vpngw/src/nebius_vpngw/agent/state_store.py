from __future__ import annotations

import json
import hashlib
from pathlib import Path
import datetime as dt
import typing as t


def _get_package_version() -> str:
    """Get the installed package version to detect code changes."""
    try:
        from importlib.metadata import version
        return version("nebius-vpngw")
    except Exception:
        return "unknown"


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_last_applied(self) -> t.Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _hash_cfg(self, resolved_config: dict) -> str:
        # Include package version in hash so code changes trigger reapply
        # This ensures that agent code updates force config regeneration
        pkg_version = _get_package_version()
        s = json.dumps({"config": resolved_config, "version": pkg_version}, sort_keys=True).encode()
        return hashlib.sha256(s).hexdigest()

    def is_changed(self, resolved_config: dict) -> bool:
        last = self.load_last_applied()
        new_hash = self._hash_cfg(resolved_config)
        return last is None or last.get("config_hash") != new_hash

    def save_last_applied(self, resolved_config: dict) -> None:
        payload = {
            "config_hash": self._hash_cfg(resolved_config),
            "package_version": _get_package_version(),
            "timestamp": dt.datetime.utcnow().isoformat() + "Z",
            "resolved_config": resolved_config,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
