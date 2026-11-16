"""Invocation-local immutable package reuse; never caches host or cloud authority."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path


class PreparationCache:
    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory | None = None
        self._bundles: dict[str, tuple[tuple[Path, str], ...]] = {}

    def files(self, key: str, destination: Path, prepare: Callable[[], list[Path]]) -> list[Path]:
        """Retain only successful complete preparations, with digest checks on reuse."""
        if key not in self._bundles:
            paths = prepare()
            if self._temporary is None:
                self._temporary = tempfile.TemporaryDirectory(prefix="vpngw-preparation-")
            root = Path(self._temporary.name) / str(len(self._bundles))
            root.mkdir(mode=0o700)
            records = []
            for source in paths:
                target = root / source.name
                raw = source.read_bytes()
                target.write_bytes(raw)
                target.chmod(0o400)
                records.append((target, hashlib.sha256(raw).hexdigest()))
            self._bundles[key] = tuple(records)
            return paths
        paths = []
        for source, expected in self._bundles[key]:
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Prepared package integrity changed within this invocation")
            target = destination / source.name
            shutil.copyfile(source, target)
            target.chmod(0o600)
            paths.append(target)
        return paths

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
        self._bundles.clear()
