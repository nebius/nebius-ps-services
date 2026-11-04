"""Fail-closed SSH host identity policy for cxcli day-2 commands."""

from __future__ import annotations

import ipaddress
import os
import stat
import subprocess
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
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"SSH known-hosts path must be a regular file: {path}")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"SSH known-hosts file must not be hard-linked: {path}")
    if metadata.st_uid not in {0, os.getuid()} or metadata.st_mode & 0o022:
        raise RuntimeError("SSH known-hosts file has unsafe ownership or write permissions")
    if metadata.st_size > 1024 * 1024:
        raise RuntimeError("SSH known-hosts file exceeds the safe inspection limit")
    if not os.access(path, os.R_OK):
        raise RuntimeError(f"SSH known-hosts file is not readable: {path}")
    resolved = path.resolve(strict=True)
    literal = str(resolved)
    if any(ord(char) < 32 for char in literal) or "${" in literal:
        raise RuntimeError("SSH known-hosts path contains unsupported expansion/control characters")
    literal = literal.replace("%", "%%")
    if any(char.isspace() or char in '\\"' for char in literal):
        literal = '"' + literal.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return [
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={literal}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "KnownHostsCommand=none",
        "-o",
        "VerifyHostKeyDNS=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "ControlPath=none",
    ]


def ssh_host_trust_options(known_hosts_file: Path, host: str) -> list[str]:
    """Check target membership locally; OpenSSH still authenticates the peer key."""
    options = ssh_trust_options(known_hosts_file)
    try:
        address = str(ipaddress.ip_address(host))
    except ValueError:
        raise RuntimeError("SSH host trust requires a valid selected IP address") from None
    path = known_hosts_file.expanduser().resolve(strict=True)
    before = path.stat()
    try:
        found = subprocess.run(
            ["ssh-keygen", "-F", address, "-f", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if found.returncode not in (0, 1):
            raise RuntimeError("SSH host trust file could not be inspected")
        if found.returncode == 1 or not found.stdout.strip():
            raise RuntimeError(
                "SSH host trust is missing for the selected gateway. Provision its public "
                "host key through an independently authenticated channel in the selected "
                "known-hosts file, or use --ssh-known-hosts-file. An SSH login public key "
                "does not authenticate the server; do not accept an unverified scan."
            )
        parsed = subprocess.run(
            ["ssh-keygen", "-l", "-f", "-"],
            input=found.stdout,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if parsed.returncode or not parsed.stdout.strip():
            raise RuntimeError("SSH host trust has no usable key for the selected gateway")
        after = path.stat()
        if any(
            getattr(before, name) != getattr(after, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise RuntimeError("SSH host trust changed during inspection; retry")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RuntimeError("SSH host trust could not be inspected safely with ssh-keygen") from None
    return [*options, "-o", f"HostKeyAlias={address}", "-o", f"Hostname={address}", "-o", "Port=22"]


def ssh_failure_detail(error: subprocess.CalledProcessError) -> str:
    """Classify failures without reflecting remote output, which may contain keys."""
    stderr = error.stderr if isinstance(error.stderr, str) else ""
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
        return "SSH host key changed. Verify the gateway identity independently; existing trust was preserved."
    if "Host key verification failed" in stderr:
        return "SSH host key verification failed. Verify the selected host and trust file independently."
    if "Permission denied" in stderr:
        return "SSH login authentication failed. Check the selected user and private-key access."
    return f"SSH exited with status {error.returncode}; remote output was withheld to protect key material."
