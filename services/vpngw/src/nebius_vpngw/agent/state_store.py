from __future__ import annotations

import json
import hashlib
from pathlib import Path
import datetime as dt
import typing as t


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
        s = json.dumps(resolved_config, sort_keys=True).encode()
        return hashlib.sha256(s).hexdigest()

    def is_changed(self, resolved_config: dict) -> bool:
        last = self.load_last_applied()
        new_hash = self._hash_cfg(resolved_config)
        return last is None or last.get("config_hash") != new_hash

    def save_last_applied(self, resolved_config: dict) -> None:
        payload = {
            "config_hash": self._hash_cfg(resolved_config),
            "timestamp": dt.datetime.utcnow().isoformat() + "Z",
            "resolved_config": resolved_config,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
