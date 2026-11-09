"""Canonical bytes and secure equality checks for the existing HA apply lock."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


def encode(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate apply lock field")
        result[key] = value
    return result


def verify(path: str, expected_sha256: str) -> None:
    """Compare complete JSON content; never reinterpret authority or follow links."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("unsafe apply lock")
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            raise ValueError("oversized apply lock")
        value = json.loads(raw, object_pairs_hook=_unique)
        if (
            not isinstance(value, dict)
            or hashlib.sha256(encode(value)).hexdigest() != expected_sha256
        ):
            raise ValueError("apply lock authority differs")
    finally:
        os.close(descriptor)


def verification_script() -> str:
    """Use the same bounded verifier on predecessors before package installation."""
    return Path(__file__).read_text() + "\nimport sys\nverify(sys.argv[1], sys.argv[2])\n"
