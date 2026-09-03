#!/usr/bin/env python3
"""Run a command while holding a process-safe lock for one local resource."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def _lock_path(resource: Path) -> Path:
    identity = str(resource.resolve()).encode()
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"nebius-cxcli-env-{os.getuid()}-{digest}.lock"


def run_locked(resource: Path, command: list[str]) -> int:
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ValueError("a command is required")

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(_lock_path(resource), flags, 0o600)
    with os.fdopen(descriptor, "a+b") as lock_stream:
        lock_metadata = os.fstat(lock_stream.fileno())
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != os.getuid():
            raise OSError("unsafe environment lock file")
        os.fchmod(lock_stream.fileno(), 0o600)
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        return subprocess.run(command, check=False, close_fds=False).returncode


def main() -> int:
    args = _parse_args()
    try:
        return run_locked(args.resource, args.command)
    except (OSError, ValueError):
        print("Development environment lock failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
