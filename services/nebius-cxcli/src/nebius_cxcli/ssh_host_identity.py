"""Protected first-boot Gateway host identities and authenticated Compute recovery."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

MARKER = "cxcli-managed-host-identity-v1"


class ComputeHostIdentityClient:
    """Bounded, read-only Compute access for gateway identity operations."""

    def __init__(self, project_id: str) -> None:
        from .sdk_auth import init_nebius_sdk

        self.project_id = project_id
        self.sdk = init_nebius_sdk(
            parent_id=project_id, context="Gateway SSH identity", prefer_operator_auth=True
        )

    def __enter__(self) -> ComputeHostIdentityClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.sdk.sync_close()

    def call(self, method: Any, request: Any) -> Any:
        from .nebius_api_helpers import bounded_nebius_request_kwargs

        try:
            return method(request, **bounded_nebius_request_kwargs()).wait()
        except Exception:
            raise RuntimeError("Gateway authenticated Compute identity is unavailable") from None


def _private_bytes(path: Path | str, *, directory: int | None = None) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 16384
        ):
            raise RuntimeError("Gateway host private-key file is unsafe")
        with os.fdopen(fd, "rb", closefd=False) as source:
            value = source.read(16385)
        if len(value) > 16384:
            raise RuntimeError("Gateway host private-key file is oversized")
        return value
    finally:
        os.close(fd)


def _pair(private: bytes) -> dict[str, str]:
    if len(private) > 16384:
        raise RuntimeError("Gateway host private key is oversized")
    try:
        with tempfile.TemporaryDirectory(prefix="cxcli-ssh-host-check-") as temporary:
            path = Path(temporary) / "host.key"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(private)
            result = subprocess.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        fields = result.stdout.split()
        if len(fields) < 2 or fields[0] != "ssh-ed25519":
            raise ValueError("unsupported key")
        return {"ed25519_private": private.decode("ascii"), "ed25519_public": " ".join(fields[:2])}
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError("Gateway host identity is not a valid unencrypted Ed25519 key") from None


def generate_host_identity() -> dict[str, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="cxcli-ssh-host-new-") as temporary:
            path = Path(temporary) / "host.key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
                capture_output=True,
                timeout=30,
                check=True,
            )
            return _pair(_private_bytes(path))
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Gateway host identity generation failed") from None


def compute_instance(sdk: Any, identity: str) -> Any:
    from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

    obj = sdk.call(InstanceServiceClient(sdk.sdk).get, GetInstanceRequest(id=identity))
    if (
        obj.metadata.id != identity
        or obj.metadata.parent_id != sdk.project_id
        or not obj.metadata.resource_version
    ):
        raise RuntimeError("Gateway host-key Compute identity differs from the selected project")
    return obj


def recovered_identity(instance: Any) -> dict[str, str]:
    """Only the product-injected pair is recoverable; never enroll network keys."""
    raw = instance.spec.cloud_init_user_data
    if not isinstance(raw, str) or len(raw) > 1024 * 1024:
        raise RuntimeError("Gateway Compute first-boot metadata is unavailable or oversized")
    if MARKER not in raw:
        return {}
    try:
        data = yaml.safe_load(raw)
        keys = data["ssh_keys"]
        if set(keys) != {"ed25519_private", "ed25519_public"}:
            raise ValueError("invalid fields")
        pair = _pair(keys["ed25519_private"].encode("ascii"))
        if pair["ed25519_public"] != keys["ed25519_public"]:
            raise ValueError("mismatched pair")
        return pair
    except (KeyError, TypeError, ValueError, AttributeError, yaml.YAMLError):
        raise RuntimeError("Gateway Compute host-key metadata is invalid or conflicting") from None


def _directory(path: Path | str, *, parent: int | None = None) -> int:
    with suppress(FileExistsError):
        os.mkdir(path, mode=0o700, dir_fd=parent)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise RuntimeError("Gateway host identity storage must be owner-only")
    return fd


def fresh_identity(
    paths: Any, project: str, component: str, *, preview: bool, namespace: str
) -> dict[str, str]:
    """Persist only real-deploy keys, outside render bundles and routing journals."""
    root = paths.project_dir / ".nebius-cxcli"
    if namespace not in {"nat-host-keys", "tailscale-host-keys"}:
        raise ValueError("Unknown gateway host identity namespace")
    folder = root / namespace
    name = hashlib.sha256((project + "\0" + component).encode()).hexdigest() + ".key"
    if preview and not folder.exists():
        return generate_host_identity()
    descriptors = []
    temporary: str | None = None
    try:
        descriptors.append(_directory(root))
        descriptors.append(_directory(folder.name, parent=descriptors[0]))

        def assert_storage() -> None:
            for path, descriptor in zip((root, folder), descriptors, strict=True):
                current, opened = path.stat(follow_symlinks=False), os.fstat(descriptor)
                if (
                    (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                    or not stat.S_ISDIR(current.st_mode)
                    or current.st_uid != os.getuid()
                    or current.st_mode & 0o077
                ):
                    raise RuntimeError("Gateway host identity directory changed during preparation")

        try:
            fcntl.flock(descriptors[-1], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Gateway host identity is being prepared; retry deploy") from None
        try:
            pair = _pair(_private_bytes(name, directory=descriptors[-1]))
            assert_storage()
            return pair
        except FileNotFoundError:
            pass
        pair = generate_host_identity()
        assert_storage()
        if preview:
            return pair
        temporary = ".new-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=descriptors[-1],
        )
        with os.fdopen(fd, "w") as output:
            output.write(pair["ed25519_private"])
            output.flush()
            os.fsync(output.fileno())
        assert_storage()
        os.link(
            temporary,
            name,
            src_dir_fd=descriptors[-1],
            dst_dir_fd=descriptors[-1],
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=descriptors[-1])
        temporary = None
        os.fsync(descriptors[-1])
        assert_storage()
        return pair
    except OSError:
        raise RuntimeError(
            "Gateway host identity storage is unsafe or changed; no key was replaced"
        ) from None
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=descriptors[-1])
        for fd in reversed(descriptors):
            os.close(fd)


def prepare_host_identity(
    sdk: Any,
    *,
    instance_id: str,
    paths: Any,
    infra_dir: Path,
    module_name: str,
    preview: bool,
    namespace: str,
) -> dict[str, str]:
    variable = f"{module_name}_ssh_host_keys"
    declared = f'variable "{variable}"' in (infra_dir / "variables.tf").read_text()
    if instance_id:
        instance = compute_instance(sdk, instance_id)
        pair = recovered_identity(instance)
        current = compute_instance(sdk, instance_id)
        if current.metadata.resource_version != instance.metadata.resource_version:
            raise RuntimeError("Gateway Compute identity changed while recovering host identity")
    else:
        if not declared:
            raise RuntimeError(
                "Gateway host identity requires an updated rendered bundle; run render before deploy"
            )
        pair = fresh_identity(
            paths, sdk.project_id, module_name, preview=preview, namespace=namespace
        )
    if pair and not declared:
        raise RuntimeError(
            "Gateway host identity requires an updated rendered bundle; run render before deploy"
        )
    # Old frozen bundles and retained guest-generated identities remain untouched.
    return {f"TF_VAR_{variable}": json.dumps(pair)} if declared else {}
