"""Publish pre-provisioned Gateway host pins recovered from authenticated Compute."""

from __future__ import annotations

import fcntl
import ipaddress
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .ssh_host_identity import recovered_identity
from .ssh_trust import resolve_ssh_known_hosts_file, ssh_trust_options

_LIMIT = 1024 * 1024


def _binding(sdk: Any, outputs: dict[str, Any]) -> tuple[Any, ...]:
    from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

    obj = sdk.call(
        InstanceServiceClient(sdk.sdk).get, GetInstanceRequest(id=outputs["instance_id"])
    )
    metadata = obj.metadata
    if (
        not re.fullmatch(r"[a-zA-Z0-9-]+", metadata.id)
        or metadata.id != outputs["instance_id"]
        or metadata.parent_id != sdk.project_id
        or not metadata.resource_version
        or obj.status.state.name != "RUNNING"
        or obj.spec.boot_disk.existing_disk.id != outputs["boot_disk_id"]
        or len(obj.status.network_interfaces) != 1
    ):
        raise RuntimeError("Gateway host-key Compute identity differs from Terraform intent")
    public = obj.status.network_interfaces[0].public_ip_address
    if (
        public.allocation_id != outputs["public_allocation_id"]
        or ipaddress.IPv4Interface(public.address).ip
        != ipaddress.IPv4Interface(outputs["public_ip"]).ip
    ):
        raise RuntimeError("Gateway host-key public address differs from Terraform intent")
    return (
        metadata.id,
        metadata.parent_id,
        metadata.resource_version,
        metadata.created_at.timestamp(),
        obj.spec.boot_disk.existing_disk.id,
        public.allocation_id,
        public.address,
        recovered_identity(obj).get("ed25519_public", ""),
    )


def _read_at(directory: int, name: str) -> tuple[bytes, tuple[int, ...] | None]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return b"", None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or info.st_size > _LIMIT
        ):
            raise RuntimeError("Gateway managed SSH trust file is unsafe")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(_LIMIT + 1)
        if len(data) > _LIMIT:
            raise RuntimeError("Gateway managed SSH trust file is oversized")
        return data, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
    finally:
        os.close(fd)


def has_host(path: Path, address: str) -> bool:
    ssh_trust_options(path)
    result = subprocess.run(
        ["ssh-keygen", "-F", address, "-f", str(path)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Gateway managed SSH trust file could not be inspected")
    return result.returncode == 0


def prepare_ssh_host_trust(
    sdk: Any, outputs: dict[str, Any], paths: Any, override: Path | None
) -> Path:
    """Keep operator pins immutable; bootstrap only the managed default file."""
    path = resolve_ssh_known_hosts_file(paths, override)
    if override is not None:
        ssh_trust_options(path)
        return path
    address = str(ipaddress.IPv4Interface(outputs["public_ip"]).ip)
    # Lock the existing generated directory itself: no extra persistent artifact.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary: str | None = None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise RuntimeError("Gateway managed SSH trust directory is unsafe")
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Gateway managed SSH trust is being updated; retry deploy") from None
        before = _read_at(directory, path.name)
        if before[1] is not None and has_host(path, address):
            return path
        binding = _binding(sdk, outputs)
        key = binding[-1]
        if not key:
            raise RuntimeError(
                "This gateway VM has a guest-generated host identity. Provision its independently "
                "verified public key in generated/ssh_known_hosts or pass --ssh-known-hosts-file, "
                "then resume deploy. Its existing host key and VM were not changed."
            )
        if _binding(sdk, outputs) != binding:
            raise RuntimeError("Gateway Compute identity changed during host-key enrollment")
        temporary = ".ssh-host-pin-" + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as target:
            target.write(before[0])
            if before[0] and not before[0].endswith(b"\n"):
                target.write(b"\n")
            target.write(f"{address} {key}\n".encode("ascii"))
            target.flush()
            os.fsync(target.fileno())
        if _read_at(directory, path.name) != before:
            raise RuntimeError("Gateway SSH trust changed during enrollment; no pin was published")
        current_dir = path.parent.stat(follow_symlinks=False)
        if (current_dir.st_dev, current_dir.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError("Gateway SSH trust directory changed during enrollment")
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        os.fsync(directory)
        return path
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError(
            "Gateway host-key enrollment failed safely; verify local trust and retry"
        ) from None
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)
