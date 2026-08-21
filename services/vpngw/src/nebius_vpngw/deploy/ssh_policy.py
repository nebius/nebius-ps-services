from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import importlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_HOSTS_ENV = "VPNGW_SSH_KNOWN_HOSTS_FILE"
HOST_KEYS_DIR_ENV = "VPNGW_SSH_HOST_KEYS_DIR"
VM_HA_SSH_HOST_KEY_PATH = "/etc/ssh/vpngw_host_key"
VM_HA_SSH_TRUST_SCHEMA = "nebius-vpngw/vm-ha-ssh-trust-v1"
VM_HA_SSH_TRUST_DIRECTORY = "nebius-vpngw"
VM_HA_SSH_TRUST_RECEIPT = "trust.json"
VM_HA_SSH_TRUST_PROJECTION = "known_hosts"


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


_TRUST_SCOPE_FIELDS = (
    "tenant_id",
    "project_id",
    "region_id",
    "gateway_name",
    "cluster_id",
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
        members.append({"hostname": _safe_hostname(hostname), "keys": keys})
    return _canonical_json(
        {
            "members": members,
            "schema": VM_HA_SSH_TRUST_SCHEMA,
            "scope": scope.as_dict(),
            "scope_sha256": scope.digest,
        }
    )


def _parse_receipt(content: bytes, scope: VMHASSHTrustScope) -> dict[str, dict[str, str]]:
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
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("VM-HA managed SSH trust receipt contains an invalid member")
        hostname = _safe_hostname(str(row.get("hostname") or ""))
        if hostname in pins or not isinstance(row.get("keys"), list) or not row["keys"]:
            raise ValueError("VM-HA managed SSH trust receipt contains an invalid member")
        host_pins: dict[str, str] = {}
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
    return pins


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
    _snapshot_owner: Any = field(default=None, repr=False, compare=False)
    _managed_update: _ManagedTrustUpdate | None = field(default=None, repr=False, compare=False)

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
    if update is None:
        return False
    policy.assert_current()
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
    if metadata.st_mode & 0o077:
        raise ValueError(
            f"VM-HA SSH private host key for {hostname} must not be accessible by group or others"
        )
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
    managed_root: Path | None = None,
    additional_aliases: Mapping[str, Iterable[str]] | None = None,
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

    if KNOWN_HOSTS_ENV in source and not str(source.get(KNOWN_HOSTS_ENV) or "").strip():
        raise ValueError(f"{KNOWN_HOSTS_ENV} is set but empty")
    explicit = explicit_known_hosts_file(source)
    paths: _ManagedTrustPaths | None = None
    existing_receipt: bytes | None = None
    existing_projection: bytes | None = None
    source_kind: str
    if explicit is not None:
        explicit_content = _regular_file(explicit, KNOWN_HOSTS_ENV)
        pins = _parse_exact_pins(explicit_content, aliases, label=KNOWN_HOSTS_ENV)
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
            receipt_pins = _parse_receipt(existing_receipt, trust_scope)
            pins = {hostname: dict(receipt_pins.get(hostname, {})) for hostname in hostnames}
            source_kind = "receipt"
        elif existing_projection is not None:
            pins = _parse_exact_pins(
                existing_projection,
                aliases,
                label="VM-HA managed SSH trust projection",
            )
            source_kind = "projection"
        else:
            pins = {hostname: {} for hostname in hostnames}
            source_kind = "missing"

    missing = {hostname for hostname in hostnames if not pins.get(hostname)}
    if missing and (source_kind == "explicit" or not allow_managed_repair):
        names = ", ".join(sorted(missing))
        raise ValueError(
            "VM-HA SSH host pin is unavailable for "
            f"{names}; run apply with retained host-key evidence or configure {KNOWN_HOSTS_ENV}"
        )

    private_key_hosts = enrollment | missing
    directory: Path | None = None
    raw_directory = source.get(HOST_KEYS_DIR_ENV)
    if (
        allow_managed_repair
        and HOST_KEYS_DIR_ENV in source
        and not str(raw_directory or "").strip()
    ):
        raise ValueError(f"{HOST_KEYS_DIR_ENV} is set but empty")
    if private_key_hosts or (allow_managed_repair and raw_directory):
        if not raw_directory:
            purpose = "fresh-host enrollment" if enrollment else "managed trust repair"
            raise ValueError(f"{HOST_KEYS_DIR_ENV} is required for VM-HA {purpose}")
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{HOST_KEYS_DIR_ENV} must name an absolute non-symlink directory")
        if allow_managed_repair:
            for hostname in hostnames:
                try:
                    (directory / f"{hostname}.key").lstat()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ValueError(
                        f"VM-HA SSH private host key for {hostname} is unavailable"
                    ) from error
                private_key_hosts.add(hostname)

    identities: list[SSHHostIdentity] = []
    derived: dict[str, tuple[bytes, str, str]] = {}
    for hostname, pin_target in host_pairs:
        entries = pins.setdefault(hostname, {})
        if hostname in private_key_hosts:
            assert directory is not None
            derived[hostname] = _validated_private_host_key(directory / f"{hostname}.key", hostname)
        if not entries:
            _, key_type, key_data = derived[hostname]
            entries[key_type] = key_data
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
    managed_action: str | None = None
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
        desired_receipt = _build_receipt(trust_scope, selected_pins)
        desired_projection = known_hosts_content
        if existing_receipt != desired_receipt or existing_projection != desired_projection:
            managed_action = (
                "migrate"
                if source_kind == "explicit"
                else "create"
                if existing_receipt is None and existing_projection is None
                else "repair"
            )
            managed_update = _ManagedTrustUpdate(
                paths=paths,
                receipt_content=desired_receipt,
                projection_content=desired_projection,
                expected_receipt_sha256=_content_sha256(existing_receipt),
                expected_projection_sha256=_content_sha256(existing_projection),
            )
    return SSHTrustPolicy(
        known_hosts_file=snapshot,
        known_hosts_sha256=hashlib.sha256(known_hosts_content).hexdigest(),
        pin_targets=tuple((hostname, hostname) for hostname in hostnames),
        transport_targets=host_pairs,
        identities=tuple(identities),
        managed_action=managed_action,
        _snapshot_owner=snapshot_owner,
        _managed_update=managed_update,
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
