from __future__ import annotations

import base64
import binascii
import os
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_PRIVATE_KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")


def _configured_public_key(value: str | None) -> tuple[str, str]:
    record = str(value or "").strip()
    if len(record.splitlines()) != 1:
        raise ValueError("The configured SSH public key must be one OpenSSH public-key record")
    fields = record.split()
    if len(fields) < 2:
        raise ValueError("The configured SSH public key must be one OpenSSH public-key record")
    key_type, key_data = fields[:2]
    if not (
        key_type.startswith("ssh-") or key_type.startswith("ecdsa-") or key_type.startswith("sk-")
    ):
        raise ValueError("The configured SSH public key has an unsupported key type")
    try:
        decoded = base64.b64decode(key_data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("The configured SSH public key is malformed") from error
    if not decoded:
        raise ValueError("The configured SSH public key is malformed")
    return key_type, key_data


def _validate_private_key_path(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    try:
        metadata = expanded.lstat()
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"{label} must be owned by the current user")
    if metadata.st_mode & 0o077:
        raise ValueError(f"{label} must not be accessible by group or others")
    return expanded


def _public_key_from_private(path: Path) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("The SSH private key could not be validated") from error
    if result.returncode != 0:
        raise ValueError("The SSH private key is encrypted, malformed, or unusable")
    fields = result.stdout.strip().split()
    if len(fields) < 2:
        raise ValueError("The SSH private key did not produce a public identity")
    return fields[0], fields[1]


@dataclass(frozen=True)
class SSHClientAuth:
    """One exact non-interactive SSH client identity."""

    source: str
    _key_type: str = field(repr=False)
    _key_data: str = field(repr=False)
    _key_path: Path | None = field(default=None, repr=False, compare=False)
    _pkey: Any | None = field(default=None, repr=False, compare=False)
    _identity_file: Path | None = field(default=None, repr=False, compare=False)
    _temporary_owner: Any | None = field(default=None, repr=False, compare=False)

    def paramiko_connect_kwargs(self) -> Mapping[str, Any]:
        kwargs: dict[str, Any] = {
            "allow_agent": False,
            "look_for_keys": False,
            "password": None,
        }
        if self._pkey is not None:
            kwargs["pkey"] = self._pkey
        elif self._key_path is not None:
            kwargs["key_filename"] = str(self._key_path)
        else:
            raise RuntimeError("The resolved SSH client identity is incomplete")
        return kwargs

    def openssh_options(self) -> tuple[str, ...]:
        identity_file = self._identity_file or self._key_path
        if identity_file is None:
            raise RuntimeError("The resolved SSH client identity has no OpenSSH selector")
        return (
            "-i",
            str(identity_file),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-o",
            "BatchMode=yes",
        )

    def matches(self, key_type: str, key_data: str) -> bool:
        return self._key_type == key_type and self._key_data == key_data


def _agent_identity_file(key_type: str, key_data: str) -> tuple[Path, Any]:
    owner = tempfile.TemporaryDirectory(prefix="nebius-vpngw-ssh-auth-")
    path = Path(owner.name) / "identity.pub"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        content = f"{key_type} {key_data}\n".encode("ascii")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path, owner


def resolve_ssh_client_auth(
    configured_public_key: str | None,
    *,
    explicit_private_key: Path | None = None,
    home: Path | None = None,
    paramiko_module: Any | None = None,
) -> SSHClientAuth:
    """Resolve exactly the configured public key without authentication fallback."""

    expected = _configured_public_key(configured_public_key)
    if explicit_private_key is not None:
        private_path = _validate_private_key_path(
            explicit_private_key,
            label="The configured SSH private key",
        )
        if _public_key_from_private(private_path) != expected:
            raise ValueError(
                "The configured SSH private key does not match the configured public key"
            )
        return SSHClientAuth(
            source="explicit-private-key",
            _key_type=expected[0],
            _key_data=expected[1],
            _key_path=private_path,
        )

    paramiko_api = paramiko_module
    if paramiko_api is None:
        try:
            import paramiko as paramiko_api  # type: ignore[import-untyped,no-redef]
        except ImportError as error:
            raise RuntimeError("Paramiko is required to inspect the SSH agent") from error
    assert paramiko_api is not None
    try:
        agent_keys = tuple(paramiko_api.Agent().get_keys())
    except Exception:
        # A broken or unreachable agent must not prevent an exact on-disk key
        # from being selected. Authentication fallback stays disabled either way.
        agent_keys = ()
    matching_agent = tuple(
        key for key in agent_keys if (str(key.get_name()), str(key.get_base64())) == expected
    )
    if len(matching_agent) > 1:
        raise ValueError("The configured SSH public key is ambiguous in the SSH agent")
    if matching_agent:
        identity_file, owner = _agent_identity_file(*expected)
        return SSHClientAuth(
            source="ssh-agent",
            _key_type=expected[0],
            _key_data=expected[1],
            _pkey=matching_agent[0],
            _identity_file=identity_file,
            _temporary_owner=owner,
        )

    ssh_directory = (home or Path.home()).expanduser() / ".ssh"
    matches: list[Path] = []
    for name in _DEFAULT_PRIVATE_KEY_NAMES:
        candidate = ssh_directory / name
        try:
            private_path = _validate_private_key_path(candidate, label="A default SSH private key")
        except ValueError:
            continue
        try:
            candidate_public = _public_key_from_private(private_path)
        except ValueError:
            continue
        if candidate_public == expected:
            matches.append(private_path)
    if len(matches) > 1:
        raise ValueError("Multiple default SSH private keys match the configured public key")
    if not matches:
        raise ValueError(
            "No usable SSH client identity matches the configured public key; configure "
            "ssh_private_key_path, load the exact key in ssh-agent, or install one matching "
            "default private key"
        )
    return SSHClientAuth(
        source="default-private-key",
        _key_type=expected[0],
        _key_data=expected[1],
        _key_path=matches[0],
    )
