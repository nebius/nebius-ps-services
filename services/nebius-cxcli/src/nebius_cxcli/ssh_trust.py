"""Fail-closed SSH host identity policy for cxcli day-2 commands."""

from __future__ import annotations

import os
from pathlib import Path

from .paths import ProjectPaths

DEFAULT_SSH_KNOWN_HOSTS_FILENAME = "ssh_known_hosts"


def resolve_ssh_known_hosts_file(
    paths: ProjectPaths,
    override: Path | None,
) -> Path:
    """Resolve the explicit trust file or the project-local generated default."""
    selected = (
        override.expanduser()
        if override is not None
        else (paths.generated_dir / DEFAULT_SSH_KNOWN_HOSTS_FILENAME)
    )
    return Path(os.path.abspath(selected))


def ssh_trust_options(known_hosts_file: Path) -> list[str]:
    """Return strict OpenSSH options after validating the selected trust file."""
    path = known_hosts_file.expanduser()
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"SSH known-hosts file does not exist: {path}. Provision it from an "
            "independently verified host key before running this command."
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"SSH known-hosts path must be a regular file: {path}")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"SSH known-hosts file must not be hard-linked: {path}")
    if not os.access(path, os.R_OK):
        raise RuntimeError(f"SSH known-hosts file is not readable: {path}")
    resolved = path.resolve(strict=True)
    return [
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={resolved}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
    ]
