from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import importlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .ssh_client_auth import SSHClientAuth

KNOWN_HOSTS_ENV = "VPNGW_SSH_KNOWN_HOSTS_FILE"
HOST_KEYS_DIR_ENV = "VPNGW_SSH_HOST_KEYS_DIR"
VM_HA_SSH_HOST_KEY_PATH = "/etc/ssh/vpngw_host_key"
VM_HA_SSH_TRUST_SCHEMA = "nebius-vpngw/ssh-trust-v2"
VM_HA_SSH_TRUST_DIRECTORY = "nebius-vpngw"
VM_HA_SSH_HOST_KEYS_NAMESPACE = "host-keys"
VM_HA_SSH_TRUST_RECEIPT = "trust.json"
VM_HA_SSH_TRUST_PROJECTION = "known_hosts"


class VMHAReplacementSSHIdentityProblem(str, Enum):
    """Closed reasons why a replacement SSH identity cannot be prepared."""

    ORIGINAL_PRIVATE_KEY_UNAVAILABLE = "original-private-key-unavailable"
    OPERATOR_SOURCE_CONFLICT = "operator-source-conflict"
    MANAGED_PREDECESSOR_UNAVAILABLE = "managed-predecessor-unavailable"


class VMHAReplacementSSHIdentityUnavailable(ValueError):
    """A replacement member's original or approved managed SSH identity is unavailable."""

    def __init__(
        self,
        message: str,
        *,
        rotation_intent: VMHASSHIdentityRotationIntent | None = None,
        problem: VMHAReplacementSSHIdentityProblem = (
            VMHAReplacementSSHIdentityProblem.ORIGINAL_PRIVATE_KEY_UNAVAILABLE
        ),
    ) -> None:
        super().__init__(message)
        self.rotation_intent = rotation_intent
        self.problem = problem


class LegacyOrdinarySSHEnrollmentRequired(ValueError):
    """A retained ordinary gateway needs its one-time bounded trust enrollment."""

    def __init__(self, hostnames: Iterable[str]) -> None:
        self.hostnames = frozenset(_safe_hostname(hostname) for hostname in hostnames)
        names = ", ".join(sorted(self.hostnames))
        super().__init__(
            f"Retained ordinary gateway SSH trust requires one-time network enrollment for {names}"
        )


@dataclass(frozen=True)
class SSHTrustAuthority:
    """Secret-free provenance for one exact member pin."""

    kind: str
    compute_binding_sha256: str | None = None
    predecessor_receipt_sha256: str | None = None


@dataclass(frozen=True)
class SSHHostKeyEnrollment:
    """One bounded network observation tied to an unchanged Compute resource."""

    hostname: str
    key_type: str
    key_data: str = field(repr=False)
    authority: SSHTrustAuthority
    assert_current: Callable[[], None] = field(repr=False, compare=False)


@dataclass(frozen=True)
class ManagedSSHTrustMember:
    """One validated member from a product-managed trust receipt."""

    hostname: str
    pins: Mapping[str, str] = field(repr=False)
    authority: SSHTrustAuthority
    receipt_sha256: str


@dataclass(frozen=True)
class TrustedSSHMemberImport:
    """One predecessor-receipt member rebound to a current Compute resource."""

    hostname: str
    pins: Mapping[str, str] = field(repr=False)
    predecessor_receipt_sha256: str
    compute_binding_sha256: str
    assert_current: Callable[[], None] = field(repr=False, compare=False)


@dataclass(frozen=True)
class VMHASSHTrustScope:
    """Stable deployment identity for one product-managed VM-HA trust store."""

    tenant_id: str
    project_id: str
    region_id: str
    gateway_name: str
    cluster_id: str

    def values(self) -> tuple[str, str, str, str, str]:
        return (
            str(self.tenant_id).strip(),
            str(self.project_id).strip(),
            str(self.region_id).strip(),
            str(self.gateway_name).strip(),
            str(self.cluster_id).strip(),
        )

    def as_dict(self) -> dict[str, str]:
        return dict(zip(_TRUST_SCOPE_FIELDS, self.values(), strict=True))

    @property
    def digest(self) -> str:
        if any(not value for value in self.values()):
            raise ValueError("VM-HA managed SSH trust requires complete deployment identity")
        return hashlib.sha256(_canonical_json(self.as_dict())).hexdigest()


@dataclass(frozen=True)
class VMHASSHIdentityRotationIntent:
    """Secret-free authority to replace one absent managed member identity."""

    hostname: str
    trust_scope_sha256: str
    old_fingerprint: str
    predecessor_receipt_sha256: str | None
    predecessor_projection_sha256: str | None
    storage_owner: str = "product-managed-default"

    def approval_state(self) -> dict[str, str]:
        return {
            "action": "generate-and-rotate-missing-non-owner-ssh-identity",
            "hostname": self.hostname,
            "old_fingerprint": self.old_fingerprint,
            "predecessor_projection_sha256": (self.predecessor_projection_sha256 or "absent"),
            "predecessor_receipt_sha256": self.predecessor_receipt_sha256 or "absent",
            "storage_owner": self.storage_owner,
            "trust_scope_sha256": self.trust_scope_sha256,
        }


@dataclass(frozen=True)
class VMHASSHIdentityRotationStage:
    """One retry-stable managed replacement identity and desired public trust."""

    hostname: str
    stage_token: str
    new_fingerprint: str
    successor_receipt_sha256: str
    successor_projection_sha256: str
    _private_key: bytes = field(repr=False, compare=False)
    _key_type: str = field(repr=False)
    _key_data: str = field(repr=False)
    _receipt_content: bytes = field(repr=False, compare=False)
    _projection_content: bytes = field(repr=False, compare=False)


_TRUST_SCOPE_FIELDS = (
    "tenant_id",
    "project_id",
    "region_id",
    "gateway_name",
    "cluster_id",
)
_SSH_TRUST_AUTHORITY_KINDS = frozenset(
    {
        "default-known-hosts-v1",
        "explicit-override-v1",
        "legacy-ordinary-network-enrollment-v1",
        "managed-projection-v1",
        "ordinary-migration-v1",
        "product-generated-v1",
        "product-provisioning-v1",
        "product-rotated-v1",
    }
)
_COMPUTE_BOUND_AUTHORITY_KINDS = frozenset(
    {
        "legacy-ordinary-network-enrollment-v1",
        "ordinary-migration-v1",
    }
)


def _default_vm_ha_ssh_host_keys_directory(scope: VMHASSHTrustScope) -> Path:
    gateway_name = str(scope.gateway_name).strip()
    path_token = gateway_name.replace("-", "")
    if (
        not gateway_name
        or len(gateway_name) > 64
        or not gateway_name.isascii()
        or not path_token.isalnum()
        or not gateway_name[0].isalnum()
        or not gateway_name[-1].isalnum()
    ):
        raise ValueError("VM-HA gateway name is not safe for the default SSH host-key directory")
    return (
        Path.home()
        / ".ssh"
        / VM_HA_SSH_TRUST_DIRECTORY
        / VM_HA_SSH_HOST_KEYS_NAMESPACE
        / gateway_name
        / scope.digest
    )


@dataclass(frozen=True)
class _ManagedTrustPaths:
    ssh_directory: Path
    product_directory: Path
    scope_directory: Path
    receipt: Path
    projection: Path
    lock: Path


@dataclass(frozen=True)
class _ManagedTrustUpdate:
    paths: _ManagedTrustPaths
    receipt_content: bytes
    projection_content: bytes
    expected_receipt_sha256: str | None
    expected_projection_sha256: str | None


@dataclass(frozen=True)
class _ExternalFileSnapshot:
    path: Path
    content: bytes = field(repr=False)
    content_sha256: str
    file_identity: tuple[int, int, int, int, int, int, int, int]

    def assert_current(self) -> None:
        current = _read_default_known_hosts_snapshot(self.path)
        if (
            current is None
            or current.file_identity != self.file_identity
            or not hmac.compare_digest(current.content_sha256, self.content_sha256)
        ):
            raise RuntimeError("VM-HA default known-hosts evidence changed after preflight")


@dataclass(frozen=True)
class _RecoveredHostKeysUpdate:
    directory: Path
    identities: tuple[SSHHostKeyRecovery, ...]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        + b"\n"
    )


def _regular_file_state(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f"{label} must name a non-empty readable regular file") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
            raise ValueError(f"{label} must name a non-empty readable regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content:
            raise ValueError(f"{label} must name a non-empty readable regular file")
        return content, metadata
    finally:
        os.close(descriptor)


def _regular_file(path: Path, label: str) -> bytes:
    return _regular_file_state(path, label)[0]


def _read_default_known_hosts_snapshot(path: Path) -> _ExternalFileSnapshot | None:
    """Read the literal user known-hosts file as a bounded migration candidate."""

    ssh_directory = Path.home() / ".ssh"
    if path != ssh_directory / "known_hosts":
        return None
    try:
        directory_metadata = ssh_directory.lstat()
    except OSError:
        return None
    if (
        stat.S_ISLNK(directory_metadata.st_mode)
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and directory_metadata.st_uid != os.getuid())
    ):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > 16 * 1024 * 1024
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or (hasattr(os, "getuid") and before.st_uid != os.getuid())
        ):
            return None
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if not content or len(content) != before.st_size or identity != after_identity:
            return None
        return _ExternalFileSnapshot(
            path=path,
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            file_identity=identity,
        )
    finally:
        os.close(descriptor)


