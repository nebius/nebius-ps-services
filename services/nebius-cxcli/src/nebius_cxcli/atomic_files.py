"""Atomic text replacement shared by CLI state and lightweight credential caches."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _write_text_atomic(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    file_mode: int | None = None,
    expected_absent: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode(encoding)
    fd = -1
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            if file_mode is not None:
                os.fchmod(handle.fileno(), file_mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_absent:
            os.link(temp_path, path)
            temp_path.unlink()
        else:
            os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
