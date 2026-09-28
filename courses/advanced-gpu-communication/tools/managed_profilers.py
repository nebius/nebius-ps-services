#!/usr/bin/env python3
"""Resolve complete cxcli-managed profiler package roots for container mounts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

PROFILE = Path("/etc/profile.d/99-nsight.sh")


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, timeout=15).strip()


def profiler_roots(profile: Path = PROFILE, prefix: Path = Path("/opt")) -> list[Path]:
    if not profile.is_file() or profile.is_symlink():
        raise ValueError(
            "Missing managed Nsight activation; complete shared README setup"
        )
    binaries = output(
        [
            "bash",
            "-c",
            'set -e; PATH=/usr/bin:/bin; source "$1"; command -v nsys; command -v ncu',
            "bash",
            str(profile),
        ]
    ).splitlines()
    if len(binaries) != 2:
        raise ValueError("Managed activation must resolve both profilers")
    roots = []
    for tool, value in zip(("nsys", "ncu"), binaries, strict=True):
        binary = Path(value).resolve(strict=True)
        if not binary.is_relative_to(prefix) or not os.access(binary, os.X_OK):
            raise ValueError(f"Managed {tool} must be an executable below {prefix}")
        owners = output(["dpkg-query", "-S", str(binary)]).splitlines()
        if len(owners) != 1 or ": " not in owners[0]:
            raise ValueError(f"Require one package owner for {tool}")
        package, owned = owners[0].split(": ", 1)
        if owned != str(binary):
            raise ValueError(f"Package owner does not identify {tool}")
        files = [
            Path(line)
            for line in output(["dpkg-query", "-L", package]).splitlines()
            if Path(line).is_relative_to(prefix) and Path(line).is_file()
        ]
        if binary not in files:
            raise ValueError(f"Package inventory does not contain {tool}")
        directory = Path(os.path.commonpath([str(path.parent) for path in files]))
        if directory == prefix or not directory.is_relative_to(prefix):
            raise ValueError(f"Cannot isolate the {tool} installation directory")
        if directory.resolve(strict=True) != directory:
            raise ValueError(
                "Managed package directories must not redirect through symlinks"
            )
        if directory not in roots:
            roots.append(directory)
    return roots


def main() -> None:
    try:
        paths = [*profiler_roots(), PROFILE]
        for path in paths:
            if any(char in str(path) for char in (":", ",", "\n")):
                raise ValueError(
                    "Managed paths cannot contain Apptainer bind separators"
                )
        for path in paths:
            sys.stdout.buffer.write(os.fsencode(f"{path}:{path}:ro") + b"\0")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ERROR: cannot mount managed profilers: {error}") from error


if __name__ == "__main__":
    main()