def _managed_trust_paths(
    scope: VMHASSHTrustScope,
    managed_root: Path | None,
) -> _ManagedTrustPaths:
    ssh_directory = Path.home() / ".ssh"
    product_directory = (
        Path(managed_root).expanduser()
        if managed_root is not None
        else ssh_directory / VM_HA_SSH_TRUST_DIRECTORY
    )
    if not product_directory.is_absolute():
        raise ValueError("VM-HA managed SSH trust root must be absolute")
    scope_directory = product_directory / scope.digest
    return _ManagedTrustPaths(
        ssh_directory=ssh_directory,
        product_directory=product_directory,
        scope_directory=scope_directory,
        receipt=scope_directory / VM_HA_SSH_TRUST_RECEIPT,
        projection=scope_directory / VM_HA_SSH_TRUST_PROJECTION,
        lock=scope_directory / ".lock",
    )


def _validate_managed_directory(path: Path, label: str, *, private: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a non-symlink directory")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ValueError(f"{label} must be owned by the current user")
    forbidden = 0o077 if private else 0o022
    if metadata.st_mode & forbidden:
        raise ValueError(f"{label} has unsafe permissions")


def _validate_managed_path(path: Path, paths: _ManagedTrustPaths) -> None:
    if paths.product_directory == paths.ssh_directory / VM_HA_SSH_TRUST_DIRECTORY:
        _validate_managed_directory(paths.ssh_directory, "SSH directory", private=False)
    _validate_managed_directory(
        paths.product_directory,
        "VM-HA managed SSH trust directory",
        private=True,
    )
    _validate_managed_directory(
        paths.scope_directory,
        "VM-HA deployment SSH trust directory",
        private=True,
    )
    try:
        path.relative_to(paths.scope_directory)
    except ValueError:
        raise ValueError("VM-HA managed SSH trust path escaped its deployment scope") from None


def _read_managed_file(path: Path, paths: _ManagedTrustPaths, label: str) -> bytes | None:
    _validate_managed_path(path, paths)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size == 0
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ValueError(f"{label} must be an owner-only regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content:
            raise ValueError(f"{label} must be non-empty")
        return content
    finally:
        os.close(descriptor)


def _content_sha256(content: bytes | None) -> str | None:
    return None if content is None else hashlib.sha256(content).hexdigest()


def _safe_hostname(hostname: str) -> str:
    value = str(hostname).strip()
    if (
        not value
        or any(character.isspace() for character in value)
        or any(character in value for character in ",*?[]\\/\x00")
        or value.startswith(("|", "@", "!"))
    ):
        raise ValueError("VM-HA SSH member hostname is not safe for exact host-key binding")
    return value


def _hashed_host_matches(pattern: str, target: str) -> bool:
    if not pattern.startswith("|1|"):
        return False
    fields = pattern.split("|")
    if len(fields) != 4 or fields[:2] != ["", "1"]:
        raise ValueError("VM-HA SSH known-hosts source contains an invalid hashed host")
    try:
        salt = base64.b64decode(fields[2], validate=True)
        expected = base64.b64decode(fields[3], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("VM-HA SSH known-hosts source contains an invalid hashed host") from None
    actual = hmac.new(salt, target.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(actual, expected)


def _host_field_matches(host_field: str, aliases: set[str]) -> bool:
    positive = False
    for raw_pattern in host_field.split(","):
        negated = raw_pattern.startswith("!")
        pattern = raw_pattern[1:] if negated else raw_pattern
        matches = any(
            hmac.compare_digest(pattern, alias)
            if not pattern.startswith("|1|")
            else _hashed_host_matches(pattern, alias)
            for alias in aliases
        )
        if matches and negated:
            return False
        positive = positive or matches
    return positive


def _validate_public_key(key_type: str, key_data: str) -> None:
    if not key_type or key_type.endswith("-cert-v01@openssh.com"):
        raise ValueError("VM-HA SSH trust requires raw public host keys")
    try:
        decoded = base64.b64decode(key_data, validate=True)
        paramiko = importlib.import_module("paramiko")
        entry = paramiko.hostkeys.HostKeyEntry.from_line(f"pin {key_type} {key_data}")
    except (binascii.Error, ValueError, TypeError, AttributeError) as error:
        raise ValueError("VM-HA SSH known-hosts source contains an invalid public key") from error
    if not decoded or entry is None or entry.key is None or entry.key.get_name() != key_type:
        raise ValueError("VM-HA SSH known-hosts source contains an invalid public key")


def _public_key_fingerprint(key_data: str) -> str:
    try:
        decoded = base64.b64decode(key_data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("VM-HA SSH trust receipt contains an invalid public key") from error
    digest = base64.b64encode(hashlib.sha256(decoded).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _parse_exact_pins(
    content: bytes,
    host_aliases: Mapping[str, Iterable[str]],
    *,
    label: str,
) -> dict[str, dict[str, str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    aliases_by_host = {
        _safe_hostname(hostname): {str(alias).strip() for alias in aliases if str(alias).strip()}
        for hostname, aliases in host_aliases.items()
    }
    pins: dict[str, dict[str, str]] = {hostname: {} for hostname in aliases_by_host}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        marker: str | None = None
        if fields[0].startswith("@"):
            if len(fields) < 4:
                raise ValueError(f"{label} contains a malformed record on line {line_number}")
            marker, host_field, key_type, key_data = fields[:4]
        else:
            if len(fields) < 3:
                raise ValueError(f"{label} contains a malformed record on line {line_number}")
            host_field, key_type, key_data = fields[:3]
        matching_hosts = [
            hostname
            for hostname, aliases in aliases_by_host.items()
            if _host_field_matches(host_field, aliases)
        ]
        if not matching_hosts:
            continue
        if marker == "@revoked":
            raise ValueError(f"{label} marks a requested VM-HA host key as revoked")
        if marker is not None:
            if marker == "@cert-authority":
                continue
            raise ValueError(f"{label} uses an unsupported marker for a requested VM-HA host")
        _validate_public_key(key_type, key_data)
        for hostname in matching_hosts:
            previous = pins[hostname].get(key_type)
            if previous is not None and not hmac.compare_digest(previous, key_data):
                raise ValueError(f"{label} contains conflicting exact pins for {hostname}")
            pins[hostname][key_type] = key_data
    return pins


def _parse_default_known_hosts_pins(
    content: bytes,
    host_aliases: Mapping[str, Iterable[str]],
) -> dict[str, dict[str, str]]:
    """Select only unambiguous exact Ed25519 records; ignore unrelated damage."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {_safe_hostname(hostname): {} for hostname in host_aliases}
    aliases_by_host = {
        _safe_hostname(hostname): {str(alias).strip() for alias in aliases if str(alias).strip()}
        for hostname, aliases in host_aliases.items()
    }
    candidates: dict[str, set[str]] = {hostname: set() for hostname in aliases_by_host}
    unusable: set[str] = set()
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        marker: str | None = None
        if fields and fields[0].startswith("@"):
            if len(fields) < 2:
                continue
            marker = fields[0]
            host_field = fields[1]
            key_fields = fields[2:4]
        else:
            if not fields:
                continue
            host_field = fields[0]
            key_fields = fields[1:3]
        matching_hosts: list[str] = []
        for hostname, aliases in aliases_by_host.items():
            try:
                matches = _host_field_matches(host_field, aliases)
            except ValueError:
                matches = False
            if matches:
                matching_hosts.append(hostname)
        if not matching_hosts:
            continue
        if marker == "@revoked":
            raise ValueError("default SSH known-hosts file revokes a requested VM-HA host key")
        if marker is not None:
            continue
        if len(key_fields) != 2 or key_fields[0] != "ssh-ed25519":
            continue
        key_type, key_data = key_fields
        try:
            _validate_public_key(key_type, key_data)
        except ValueError:
            unusable.update(matching_hosts)
            continue
        for hostname in matching_hosts:
            candidates[hostname].add(key_data)
    return {
        hostname: (
            {"ssh-ed25519": next(iter(values))}
            if len(values) == 1 and hostname not in unusable
            else {}
        )
        for hostname, values in candidates.items()
    }


def _canonical_known_hosts(
    pins: Mapping[str, Mapping[str, str]],
    aliases: Mapping[str, Iterable[str]],
) -> bytes:
    lines = [
        f"{','.join((_safe_hostname(hostname), *sorted(set(aliases[hostname]) - {hostname})))} "
        f"{key_type} {key_data}\n"
        for hostname in sorted(pins)
        for key_type, key_data in sorted(pins[hostname].items())
    ]
    if not lines:
        raise ValueError("VM-HA SSH trust contains no exact member pins")
    return "".join(lines).encode("utf-8")


def _build_receipt(
    scope: VMHASSHTrustScope,
    pins: Mapping[str, Mapping[str, str]],
    authorities: Mapping[str, SSHTrustAuthority],
) -> bytes:
    members = []
    for hostname in sorted(pins):
        keys = []
        for key_type, key_data in sorted(pins[hostname].items()):
            keys.append(
                {
                    "fingerprint": _public_key_fingerprint(key_data),
                    "key_data": key_data,
                    "key_type": key_type,
                }
            )
        if not keys:
            raise ValueError(f"VM-HA SSH host pin is unavailable for {hostname}")
        authority = _validate_ssh_trust_authority(authorities.get(hostname))
        members.append(
            {
                "authority": {
                    "compute_binding_sha256": authority.compute_binding_sha256,
                    "kind": authority.kind,
                    "predecessor_receipt_sha256": authority.predecessor_receipt_sha256,
                },
                "hostname": _safe_hostname(hostname),
                "keys": keys,
            }
        )
    return _canonical_json(
        {
            "members": members,
            "schema": VM_HA_SSH_TRUST_SCHEMA,
            "scope": scope.as_dict(),
            "scope_sha256": scope.digest,
        }
    )


def _validate_optional_digest(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"VM-HA managed SSH trust {label} is invalid")
    return value


def _validate_ssh_trust_authority(
    authority: SSHTrustAuthority | None,
) -> SSHTrustAuthority:
    if authority is None or authority.kind not in _SSH_TRUST_AUTHORITY_KINDS:
        raise ValueError("VM-HA managed SSH trust authority is invalid")
    compute_binding = _validate_optional_digest(
        authority.compute_binding_sha256,
        "Compute binding",
    )
    predecessor = _validate_optional_digest(
        authority.predecessor_receipt_sha256,
        "predecessor receipt",
    )
    if authority.kind in _COMPUTE_BOUND_AUTHORITY_KINDS and compute_binding is None:
        raise ValueError("VM-HA managed SSH trust authority lacks its Compute binding")
    if authority.kind not in _COMPUTE_BOUND_AUTHORITY_KINDS and compute_binding is not None:
        raise ValueError("VM-HA managed SSH trust authority has an unexpected Compute binding")
    if authority.kind != "ordinary-migration-v1" and predecessor is not None:
        raise ValueError("VM-HA managed SSH trust authority has an unexpected predecessor")
    if authority.kind == "ordinary-migration-v1" and predecessor is None:
        raise ValueError("VM-HA managed SSH trust migration authority lacks its predecessor")
    return authority


def _parse_receipt(
    content: bytes,
    scope: VMHASSHTrustScope,
) -> tuple[dict[str, dict[str, str]], dict[str, SSHTrustAuthority]]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("VM-HA managed SSH trust receipt is malformed") from error
    if not isinstance(payload, dict) or payload.get("schema") != VM_HA_SSH_TRUST_SCHEMA:
        raise ValueError("VM-HA managed SSH trust receipt has the wrong schema")
    if payload.get("scope") != scope.as_dict() or payload.get("scope_sha256") != scope.digest:
        raise ValueError("VM-HA managed SSH trust receipt belongs to another deployment")
    rows = payload.get("members")
    if not isinstance(rows, list) or not rows:
        raise ValueError("VM-HA managed SSH trust receipt contains no members")
    pins: dict[str, dict[str, str]] = {}
    authorities: dict[str, SSHTrustAuthority] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("VM-HA managed SSH trust receipt contains an invalid member")
        hostname = _safe_hostname(str(row.get("hostname") or ""))
        if hostname in pins or not isinstance(row.get("keys"), list) or not row["keys"]:
            raise ValueError("VM-HA managed SSH trust receipt contains an invalid member")
        host_pins: dict[str, str] = {}
        raw_authority = row.get("authority")
        if not isinstance(raw_authority, dict) or set(raw_authority) != {
            "compute_binding_sha256",
            "kind",
            "predecessor_receipt_sha256",
        }:
            raise ValueError("VM-HA managed SSH trust receipt contains invalid authority")
        authority = _validate_ssh_trust_authority(
            SSHTrustAuthority(
                kind=str(raw_authority.get("kind") or ""),
                compute_binding_sha256=raw_authority.get("compute_binding_sha256"),
                predecessor_receipt_sha256=raw_authority.get("predecessor_receipt_sha256"),
            )
        )
        for key in row["keys"]:
            if not isinstance(key, dict):
                raise ValueError("VM-HA managed SSH trust receipt contains an invalid key")
            key_type = str(key.get("key_type") or "")
            key_data = str(key.get("key_data") or "")
            _validate_public_key(key_type, key_data)
            if key.get("fingerprint") != _public_key_fingerprint(key_data):
                raise ValueError("VM-HA managed SSH trust receipt fingerprint does not match")
            previous = host_pins.get(key_type)
            if previous is not None and not hmac.compare_digest(previous, key_data):
                raise ValueError("VM-HA managed SSH trust receipt contains conflicting pins")
            host_pins[key_type] = key_data
        pins[hostname] = host_pins
        authorities[hostname] = authority
    return pins, authorities


def _write_atomic(path: Path, content: bytes) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ensure_managed_directory(path: Path, label: str, *, private: bool = True) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _validate_managed_directory(path, label, private=private)


def _validate_default_host_key_directories(directory: Path) -> None:
    ssh_directory = Path.home() / ".ssh"
    product_directory = ssh_directory / VM_HA_SSH_TRUST_DIRECTORY
    namespace_directory = product_directory / VM_HA_SSH_HOST_KEYS_NAMESPACE
    gateway_directory = directory.parent
    if gateway_directory.parent != namespace_directory:
        raise ValueError("VM-HA default SSH host-key directory escaped its managed namespace")
    _validate_managed_directory(ssh_directory, "SSH directory", private=False)
    _validate_managed_directory(
        product_directory,
        "VM-HA managed SSH trust directory",
        private=True,
    )
    _validate_managed_directory(
        namespace_directory,
        "VM-HA managed SSH host-key namespace",
        private=True,
    )
    _validate_managed_directory(
        gateway_directory,
        "VM-HA per-gateway SSH host-key directory",
        private=True,
    )
    _validate_managed_directory(
        directory,
        "VM-HA deployment SSH host-key directory",
        private=True,
    )


def _ensure_default_host_key_directories(directory: Path) -> None:
    ssh_directory = Path.home() / ".ssh"
    product_directory = ssh_directory / VM_HA_SSH_TRUST_DIRECTORY
    namespace_directory = product_directory / VM_HA_SSH_HOST_KEYS_NAMESPACE
    gateway_directory = directory.parent
    if gateway_directory.parent != namespace_directory:
        raise ValueError("VM-HA default SSH host-key directory escaped its managed namespace")
    _ensure_managed_directory(ssh_directory, "SSH directory", private=False)
    _ensure_managed_directory(
        product_directory,
        "VM-HA managed SSH trust directory",
    )
    _ensure_managed_directory(
        namespace_directory,
        "VM-HA managed SSH host-key namespace",
    )
    _ensure_managed_directory(
        gateway_directory,
        "VM-HA per-gateway SSH host-key directory",
    )
    _ensure_managed_directory(
        directory,
        "VM-HA deployment SSH host-key directory",
    )


@contextmanager
def _default_host_key_lock(directory: Path) -> Iterator[None]:
    _ensure_default_host_key_directories(directory)
    lock_path = directory / ".lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ValueError("VM-HA managed SSH host-key lock has unsafe ownership or permissions")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _generate_ed25519_host_key(
    hostname: str,
    *,
    parent: Path | None = None,
) -> tuple[bytes, str, str]:
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{hostname}-",
            dir=parent,
        ) as temporary:
            private_key = Path(temporary) / "host.key"
            subprocess.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    "",
                    "-f",
                    str(private_key),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            return _validated_private_host_key(private_key, hostname)
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"VM-HA SSH private host-key generation failed for fresh member {hostname}"
        ) from error


def _write_exclusive_private_key(path: Path, content: bytes) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _prepare_persistent_default_host_keys(
    directory: Path,
    enrollment: set[str],
) -> None:
    try:
        with _default_host_key_lock(directory):
            for hostname in sorted(enrollment):
                private_key = directory / f"{hostname}.key"
                try:
                    private_key.lstat()
                except FileNotFoundError:
                    content, _, _ = _generate_ed25519_host_key(hostname, parent=directory)
                    try:
                        _write_exclusive_private_key(private_key, content)
                    except FileExistsError:
                        pass
                except OSError as error:
                    raise ValueError(
                        f"VM-HA SSH private host key for fresh member {hostname} is unavailable"
                    ) from error
                _validated_private_host_key(private_key, hostname)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("VM-HA managed SSH host-key persistence failed") from error


@contextmanager
def _managed_trust_lock(paths: _ManagedTrustPaths) -> Iterator[None]:
    if paths.product_directory == paths.ssh_directory / VM_HA_SSH_TRUST_DIRECTORY:
        _ensure_managed_directory(paths.ssh_directory, "SSH directory", private=False)
    _ensure_managed_directory(paths.product_directory, "VM-HA managed SSH trust directory")
    _ensure_managed_directory(paths.scope_directory, "VM-HA deployment SSH trust directory")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(paths.lock, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise ValueError("VM-HA managed SSH trust lock has unsafe ownership or permissions")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class SSHHostKeyRecovery:
    """One authenticated product host key plus its post-SSH evidence reread."""

    hostname: str
    private_key: bytes = field(repr=False)
    assert_current: Callable[[], None] = field(repr=False, compare=False)


@dataclass(frozen=True)
class SSHHostIdentity:
    hostname: str
    private_key: bytes = field(repr=False)

    def cloud_init_entries(self) -> str:
        encoded = base64.b64encode(self.private_key).decode("ascii")
        return (
            f"  - path: {VM_HA_SSH_HOST_KEY_PATH}\n"
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
    transport_targets: tuple[tuple[str, str], ...] = ()
    identities: tuple[SSHHostIdentity, ...] = ()
    managed_action: str | None = None
    managed_receipt_sha256: str | None = None
    _snapshot_owner: Any = field(default=None, repr=False, compare=False)
    _managed_update: _ManagedTrustUpdate | None = field(default=None, repr=False, compare=False)
    _recovered_host_keys_update: _RecoveredHostKeysUpdate | None = field(
        default=None, repr=False, compare=False
    )
    _evidence_checks: tuple[Callable[[], None], ...] = field(default=(), repr=False, compare=False)

    def assert_current(self) -> None:
        if (
            hashlib.sha256(_regular_file(self.known_hosts_file, KNOWN_HOSTS_ENV)).hexdigest()
            != self.known_hosts_sha256
        ):
            raise RuntimeError("VM-HA immutable SSH trust source changed after preflight")
        for check in self._evidence_checks:
            check()

    def identity_for(self, hostname: str) -> SSHHostIdentity:
        self.assert_current()
        matching = tuple(item for item in self.identities if item.hostname == hostname)
        if len(matching) != 1:
            raise ValueError(f"VM-HA SSH host identity is unavailable for {hostname}")
        return matching[0]

    def pin_target_for(self, hostname: str) -> str:
        matching = tuple(target for name, target in self.pin_targets if name == hostname)
        if len(matching) != 1:
            raise ValueError(f"VM-HA SSH host pin is unavailable for {hostname}")
        return matching[0]

    def hostname_for_transport(self, transport_host: str) -> str:
        matching = tuple(
            hostname
            for hostname, target in self.transport_targets
            if hmac.compare_digest(target, transport_host)
        )
        if len(matching) != 1:
            raise ValueError(
                "VM-HA SSH trust does not bind exactly one member to this transport address"
            )
        return matching[0]


def publish_vm_ha_ssh_trust(policy: SSHTrustPolicy) -> bool:
    """Publish a preflighted managed receipt/projection before cloud mutation."""

    update = policy._managed_update
    recovered_update = policy._recovered_host_keys_update
    if update is None and recovered_update is None:
        return False
    policy.assert_current()
    if recovered_update is not None:
        with _default_host_key_lock(recovered_update.directory):
            for recovery in recovered_update.identities:
                path = recovered_update.directory / f"{recovery.hostname}.key"
                try:
                    path.lstat()
                except FileNotFoundError:
                    _write_exclusive_private_key(path, recovery.private_key)
                content, _, _ = _validated_private_host_key(path, recovery.hostname)
                if not hmac.compare_digest(content, recovery.private_key):
                    raise RuntimeError(
                        "VM-HA recovered SSH host identity changed during publication"
                    )
    if update is None:
        return True
    with _managed_trust_lock(update.paths):
        current_receipt = _read_managed_file(
            update.paths.receipt,
            update.paths,
            "VM-HA managed SSH trust receipt",
        )
        current_projection = _read_managed_file(
            update.paths.projection,
            update.paths,
            "VM-HA managed SSH trust projection",
        )
        if (
            _content_sha256(current_receipt) != update.expected_receipt_sha256
            or _content_sha256(current_projection) != update.expected_projection_sha256
        ):
            raise RuntimeError("VM-HA managed SSH trust changed after preflight")
        if current_receipt != update.receipt_content:
            _write_atomic(update.paths.receipt, update.receipt_content)
        if current_projection != update.projection_content:
            _write_atomic(update.paths.projection, update.projection_content)
    return True


def managed_ssh_trust_available(
    scope: VMHASSHTrustScope,
    *,
    managed_root: Path | None = None,
) -> bool:
    """Return whether a validated managed receipt or projection exists for a scope."""

    paths = _managed_trust_paths(scope, managed_root)
    receipt = _read_managed_file(
        paths.receipt,
        paths,
        "managed SSH trust receipt",
    )
    projection = _read_managed_file(
        paths.projection,
        paths,
        "managed SSH trust projection",
    )
    return receipt is not None or projection is not None


def managed_ssh_trust_member(
    scope: VMHASSHTrustScope,
    hostname: str,
    *,
    managed_root: Path | None = None,
) -> ManagedSSHTrustMember | None:
    """Read one exact authority-bearing member from the managed receipt."""

    paths = _managed_trust_paths(scope, managed_root)
    receipt = _read_managed_file(
        paths.receipt,
        paths,
        "managed SSH trust receipt",
    )
    if receipt is None:
        return None
    pins, authorities = _parse_receipt(receipt, scope)
    canonical = _safe_hostname(hostname)
    member_pins = pins.get(canonical)
    authority = authorities.get(canonical)
    if not member_pins or authority is None:
        return None
    return ManagedSSHTrustMember(
        hostname=canonical,
        pins=dict(member_pins),
        authority=authority,
        receipt_sha256=hashlib.sha256(receipt).hexdigest(),
    )


def _validate_vm_ha_ssh_identity_rotation_intent(
    intent: VMHASSHIdentityRotationIntent,
) -> None:
    try:
        canonical_hostname = _safe_hostname(intent.hostname)
    except ValueError:
        raise ValueError("VM-HA SSH identity rotation intent is invalid") from None
    predecessor_digests = (
        intent.predecessor_receipt_sha256,
        intent.predecessor_projection_sha256,
    )
    if (
        canonical_hostname != intent.hostname
        or intent.storage_owner != "product-managed-default"
        or not re.fullmatch(r"[0-9a-f]{64}", intent.trust_scope_sha256)
        or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", intent.old_fingerprint)
        or not any(predecessor_digests)
        or any(
            digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in predecessor_digests
        )
    ):
        raise ValueError("VM-HA SSH identity rotation intent is invalid")


def _rotation_stage_token(
    intent: VMHASSHIdentityRotationIntent,
    operation_id: str,
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", operation_id):
        raise ValueError("VM-HA SSH identity rotation operation identity is invalid")
    return hashlib.sha256(
        (
            "nebius-vpngw/vm-ha-ssh-identity-rotation-v1\0"
            f"{intent.hostname}\0{intent.trust_scope_sha256}\0{operation_id}"
        ).encode()
    ).hexdigest()


def _rotation_key_paths(
    scope: VMHASSHTrustScope,
    intent: VMHASSHIdentityRotationIntent,
    operation_id: str,
) -> tuple[Path, Path, str]:
    _validate_vm_ha_ssh_identity_rotation_intent(intent)
    if not hmac.compare_digest(scope.digest, intent.trust_scope_sha256):
        raise ValueError("VM-HA SSH identity rotation belongs to another deployment")
    directory = _default_vm_ha_ssh_host_keys_directory(scope)
    token = _rotation_stage_token(intent, operation_id)
    return (
        directory / f".{intent.hostname}.replacement-{token}.key",
        directory / f"{intent.hostname}.key",
        token,
    )


def _rotation_key_material(
    path: Path,
    hostname: str,
) -> tuple[bytes, str, str, str]:
    content, key_type, key_data = _validated_private_host_key(path, hostname)
    return content, key_type, key_data, _public_key_fingerprint(key_data)


def _rotation_source_pins(
    *,
    receipt: bytes | None,
    projection: bytes | None,
    scope: VMHASSHTrustScope,
    aliases: Mapping[str, set[str]],
) -> tuple[dict[str, dict[str, str]], dict[str, SSHTrustAuthority]]:
    if receipt is not None:
        pins, authorities = _parse_receipt(receipt, scope)
        if set(pins) != set(aliases):
            raise RuntimeError("VM-HA managed SSH trust member set changed during rotation")
        return pins, authorities
    if projection is not None:
        parsed = _parse_exact_pins(
            projection,
            aliases,
            label="VM-HA managed SSH trust projection",
        )
        if any(not parsed.get(hostname) for hostname in aliases):
            raise RuntimeError("VM-HA managed SSH trust projection is incomplete")
        return parsed, {
            hostname: SSHTrustAuthority(kind="managed-projection-v1") for hostname in parsed
        }
    raise RuntimeError("VM-HA managed SSH trust predecessor is unavailable")


def validate_vm_ha_ssh_identity_rotation(
    intent: VMHASSHIdentityRotationIntent,
    *,
    trust_scope: VMHASSHTrustScope,
    expected_successor_receipt_sha256: str | None = None,
    expected_successor_projection_sha256: str | None = None,
    managed_root: Path | None = None,
) -> None:
    """Reprove the approved managed trust predecessor or exact successor."""

    _validate_vm_ha_ssh_identity_rotation_intent(intent)
    if not hmac.compare_digest(trust_scope.digest, intent.trust_scope_sha256):
        raise ValueError("VM-HA SSH identity rotation belongs to another deployment")
    paths = _managed_trust_paths(trust_scope, managed_root)
    with _managed_trust_lock(paths):
        receipt = _read_managed_file(
            paths.receipt,
            paths,
            "VM-HA managed SSH trust receipt",
        )
        projection = _read_managed_file(
            paths.projection,
            paths,
            "VM-HA managed SSH trust projection",
        )
        receipt_sha256 = _content_sha256(receipt)
        projection_sha256 = _content_sha256(projection)
        predecessor = (
            receipt_sha256 == intent.predecessor_receipt_sha256
            and projection_sha256 == intent.predecessor_projection_sha256
        )
        successor_or_partial = bool(
            expected_successor_receipt_sha256
            and receipt_sha256 == expected_successor_receipt_sha256
            and projection_sha256
            in {
                intent.predecessor_projection_sha256,
                expected_successor_projection_sha256,
            }
        )
        if not predecessor and not successor_or_partial:
            raise RuntimeError("VM-HA managed SSH trust changed after rotation approval")


def prepare_vm_ha_ssh_identity_rotation(
    intent: VMHASSHIdentityRotationIntent,
    *,
    operation_id: str,
    trust_scope: VMHASSHTrustScope,
    hosts: Iterable[str | tuple[str, str]],
    additional_aliases: Mapping[str, Iterable[str]] | None = None,
    expected_new_fingerprint: str | None = None,
    expected_successor_receipt_sha256: str | None = None,
    expected_successor_projection_sha256: str | None = None,
    managed_root: Path | None = None,
) -> VMHASSHIdentityRotationStage:
    """Stage or resume one approved replacement key without publishing trust."""

    host_pairs = tuple(
        (_safe_hostname(host), _safe_hostname(host))
        if isinstance(host, str)
        else (_safe_hostname(host[0]), _safe_hostname(host[1]))
        for host in hosts
    )
    hostnames = {hostname for hostname, _target in host_pairs}
    if intent.hostname not in hostnames or len(hostnames) != len(host_pairs):
        raise ValueError("VM-HA SSH identity rotation target is outside the deployment")
    extra_aliases = additional_aliases or {}
    aliases = {
        hostname: {
            hostname,
            target,
            *(_safe_hostname(alias) for alias in extra_aliases.get(hostname, ())),
        }
        for hostname, target in host_pairs
    }
    stage_path, canonical_path, stage_token = _rotation_key_paths(
        trust_scope,
        intent,
        operation_id,
    )
    directory = canonical_path.parent
    with _default_host_key_lock(directory):
        stage_material: tuple[bytes, str, str, str] | None = None
        canonical_material: tuple[bytes, str, str, str] | None = None
        try:
            stage_path.lstat()
        except FileNotFoundError:
            pass
        else:
            stage_material = _rotation_key_material(stage_path, intent.hostname)
        try:
            canonical_path.lstat()
        except FileNotFoundError:
            pass
        else:
            canonical_material = _rotation_key_material(canonical_path, intent.hostname)
        if (
            stage_material is not None
            and canonical_material is not None
            and not hmac.compare_digest(stage_material[0], canonical_material[0])
        ):
            raise RuntimeError("VM-HA staged and canonical replacement identities conflict")
        material = canonical_material or stage_material
        if material is None:
            if expected_new_fingerprint is not None:
                raise RuntimeError("VM-HA approved replacement SSH identity is unavailable")
            content, key_type, key_data = _generate_ed25519_host_key(
                intent.hostname,
                parent=directory,
            )
            _write_exclusive_private_key(stage_path, content)
            material = _rotation_key_material(stage_path, intent.hostname)
        elif (
            canonical_material is not None
            and stage_material is None
            and expected_new_fingerprint is None
        ):
            raise RuntimeError("VM-HA canonical replacement SSH identity appeared before staging")
        content, key_type, key_data, fingerprint = material
        if expected_new_fingerprint is not None and not hmac.compare_digest(
            fingerprint,
            expected_new_fingerprint,
        ):
            raise RuntimeError("VM-HA approved replacement SSH identity changed")

    paths = _managed_trust_paths(trust_scope, managed_root)
    with _managed_trust_lock(paths):
        receipt = _read_managed_file(
            paths.receipt,
            paths,
            "VM-HA managed SSH trust receipt",
        )
        projection = _read_managed_file(
            paths.projection,
            paths,
            "VM-HA managed SSH trust projection",
        )
        receipt_sha256 = _content_sha256(receipt)
        projection_sha256 = _content_sha256(projection)
        predecessor = (
            receipt_sha256 == intent.predecessor_receipt_sha256
            and projection_sha256 == intent.predecessor_projection_sha256
        )
        successor_or_partial = bool(
            expected_successor_receipt_sha256
            and receipt_sha256 == expected_successor_receipt_sha256
            and projection_sha256
            in {
                intent.predecessor_projection_sha256,
                expected_successor_projection_sha256,
            }
        )
        if not predecessor and not successor_or_partial:
            raise RuntimeError("VM-HA managed SSH trust changed after rotation approval")
        pins, authorities = _rotation_source_pins(
            receipt=receipt,
            projection=projection,
            scope=trust_scope,
            aliases=aliases,
        )
        if predecessor:
            old_entries = pins.get(intent.hostname, {})
            if len(old_entries) != 1 or not hmac.compare_digest(
                _public_key_fingerprint(next(iter(old_entries.values()))),
                intent.old_fingerprint,
            ):
                raise RuntimeError("VM-HA managed SSH predecessor pin changed")
            pins[intent.hostname] = {key_type: key_data}
            authorities[intent.hostname] = SSHTrustAuthority(kind="product-rotated-v1")
        else:
            new_entries = pins.get(intent.hostname, {})
            if len(new_entries) != 1 or not hmac.compare_digest(
                _public_key_fingerprint(next(iter(new_entries.values()))),
                fingerprint,
            ):
                raise RuntimeError("VM-HA managed SSH successor pin changed")
        receipt_content = _build_receipt(trust_scope, pins, authorities)
        projection_content = _canonical_known_hosts(pins, aliases)
        successor_receipt_sha256 = hashlib.sha256(receipt_content).hexdigest()
        successor_projection_sha256 = hashlib.sha256(projection_content).hexdigest()
        if expected_successor_receipt_sha256 is not None and not hmac.compare_digest(
            successor_receipt_sha256,
            expected_successor_receipt_sha256,
        ):
            raise RuntimeError("VM-HA approved SSH trust receipt changed")
        if expected_successor_projection_sha256 is not None and not hmac.compare_digest(
            successor_projection_sha256,
            expected_successor_projection_sha256,
        ):
            raise RuntimeError("VM-HA approved SSH trust projection changed")
    return VMHASSHIdentityRotationStage(
        hostname=intent.hostname,
        stage_token=stage_token,
        new_fingerprint=fingerprint,
        successor_receipt_sha256=successor_receipt_sha256,
        successor_projection_sha256=successor_projection_sha256,
        _private_key=content,
        _key_type=key_type,
        _key_data=key_data,
        _receipt_content=receipt_content,
        _projection_content=projection_content,
    )


def publish_vm_ha_ssh_identity_rotation(
    intent: VMHASSHIdentityRotationIntent,
    stage: VMHASSHIdentityRotationStage,
    *,
    operation_id: str,
    trust_scope: VMHASSHTrustScope,
    managed_root: Path | None = None,
) -> tuple[str, str]:
    """Checkpoint-publish one staged replacement identity and public trust."""

    if stage.hostname != intent.hostname:
        raise ValueError("VM-HA staged SSH identity target changed")
    stage_path, canonical_path, stage_token = _rotation_key_paths(
        trust_scope,
        intent,
        operation_id,
    )
    if stage_token != stage.stage_token:
        raise ValueError("VM-HA staged SSH identity operation changed")
    with _default_host_key_lock(canonical_path.parent):
        try:
            canonical_path.lstat()
        except FileNotFoundError:
            _write_exclusive_private_key(canonical_path, stage._private_key)
        canonical = _rotation_key_material(canonical_path, intent.hostname)
        if not hmac.compare_digest(canonical[3], stage.new_fingerprint):
            raise RuntimeError("VM-HA canonical replacement SSH identity changed")
        try:
            stage_path.lstat()
        except FileNotFoundError:
            staged = canonical
        else:
            staged = _rotation_key_material(stage_path, intent.hostname)
        if not hmac.compare_digest(staged[3], stage.new_fingerprint):
            raise RuntimeError("VM-HA staged replacement SSH identity changed")

    paths = _managed_trust_paths(trust_scope, managed_root)
    with _managed_trust_lock(paths):
        receipt = _read_managed_file(
            paths.receipt,
            paths,
            "VM-HA managed SSH trust receipt",
        )
        projection = _read_managed_file(
            paths.projection,
            paths,
            "VM-HA managed SSH trust projection",
        )
        receipt_sha256 = _content_sha256(receipt)
        projection_sha256 = _content_sha256(projection)
        if receipt_sha256 == intent.predecessor_receipt_sha256:
            _write_atomic(paths.receipt, stage._receipt_content)
        elif receipt_sha256 != stage.successor_receipt_sha256:
            raise RuntimeError("VM-HA managed SSH receipt changed during rotation")
        if projection_sha256 == intent.predecessor_projection_sha256:
            _write_atomic(paths.projection, stage._projection_content)
        elif projection_sha256 != stage.successor_projection_sha256:
            raise RuntimeError("VM-HA managed SSH projection changed during rotation")
        final_receipt = _read_managed_file(
            paths.receipt,
            paths,
            "VM-HA managed SSH trust receipt",
        )
        final_projection = _read_managed_file(
            paths.projection,
            paths,
            "VM-HA managed SSH trust projection",
        )
        if (
            _content_sha256(final_receipt) != stage.successor_receipt_sha256
            or _content_sha256(final_projection) != stage.successor_projection_sha256
        ):
            raise RuntimeError("VM-HA managed SSH trust did not verify after rotation")
    with _default_host_key_lock(canonical_path.parent):
        cleanup_staged: tuple[bytes, str, str, str] | None
        try:
            stage_path.lstat()
        except FileNotFoundError:
            cleanup_staged = None
        else:
            cleanup_staged = _rotation_key_material(stage_path, intent.hostname)
        if cleanup_staged is not None:
            if not hmac.compare_digest(cleanup_staged[3], stage.new_fingerprint):
                raise RuntimeError("VM-HA staged replacement SSH identity changed")
            stage_path.unlink()
    return stage.successor_receipt_sha256, stage.successor_projection_sha256


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
            f"{KNOWN_HOSTS_ENV} is not set; no explicit pinned known-hosts override is available"
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
    content, metadata = _regular_file_state(path, f"VM-HA SSH private host key for {hostname}")
    if metadata.st_nlink != 1 or (hasattr(os, "getuid") and metadata.st_uid != os.getuid()):
        raise ValueError(
            f"VM-HA SSH private host key for {hostname} must be an owner-only "
            "single-link regular file"
        )
    if metadata.st_mode & 0o077:
        raise ValueError(
            f"VM-HA SSH private host key for {hostname} must not be accessible by group or others"
        )
    return _validated_private_host_key_content(content, hostname)


def _validated_private_host_key_content(
    content: bytes,
    hostname: str,
) -> tuple[bytes, str, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="nebius-vpngw-host-key-") as temporary:
            snapshot = Path(temporary) / "host-key"
            snapshot.write_bytes(content)
            snapshot.chmod(0o600)
            result = subprocess.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(snapshot)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        key_type, key_data, *_ = result.stdout.strip().split()
        _validate_public_key(key_type, key_data)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            f"VM-HA SSH private host key for {hostname} is malformed, encrypted, or unusable"
        ) from error
    return content, key_type, key_data


def require_vm_ha_management_key(
    private_key_path: Path | None,
    public_key: str | None,
) -> Path:
    """Prove the fresh-member login key pair before any cloud mutation."""

    if private_key_path is None:
        raise ValueError(
            "VM-HA fresh-host enrollment requires ssh_private_key_path or VPNGW_SSH_KEY"
        )
    path = private_key_path.expanduser()
    _regular_file(path, "VM-HA management private key")
    try:
        metadata = path.lstat()
    except OSError:
        raise ValueError("VM-HA management private key is unavailable") from None
    if metadata.st_mode & 0o077:
        raise ValueError("VM-HA management private key must not be accessible by group or others")

    fields = (public_key or "").strip().split()
    if len(fields) < 2 or "\n" in (public_key or "") or "\r" in (public_key or ""):
        raise ValueError("VM-HA management public key must be one OpenSSH public-key record")
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        derived = result.stdout.strip().split()
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        raise ValueError(
            "VM-HA management private key is malformed, encrypted, or unusable"
        ) from error
    if len(derived) < 2 or fields[:2] != derived[:2]:
        raise ValueError("VM-HA management public key does not match the selected private key")
    return path


def require_vm_ha_ssh_policy(
    hosts: Iterable[str | tuple[str, str]],
    environment: Mapping[str, str] | None = None,
    *,
    enrollment_hosts: Iterable[str] | None = None,
    management_key_path: Path | None = None,
    management_public_key: str | None = None,
    require_management_key: bool = False,
    trust_scope: VMHASSHTrustScope | None = None,
    allow_managed_repair: bool = False,
    persist_default_host_keys: bool = False,
    managed_root: Path | None = None,
    additional_aliases: Mapping[str, Iterable[str]] | None = None,
    retained_hosts: Iterable[str] = (),
    allow_default_known_hosts_import: bool = False,
    default_known_hosts_bindings: Mapping[str, Callable[[], None]] | None = None,
    default_known_hosts_import_hosts: Iterable[str] | None = None,
    host_identity_recovery: (
        Callable[[frozenset[str]], Mapping[str, SSHHostKeyRecovery]] | None
    ) = None,
    allow_legacy_ordinary_enrollment: bool = False,
    legacy_host_key_enrollments: Mapping[str, SSHHostKeyEnrollment] | None = None,
    trusted_member_imports: Mapping[str, TrustedSSHMemberImport] | None = None,
    rotate_identity_hosts: Iterable[str] = (),
) -> SSHTrustPolicy:
    """Resolve exact pins and prevalidate any authoritative managed-store update."""

    source = os.environ if environment is None else environment
    host_pairs = tuple(
        (_safe_hostname(host), _safe_hostname(host))
        if isinstance(host, str)
        else (_safe_hostname(host[0]), _safe_hostname(host[1]))
        for host in hosts
    )
    if not host_pairs:
        raise ValueError("VM-HA SSH policy requires a non-empty exact target for every member")
    hostnames = [hostname for hostname, _ in host_pairs]
    if len(set(hostnames)) != len(hostnames):
        raise ValueError("VM-HA SSH policy contains duplicate member hostnames")
    extra_aliases = additional_aliases or {}
    unknown_alias_hosts = set(extra_aliases) - set(hostnames)
    if unknown_alias_hosts:
        raise ValueError("VM-HA SSH aliases contain a member outside the deployment plan")
    aliases = {
        hostname: {
            hostname,
            pin_target,
            *(_safe_hostname(alias) for alias in extra_aliases.get(hostname, ())),
        }
        for hostname, pin_target in host_pairs
    }
    alias_owners: dict[str, str] = {}
    for hostname, member_aliases in aliases.items():
        for alias in member_aliases:
            previous = alias_owners.setdefault(alias, hostname)
            if previous != hostname:
                raise ValueError("VM-HA SSH alias is assigned to more than one member")
    enrollment = (
        {hostname for hostname, _ in host_pairs}
        if enrollment_hosts is None
        else set(enrollment_hosts)
    )
    unknown_enrollment = enrollment - {hostname for hostname, _ in host_pairs}
    if unknown_enrollment:
        raise ValueError("VM-HA enrollment host is not present in the deployment plan")
    if enrollment and require_management_key:
        require_vm_ha_management_key(management_key_path, management_public_key)
    rotation = set(rotate_identity_hosts)
    if rotation - enrollment:
        raise ValueError("VM-HA SSH identity rotation target is not an enrollment host")
    retained = set(retained_hosts)
    if retained - set(hostnames):
        raise ValueError("VM-HA retained host is not present in the deployment plan")
    bindings = dict(default_known_hosts_bindings or {})
    if set(bindings) - set(hostnames):
        raise ValueError("VM-HA SSH migration binding is outside the deployment plan")
    default_import_hosts = (
        set(bindings)
        if default_known_hosts_import_hosts is None
        else set(default_known_hosts_import_hosts)
    )
    if default_import_hosts - set(bindings):
        raise ValueError("VM-HA default known-hosts import host has no Compute binding")

    if KNOWN_HOSTS_ENV in source and not str(source.get(KNOWN_HOSTS_ENV) or "").strip():
        raise ValueError(f"{KNOWN_HOSTS_ENV} is set but empty")
    explicit = explicit_known_hosts_file(source)
    paths: _ManagedTrustPaths | None = None
    existing_receipt: bytes | None = None
    existing_projection: bytes | None = None
    authorities: dict[str, SSHTrustAuthority]
    source_kind: str
    if explicit is not None:
        explicit_content = _regular_file(explicit, KNOWN_HOSTS_ENV)
        pins = _parse_exact_pins(explicit_content, aliases, label=KNOWN_HOSTS_ENV)
        authorities = {
            hostname: SSHTrustAuthority(kind="explicit-override-v1")
            for hostname in hostnames
            if pins.get(hostname)
        }
        source_kind = "explicit"
    else:
        if trust_scope is None:
            raise ValueError(
                "VM-HA SSH trust requires a deployment scope or an explicit "
                f"{KNOWN_HOSTS_ENV} override"
            )
        paths = _managed_trust_paths(trust_scope, managed_root)
        existing_receipt = _read_managed_file(
            paths.receipt,
            paths,
            "VM-HA managed SSH trust receipt",
        )
        existing_projection = _read_managed_file(
            paths.projection,
            paths,
            "VM-HA managed SSH trust projection",
        )
        if existing_receipt is not None:
            receipt_pins, receipt_authorities = _parse_receipt(
                existing_receipt,
                trust_scope,
            )
            if set(receipt_pins) != set(hostnames):
                raise ValueError(
                    "VM-HA managed SSH trust receipt member set does not match the deployment plan"
                )
            pins = {hostname: dict(receipt_pins.get(hostname, {})) for hostname in hostnames}
            authorities = {
                hostname: receipt_authorities[hostname]
                for hostname in hostnames
                if pins.get(hostname)
            }
            source_kind = "receipt"
        elif existing_projection is not None:
            pins = _parse_exact_pins(
                existing_projection,
                aliases,
                label="VM-HA managed SSH trust projection",
            )
            authorities = {
                hostname: SSHTrustAuthority(kind="managed-projection-v1")
                for hostname in hostnames
                if pins.get(hostname)
            }
            source_kind = "projection"
        else:
            pins = {hostname: {} for hostname in hostnames}
            authorities = {}
            source_kind = "missing"

    evidence_checks: list[Callable[[], None]] = []
    member_imports = dict(trusted_member_imports or {})
    if set(member_imports) - retained:
        raise ValueError("Managed SSH trust import is outside the retained member set")
    for hostname, imported in member_imports.items():
        if imported.hostname != hostname:
            raise ValueError("Managed SSH trust import member identity is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", imported.predecessor_receipt_sha256):
            raise ValueError("Managed SSH trust import predecessor is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", imported.compute_binding_sha256):
            raise ValueError("Managed SSH trust import Compute binding is invalid")
        imported_pins = dict(imported.pins)
        if set(imported_pins) != {"ssh-ed25519"}:
            raise ValueError("Managed SSH trust import requires one Ed25519 pin")
        _validate_public_key("ssh-ed25519", imported_pins["ssh-ed25519"])
        existing = pins.get(hostname, {})
        if existing and existing != imported_pins:
            raise ValueError("Managed SSH trust import conflicts with current trust")
        imported.assert_current()
        pins[hostname] = imported_pins
        authorities[hostname] = SSHTrustAuthority(
            kind="ordinary-migration-v1",
            compute_binding_sha256=imported.compute_binding_sha256,
            predecessor_receipt_sha256=imported.predecessor_receipt_sha256,
        )
        evidence_checks.append(imported.assert_current)
    if member_imports:
        source_kind = "ordinary-migration"
    missing = {hostname for hostname in hostnames if not pins.get(hostname)}
    if missing and source_kind == "explicit":
        names = ", ".join(sorted(missing))
        raise ValueError(
            "VM-HA SSH host pin is unavailable for "
            f"{names}; the explicit {KNOWN_HOSTS_ENV} override is incomplete"
        )
    if missing and explicit is None and allow_managed_repair and allow_default_known_hosts_import:
        eligible = missing & retained & default_import_hosts
        default_snapshot = _read_default_known_hosts_snapshot(Path.home() / ".ssh" / "known_hosts")
        if eligible and default_snapshot is not None:
            candidate_pins = _parse_default_known_hosts_pins(
                default_snapshot.content,
                {hostname: aliases[hostname] for hostname in eligible},
            )
            candidates = {hostname for hostname in eligible if candidate_pins.get(hostname)}
            default_imported: set[str] = set()
            for hostname in candidates:
                try:
                    bindings[hostname]()
                except (RuntimeError, ValueError):
                    continue
                pins[hostname] = candidate_pins[hostname]
                authorities[hostname] = SSHTrustAuthority(kind="default-known-hosts-v1")
                evidence_checks.append(bindings[hostname])
                default_imported.add(hostname)
            if default_imported:
                evidence_checks.insert(0, default_snapshot.assert_current)
                source_kind = "default-known-hosts"

    # A retained member's network address and Compute object are mutable cloud
    # references, not identity authority.  Recheck every supplied binding for
    # every trust source, including an existing managed receipt or an explicit
    # operator file, before publication and each later policy use.
    for hostname in sorted(retained & set(bindings)):
        binding = bindings[hostname]
        if binding not in evidence_checks:
            evidence_checks.append(binding)

    derived: dict[str, tuple[bytes, str, str]] = {}
    recovered: dict[str, SSHHostKeyRecovery] = {}
    if (
        allow_legacy_ordinary_enrollment
        and explicit is None
        and HOST_KEYS_DIR_ENV not in source
        and host_identity_recovery is not None
    ):
        recovery_candidates = frozenset(hostname for hostname in retained if not pins.get(hostname))
        if recovery_candidates:
            recovered = dict(host_identity_recovery(recovery_candidates))
            if set(recovered) - set(recovery_candidates):
                raise ValueError("VM-HA cloud recovery returned an unexpected member identity")
            for hostname, recovery in recovered.items():
                if recovery.hostname != hostname:
                    raise ValueError("VM-HA cloud recovery returned a mismatched member identity")
                material = _validated_private_host_key_content(
                    recovery.private_key,
                    hostname,
                )
                derived[hostname] = material
                pins[hostname] = {material[1]: material[2]}
                authorities[hostname] = SSHTrustAuthority(kind="product-provisioning-v1")
                evidence_checks.append(recovery.assert_current)
            if recovered:
                source_kind = "cloud"

    enrollment_evidence = dict(legacy_host_key_enrollments or {})
    if set(enrollment_evidence) - set(hostnames):
        raise ValueError("Ordinary SSH network enrollment returned an unexpected member")
    unresolved_retained = {hostname for hostname in retained if not pins.get(hostname)}
    if enrollment_evidence and not allow_legacy_ordinary_enrollment:
        raise ValueError("Ordinary SSH network enrollment was not explicitly admitted")
    if enrollment_evidence:
        if set(enrollment_evidence) != unresolved_retained:
            raise ValueError(
                "Ordinary SSH network enrollment does not match the retained member set"
            )
        for hostname, observed in enrollment_evidence.items():
            if observed.hostname != hostname or observed.key_type != "ssh-ed25519":
                raise ValueError("Ordinary SSH network enrollment identity is invalid")
            _validate_public_key(observed.key_type, observed.key_data)
            authority = _validate_ssh_trust_authority(observed.authority)
            if authority.kind != "legacy-ordinary-network-enrollment-v1":
                raise ValueError("Ordinary SSH network enrollment authority is invalid")
            observed.assert_current()
            pins[hostname] = {observed.key_type: observed.key_data}
            authorities[hostname] = authority
            evidence_checks.append(observed.assert_current)
        source_kind = "legacy-network-enrollment"
        unresolved_retained.clear()
    if unresolved_retained and allow_legacy_ordinary_enrollment:
        raise LegacyOrdinarySSHEnrollmentRequired(unresolved_retained)

    if rotation:
        if explicit is not None or HOST_KEYS_DIR_ENV in source:
            raise VMHAReplacementSSHIdentityUnavailable(
                "VM-HA automatic replacement SSH identity rotation requires product-managed "
                "trust and default host-key storage",
                problem=VMHAReplacementSSHIdentityProblem.OPERATOR_SOURCE_CONFLICT,
            )
        if source_kind not in {"receipt", "projection"}:
            raise VMHAReplacementSSHIdentityUnavailable(
                "VM-HA automatic replacement SSH identity rotation requires existing "
                "product-managed trust",
                problem=VMHAReplacementSSHIdentityProblem.MANAGED_PREDECESSOR_UNAVAILABLE,
            )
        for hostname in rotation:
            entries = pins.get(hostname, {})
            if set(entries) != {"ssh-ed25519"}:
                raise VMHAReplacementSSHIdentityUnavailable(
                    "VM-HA automatic replacement SSH identity rotation requires one exact "
                    f"managed Ed25519 pin for {hostname}",
                    problem=VMHAReplacementSSHIdentityProblem.MANAGED_PREDECESSOR_UNAVAILABLE,
                )
            pins[hostname] = {}
            authorities.pop(hostname, None)

    missing = {hostname for hostname in hostnames if not pins.get(hostname)}
    if missing and not allow_managed_repair:
        names = ", ".join(sorted(missing))
        raise ValueError(
            "VM-HA SSH host pin is unavailable for "
            f"{names}; run apply with retained host-key evidence or configure {KNOWN_HOSTS_ENV}"
        )

    fresh_hosts = enrollment - retained
    generation_hosts = fresh_hosts & missing
    private_key_hosts = enrollment | missing
    directory: Path | None = None
    directory_is_explicit = HOST_KEYS_DIR_ENV in source
    raw_directory = source.get(HOST_KEYS_DIR_ENV)
    if allow_managed_repair and directory_is_explicit and not str(raw_directory or "").strip():
        raise ValueError(f"{HOST_KEYS_DIR_ENV} is set but empty")
    if (
        allow_managed_repair
        and not directory_is_explicit
        and (private_key_hosts or (recovered and persist_default_host_keys))
        and trust_scope is not None
    ):
        raw_directory = str(_default_vm_ha_ssh_host_keys_directory(trust_scope))
    if private_key_hosts or (allow_managed_repair and directory_is_explicit and raw_directory):
        if not raw_directory:
            purpose = "fresh-host enrollment" if enrollment else "managed trust repair"
            raise ValueError(f"{HOST_KEYS_DIR_ENV} is required for VM-HA {purpose}")
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute():
            source_label = HOST_KEYS_DIR_ENV if directory_is_explicit else "default host-key path"
            raise ValueError(f"{source_label} must name an absolute directory")
        if directory_is_explicit:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError(f"{HOST_KEYS_DIR_ENV} must name an absolute non-symlink directory")
        else:
            if persist_default_host_keys:
                _prepare_persistent_default_host_keys(directory, generation_hosts)
            else:
                if directory.exists():
                    _validate_default_host_key_directories(directory)
            if not persist_default_host_keys:
                for hostname in sorted(generation_hosts):
                    private_key = directory / f"{hostname}.key"
                    try:
                        private_key.lstat()
                    except FileNotFoundError:
                        derived[hostname] = _generate_ed25519_host_key(hostname)
                    except OSError as error:
                        raise ValueError(
                            f"VM-HA SSH private host key for fresh member {hostname} is unavailable"
                        ) from error
        if directory.is_dir():
            for hostname in hostnames:
                private_key = directory / f"{hostname}.key"
                try:
                    private_key.lstat()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ValueError(
                        f"VM-HA SSH private host key for {hostname} is unavailable"
                    ) from error
                derived[hostname] = _validated_private_host_key(private_key, hostname)

    recovery_needed = frozenset(
        hostname
        for hostname in hostnames
        if (
            (not pins.get(hostname) and hostname not in derived)
            or (hostname in enrollment and hostname not in derived)
        )
    )
    if recovery_needed and host_identity_recovery is not None and not directory_is_explicit:
        additionally_recovered = dict(host_identity_recovery(recovery_needed))
        if set(additionally_recovered) - set(recovery_needed):
            raise ValueError("VM-HA cloud recovery returned an unexpected member identity")
        for hostname, recovery in additionally_recovered.items():
            if recovery.hostname != hostname:
                raise ValueError("VM-HA cloud recovery returned a mismatched member identity")
            derived[hostname] = _validated_private_host_key_content(
                recovery.private_key,
                hostname,
            )
            authorities[hostname] = SSHTrustAuthority(kind="product-provisioning-v1")
            evidence_checks.append(recovery.assert_current)
        recovered.update(additionally_recovered)
        if additionally_recovered:
            source_kind = "cloud"

    identities: list[SSHHostIdentity] = []
    for hostname, pin_target in host_pairs:
        entries = pins.setdefault(hostname, {})
        if not entries:
            if hostname not in derived:
                if hostname in fresh_hosts:
                    raise ValueError(
                        f"VM-HA SSH private host key for fresh member {hostname} is unavailable"
                    )
                raise ValueError(
                    "VM-HA original SSH private host key for retained member "
                    f"{hostname} is unavailable; no authoritative local, default-known-hosts, "
                    "or product "
                    "provisioning evidence remains"
                )
            _, key_type, key_data = derived[hostname]
            entries[key_type] = key_data
            authorities.setdefault(
                hostname,
                SSHTrustAuthority(
                    kind=(
                        "product-generated-v1"
                        if hostname in fresh_hosts
                        else "product-provisioning-v1"
                    )
                ),
            )
        if hostname in private_key_hosts and hostname not in derived:
            if hostname in enrollment:
                rotation_intent = None
                if (
                    explicit is None
                    and not directory_is_explicit
                    and trust_scope is not None
                    and source_kind in {"receipt", "projection"}
                    and set(entries) == {"ssh-ed25519"}
                ):
                    rotation_intent = VMHASSHIdentityRotationIntent(
                        hostname=hostname,
                        trust_scope_sha256=trust_scope.digest,
                        old_fingerprint=_public_key_fingerprint(entries["ssh-ed25519"]),
                        predecessor_receipt_sha256=_content_sha256(existing_receipt),
                        predecessor_projection_sha256=_content_sha256(existing_projection),
                    )
                raise VMHAReplacementSSHIdentityUnavailable(
                    "VM-HA original SSH private host key for replacement or recreation member "
                    f"{hostname} is unavailable; exact replacement approval is required",
                    rotation_intent=rotation_intent,
                )
            raise ValueError(
                f"VM-HA original SSH private host key for retained member {hostname} is unavailable"
            )
        if hostname in derived:
            content, key_type, key_data = derived[hostname]
            pinned = entries.get(key_type)
            if pinned is None or not hmac.compare_digest(pinned, key_data):
                raise ValueError(
                    f"VM-HA SSH private host key for {hostname} does not match its exact pin for {pin_target}"
                )
            if hostname in enrollment:
                identities.append(SSHHostIdentity(hostname=hostname, private_key=content))

    selected_pins = {hostname: pins[hostname] for hostname in hostnames}
    known_hosts_content = _canonical_known_hosts(selected_pins, aliases)
    snapshot_owner = tempfile.TemporaryDirectory(prefix="nebius-vpngw-known-hosts-")
    snapshot = Path(snapshot_owner.name) / "known_hosts"
    snapshot.write_bytes(known_hosts_content)
    snapshot.chmod(0o400)

    managed_update: _ManagedTrustUpdate | None = None
    recovered_update: _RecoveredHostKeysUpdate | None = None
    managed_action: str | None = None
    managed_receipt_sha256: str | None = None
    if recovered and persist_default_host_keys:
        assert directory is not None and not directory_is_explicit
        recovered_update = _RecoveredHostKeysUpdate(
            directory=directory,
            identities=tuple(recovered[hostname] for hostname in sorted(recovered)),
        )
    if trust_scope is not None and allow_managed_repair:
        if paths is None:
            paths = _managed_trust_paths(trust_scope, managed_root)
            existing_receipt = _read_managed_file(
                paths.receipt,
                paths,
                "VM-HA managed SSH trust receipt",
            )
            existing_projection = _read_managed_file(
                paths.projection,
                paths,
                "VM-HA managed SSH trust projection",
            )
        desired_receipt = _build_receipt(trust_scope, selected_pins, authorities)
        managed_receipt_sha256 = hashlib.sha256(desired_receipt).hexdigest()
        desired_projection = known_hosts_content
        if existing_receipt != desired_receipt or existing_projection != desired_projection:
            managed_action = (
                "migrate"
                if source_kind
                in {
                    "explicit",
                    "default-known-hosts",
                    "cloud",
                    "ordinary-migration",
                }
                else "create"
                if existing_receipt is None and existing_projection is None
                else "repair"
            )
            if source_kind == "legacy-network-enrollment":
                managed_action = "enroll"
            managed_update = _ManagedTrustUpdate(
                paths=paths,
                receipt_content=desired_receipt,
                projection_content=desired_projection,
                expected_receipt_sha256=_content_sha256(existing_receipt),
                expected_projection_sha256=_content_sha256(existing_projection),
            )
    if rotation:
        managed_action = "rotate"
        managed_update = None
        recovered_update = None
    if recovered_update is not None and managed_action is None:
        managed_action = "repair"
    return SSHTrustPolicy(
        known_hosts_file=snapshot,
        known_hosts_sha256=hashlib.sha256(known_hosts_content).hexdigest(),
        pin_targets=tuple((hostname, hostname) for hostname in hostnames),
        transport_targets=host_pairs,
        identities=tuple(identities),
        managed_action=managed_action,
        managed_receipt_sha256=managed_receipt_sha256,
        _snapshot_owner=snapshot_owner,
        _managed_update=managed_update,
        _recovered_host_keys_update=recovered_update,
        _evidence_checks=tuple(evidence_checks),
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
    client_auth: SSHClientAuth | None = None,
    connect_timeout: int = 10,
    policy: SSHTrustPolicy | None = None,
    hostname: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    if connect_timeout <= 0:
        raise ValueError("SSH connect timeout must be positive")
    if key_path is not None and client_auth is not None:
        raise ValueError("SSH command cannot combine legacy and exact client identities")
    command = ["ssh"]
    if client_auth is not None:
        command.extend(client_auth.openssh_options())
    elif key_path is not None:
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
