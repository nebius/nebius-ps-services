from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

KNOWN_HOSTS_ENV = "VPNGW_SSH_KNOWN_HOSTS_FILE"


def explicit_known_hosts_file(environment: Mapping[str, str] | None = None) -> Path | None:
    """Return one operator-supplied pinned host-key file, rejecting unsafe inputs."""

    source = os.environ if environment is None else environment
    raw = source.get(KNOWN_HOSTS_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{KNOWN_HOSTS_ENV} must be an absolute path")
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
            raise ValueError
        with path.open("rb") as stream:
            stream.read(1)
    except (OSError, ValueError):
        raise ValueError(f"{KNOWN_HOSTS_ENV} must name a non-empty readable regular file") from None
    return path


def configure_paramiko_host_verification(
    client: Any,
    paramiko: Any,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Load pinned host keys and reject every host absent from that trust store."""

    known_hosts = explicit_known_hosts_file(environment)
    if known_hosts is None:
        client.load_system_host_keys()
    else:
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())


def build_openssh_base_command(
    *,
    key_path: Path | None = None,
    connect_timeout: int = 10,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Build a strict OpenSSH command using system or explicitly pinned host keys."""

    if connect_timeout <= 0:
        raise ValueError("SSH connect timeout must be positive")
    command = ["ssh"]
    if key_path is not None:
        command.extend(["-i", str(key_path)])
    command.extend(["-o", "StrictHostKeyChecking=yes"])
    known_hosts = explicit_known_hosts_file(environment)
    if known_hosts is not None:
        command.extend(
            [
                "-o",
                f"UserKnownHostsFile={known_hosts}",
                "-o",
                "GlobalKnownHostsFile=none",
                "-o",
                "KnownHostsCommand=none",
            ]
        )
    command.extend(
        [
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            "LogLevel=ERROR",
        ]
    )
    return command
