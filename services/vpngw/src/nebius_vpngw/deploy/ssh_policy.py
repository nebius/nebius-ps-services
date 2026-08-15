from __future__ import annotations

import base64
import hashlib
import importlib
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_HOSTS_ENV = "VPNGW_SSH_KNOWN_HOSTS_FILE"
HOST_KEYS_DIR_ENV = "VPNGW_SSH_HOST_KEYS_DIR"


def _regular_file(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
            raise ValueError
        return path.read_bytes()
    except (OSError, ValueError):
        raise ValueError(f"{label} must name a non-empty readable regular file") from None


@dataclass(frozen=True)
class SSHHostIdentity:
    hostname: str
    private_key: bytes

    def cloud_init_entries(self) -> str:
        encoded = base64.b64encode(self.private_key).decode("ascii")
        return (
            "  - path: /etc/ssh/ssh_host_vpngw_key\n"
            '    permissions: "0600"\n'
            "    owner: root:root\n"
            "    encoding: b64\n"
            f"    content: {encoded}\n"
        )


@dataclass(frozen=True)
class SSHTrustPolicy:
    """One immutable exact-pin policy and its prevalidated fresh-host identities."""

    known_hosts_file: Path
    known_hosts_sha256: str
    pin_targets: tuple[tuple[str, str], ...] = ()
    identities: tuple[SSHHostIdentity, ...] = ()
    _snapshot_owner: Any = field(default=None, repr=False, compare=False)

    def assert_current(self) -> None:
        if (
            hashlib.sha256(_regular_file(self.known_hosts_file, KNOWN_HOSTS_ENV)).hexdigest()
            != self.known_hosts_sha256
        ):
            raise RuntimeError("VM-HA immutable SSH trust source changed after preflight")

    def identity_for(self, hostname: str) -> SSHHostIdentity:
        matching = tuple(item for item in self.identities if item.hostname == hostname)
        if len(matching) != 1:
            raise ValueError(f"VM-HA SSH host identity is unavailable for {hostname}")
        return matching[0]

    def pin_target_for(self, hostname: str) -> str:
        matching = tuple(target for name, target in self.pin_targets if name == hostname)
        if len(matching) != 1:
            raise ValueError(f"VM-HA SSH host pin is unavailable for {hostname}")
        return matching[0]


def explicit_known_hosts_file(environment: Mapping[str, str] | None = None) -> Path | None:
    source = os.environ if environment is None else environment
    raw = source.get(KNOWN_HOSTS_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{KNOWN_HOSTS_ENV} must be an absolute path")
    _regular_file(path, KNOWN_HOSTS_ENV)
    return path


def require_explicit_known_hosts_file(environment: Mapping[str, str] | None = None) -> Path:
    path = explicit_known_hosts_file(environment)
    if path is None:
        raise ValueError(
            f"{KNOWN_HOSTS_ENV} is required for VM-HA apply and must name a pinned known-hosts file"
        )
    try:
        paramiko = importlib.import_module("paramiko")
        keys = paramiko.HostKeys(filename=str(path))
    except Exception as error:
        raise ValueError(f"{KNOWN_HOSTS_ENV} does not contain usable SSH host keys") from error
    if not keys:
        raise ValueError(f"{KNOWN_HOSTS_ENV} does not contain usable SSH host keys")
    return path


def _validated_private_host_key(path: Path, hostname: str) -> tuple[bytes, str, str]:
    content = _regular_file(path, f"VM-HA SSH private host key for {hostname}")
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        key_type, key_data, *_ = result.stdout.strip().split()
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            f"VM-HA SSH private host key for {hostname} is malformed, encrypted, or unusable"
        ) from error
    return content, key_type, key_data


def require_vm_ha_ssh_policy(
    hosts: Iterable[str | tuple[str, str]],
    environment: Mapping[str, str] | None = None,
    *,
    enrollment_hosts: Iterable[str] | None = None,
) -> SSHTrustPolicy:
    """Validate exact pins and unattended private host keys before cloud mutation."""

    source = os.environ if environment is None else environment
    known_hosts = require_explicit_known_hosts_file(source)
    known_hosts_content = _regular_file(known_hosts, KNOWN_HOSTS_ENV)
    snapshot_owner = tempfile.TemporaryDirectory(prefix="nebius-vpngw-known-hosts-")
    snapshot = Path(snapshot_owner.name) / "known_hosts"
    snapshot.write_bytes(known_hosts_content)
    snapshot.chmod(0o400)
    paramiko = importlib.import_module("paramiko")
    pins = paramiko.HostKeys(filename=str(snapshot))
    host_pairs = tuple((host, host) if isinstance(host, str) else host for host in hosts)
    enrollment = (
        {hostname for hostname, _ in host_pairs}
        if enrollment_hosts is None
        else set(enrollment_hosts)
    )
    unknown_enrollment = enrollment - {hostname for hostname, _ in host_pairs}
    if unknown_enrollment:
        raise ValueError("VM-HA enrollment host is not present in the deployment plan")
    directory: Path | None = None
    if enrollment:
        raw_directory = source.get(HOST_KEYS_DIR_ENV)
        if not raw_directory:
            raise ValueError(f"{HOST_KEYS_DIR_ENV} is required for VM-HA fresh-host enrollment")
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{HOST_KEYS_DIR_ENV} must name an absolute non-symlink directory")
    identities: list[SSHHostIdentity] = []
    for hostname, pin_target in host_pairs:
        entries = pins.lookup(pin_target) or {}
        if not entries:
            raise ValueError(f"VM-HA SSH host pin is unavailable for {pin_target}")
        if hostname not in enrollment:
            continue
        assert directory is not None
        content, key_type, key_data = _validated_private_host_key(
            directory / f"{hostname}.key", hostname
        )
        pinned = entries.get(key_type)
        if pinned is None or pinned.get_base64() != key_data:
            raise ValueError(
                f"VM-HA SSH private host key for {hostname} does not match its exact pin for {pin_target}"
            )
        identities.append(SSHHostIdentity(hostname=hostname, private_key=content))
    return SSHTrustPolicy(
        known_hosts_file=snapshot,
        known_hosts_sha256=hashlib.sha256(known_hosts_content).hexdigest(),
        pin_targets=host_pairs,
        identities=tuple(identities),
        _snapshot_owner=snapshot_owner,
    )


def configure_paramiko_host_verification(
    client: Any,
    paramiko: Any,
    *,
    policy: SSHTrustPolicy | None = None,
    hostname: str | None = None,
    transport_host: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    if policy is not None:
        policy.assert_current()
    known_hosts = (
        policy.known_hosts_file if policy is not None else explicit_known_hosts_file(environment)
    )
    if known_hosts is None:
        client.load_system_host_keys()
    else:
        client.load_host_keys(str(known_hosts))
        if policy is not None and hostname is not None and transport_host is not None:
            pin_target = policy.pin_target_for(hostname)
            pinned = client.get_host_keys().lookup(pin_target) or {}
            if not pinned:
                raise RuntimeError(f"VM-HA SSH host pin is unavailable for {pin_target}")
            if transport_host != pin_target:
                for key_type, key in tuple(pinned.items()):
                    client.get_host_keys().add(transport_host, key_type, key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())


def build_openssh_base_command(
    *,
    key_path: Path | None = None,
    connect_timeout: int = 10,
    policy: SSHTrustPolicy | None = None,
    hostname: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    if connect_timeout <= 0:
        raise ValueError("SSH connect timeout must be positive")
    command = ["ssh"]
    if key_path is not None:
        command.extend(["-i", str(key_path)])
    command.extend(["-o", "StrictHostKeyChecking=yes"])
    if policy is not None:
        policy.assert_current()
    known_hosts = (
        policy.known_hosts_file if policy is not None else explicit_known_hosts_file(environment)
    )
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
    if policy is not None and hostname is not None:
        command.extend(["-o", f"HostKeyAlias={policy.pin_target_for(hostname)}"])
    command.extend(["-o", f"ConnectTimeout={connect_timeout}", "-o", "LogLevel=ERROR"])
    return command
