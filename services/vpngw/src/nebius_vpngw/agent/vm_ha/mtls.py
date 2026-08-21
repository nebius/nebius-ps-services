"""VM-local, direct-pinned mutual-TLS identity lifecycle for VM-HA."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from nebius_vpngw.vm_ha_tls import (
    generate_vm_ha_managed_identity,
    validate_vm_ha_managed_certificate,
)

from .models import canonical_json

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_ACTIVE_SCHEMA = "nebius-vpngw/vm-ha-mtls-active-v1"
_TRANSACTION_SCHEMA = "nebius-vpngw/vm-ha-mtls-transaction-v1"
_RECEIPT_SCHEMA = "nebius-vpngw/vm-ha-mtls-receipt-v1"
_REQUIRED_ROTATION_OBSERVATIONS = 3

FaultHook = Callable[[str, Path], None]


class ManagedMTLSError(ValueError):
    """A secret-safe managed-mTLS state or transition error."""


class MTLSTransactionPhase(str, Enum):
    PREPARED = "prepared"
    PEER_STAGED = "peer-staged"
    TRUST_EXPANDED = "trust-expanded"
    LOCAL_ACTIVE = "local-active"
    VERIFIED = "verified"
    COMMITTED = "committed"
    PRUNED = "pruned"
    ROLLED_BACK = "rolled-back"


class MTLSOperationKind(str, Enum):
    BOOTSTRAP = "bootstrap"
    REPLACEMENT = "replacement"
    RECOVERY = "recovery"
    ROTATION = "rotation"


_PHASE_RANK = {
    MTLSTransactionPhase.PREPARED.value: 0,
    MTLSTransactionPhase.PEER_STAGED.value: 1,
    MTLSTransactionPhase.TRUST_EXPANDED.value: 2,
    MTLSTransactionPhase.LOCAL_ACTIVE.value: 3,
    MTLSTransactionPhase.VERIFIED.value: 4,
    MTLSTransactionPhase.COMMITTED.value: 5,
    MTLSTransactionPhase.PRUNED.value: 6,
    MTLSTransactionPhase.ROLLED_BACK.value: 6,
}


def _phase_at_least(value: Mapping[str, Any], phase: MTLSTransactionPhase) -> bool:
    current = str(value.get("phase"))
    if current not in _PHASE_RANK:
        raise ManagedMTLSError("managed mTLS transaction phase is invalid")
    if current == MTLSTransactionPhase.ROLLED_BACK.value:
        raise ManagedMTLSError("managed mTLS transaction was rolled back")
    return _PHASE_RANK[current] >= _PHASE_RANK[phase.value]


def _require_identifier(name: str, value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ManagedMTLSError(f"managed mTLS {name} is invalid")
    return value


def _require_sha256(name: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ManagedMTLSError(f"managed mTLS {name} is invalid")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_read(path: Path, *, expected_mode: int = 0o600) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ManagedMTLSError("managed mTLS state is unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise ManagedMTLSError("managed mTLS state permissions are invalid")
        payload = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            payload.extend(chunk)
        identity = path.lstat()
        if identity.st_dev != metadata.st_dev or identity.st_ino != metadata.st_ino:
            raise ManagedMTLSError("managed mTLS state changed during validation")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _write_once(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        if _safe_read(path) != payload:
            raise ManagedMTLSError("managed mTLS immutable object conflicts") from None
        return
    except OSError:
        raise ManagedMTLSError("managed mTLS immutable object cannot be created") from None
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ManagedMTLSError("managed mTLS immutable object write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (canonical_json(payload) + "\n").encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(_safe_read(path).decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise ManagedMTLSError("managed mTLS state is corrupt") from None
    if not isinstance(value, Mapping):
        raise ManagedMTLSError("managed mTLS state is corrupt")
    return value


@dataclass(frozen=True)
class MTLSReceipt:
    """Public-only identity receipt returned over exact-pinned SSH."""

    operation_id: str
    operation_kind: MTLSOperationKind
    cluster_id: str
    node_id: str
    compute_id: str
    epoch: int
    certificate_fingerprint: str
    spki_fingerprint: str
    certificate_pem: bytes

    def to_dict(self, *, include_certificate: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "schema": _RECEIPT_SCHEMA,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind.value,
            "cluster_id": self.cluster_id,
            "node_id": self.node_id,
            "compute_id": self.compute_id,
            "epoch": self.epoch,
            "certificate_fingerprint": self.certificate_fingerprint,
            "spki_fingerprint": self.spki_fingerprint,
        }
        if include_certificate:
            value["certificate_pem_base64"] = base64.b64encode(self.certificate_pem).decode("ascii")
        return value

    @classmethod
    def from_mapping(cls, value: object) -> MTLSReceipt:
        expected = {
            "schema",
            "operation_id",
            "operation_kind",
            "cluster_id",
            "node_id",
            "compute_id",
            "epoch",
            "certificate_fingerprint",
            "spki_fingerprint",
            "certificate_pem_base64",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ManagedMTLSError("managed mTLS public receipt is invalid")
        if value.get("schema") != _RECEIPT_SCHEMA:
            raise ManagedMTLSError("managed mTLS public receipt is invalid")
        epoch = value.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ManagedMTLSError("managed mTLS public receipt epoch is invalid")
        try:
            certificate_pem = base64.b64decode(str(value["certificate_pem_base64"]), validate=True)
            operation_kind = MTLSOperationKind(str(value["operation_kind"]))
        except (ValueError, TypeError):
            raise ManagedMTLSError("managed mTLS public receipt is invalid") from None
        node_id = _require_identifier("receipt node identity", str(value["node_id"]))
        certificate = validate_vm_ha_managed_certificate(node_id, certificate_pem)
        certificate_fingerprint = _require_sha256(
            "receipt certificate fingerprint", str(value["certificate_fingerprint"])
        )
        spki_fingerprint = _require_sha256(
            "receipt SPKI fingerprint", str(value["spki_fingerprint"])
        )
        if (
            certificate.certificate_fingerprint != certificate_fingerprint
            or certificate.spki_fingerprint != spki_fingerprint
        ):
            raise ManagedMTLSError("managed mTLS public receipt digest mismatch")
        return cls(
            operation_id=_require_sha256("operation identity", str(value["operation_id"])),
            operation_kind=operation_kind,
            cluster_id=_require_identifier("receipt cluster identity", str(value["cluster_id"])),
            node_id=node_id,
            compute_id=_require_identifier("receipt Compute identity", str(value["compute_id"])),
            epoch=epoch,
            certificate_fingerprint=certificate_fingerprint,
            spki_fingerprint=spki_fingerprint,
            certificate_pem=certificate_pem,
        )


@dataclass(frozen=True)
class PeerLeaf:
    node_id: str
    compute_id: str
    epoch: int
    certificate_fingerprint: str
    spki_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "compute_id": self.compute_id,
            "epoch": self.epoch,
            "certificate_fingerprint": self.certificate_fingerprint,
            "spki_fingerprint": self.spki_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: object) -> PeerLeaf:
        if not isinstance(value, Mapping) or set(value) != {
            "node_id",
            "compute_id",
            "epoch",
            "certificate_fingerprint",
            "spki_fingerprint",
        }:
            raise ManagedMTLSError("managed mTLS peer identity is corrupt")
        epoch = value["epoch"]
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ManagedMTLSError("managed mTLS peer epoch is invalid")
        return cls(
            node_id=_require_identifier("peer node identity", str(value["node_id"])),
            compute_id=_require_identifier("peer Compute identity", str(value["compute_id"])),
            epoch=epoch,
            certificate_fingerprint=_require_sha256(
                "peer certificate fingerprint", str(value["certificate_fingerprint"])
            ),
            spki_fingerprint=_require_sha256(
                "peer SPKI fingerprint", str(value["spki_fingerprint"])
            ),
        )


@dataclass(frozen=True)
class MTLSSnapshot:
    cluster_id: str
    node_id: str
    compute_id: str
    epoch: int
    certificate_fingerprint: str
    spki_fingerprint: str
    certificate_path: Path
    private_key_path: Path
    peers: tuple[PeerLeaf, ...]
    peer_certificate_paths: tuple[Path, ...]
    peer_certificate_pems: tuple[bytes, ...]

    @property
    def peer_epochs_by_fingerprint(self) -> dict[str, int]:
        return {peer.certificate_fingerprint: peer.epoch for peer in self.peers}


@dataclass(frozen=True)
class MTLSStatus:
    state: str
    cluster_id: str | None
    node_id: str | None
    compute_id: str | None
    epoch: int | None
    certificate_fingerprint: str | None
    spki_fingerprint: str | None
    peer_fingerprints: tuple[str, ...]
    operation_id: str | None
    operation_kind: str | None
    target_epoch: int | None
    peer_target_epoch: int | None
    preserve_local: bool | None
    inhibited: bool
    inhibition_operation_id: str | None
    phase: str | None
    recovery: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "cluster_id": self.cluster_id,
            "node_id": self.node_id,
            "compute_id": self.compute_id,
            "epoch": self.epoch,
            "certificate_fingerprint": self.certificate_fingerprint,
            "spki_fingerprint": self.spki_fingerprint,
            "peer_fingerprints": list(self.peer_fingerprints),
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
            "target_epoch": self.target_epoch,
            "peer_target_epoch": self.peer_target_epoch,
            "preserve_local": self.preserve_local,
            "inhibited": self.inhibited,
            "inhibition_operation_id": self.inhibition_operation_id,
            "phase": self.phase,
            "recovery": self.recovery,
        }


class ManagedMTLSStore:
    """Crash-safe VM-local identity, direct-pin, and transaction authority."""

    def __init__(
        self,
        root: Path,
        *,
        fault_hook: FaultHook | None = None,
        create: bool = True,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("managed mTLS state root must be absolute")
        self.root = root
        self.identities = root / "identities"
        self.peers = root / "peers"
        self.transactions = root / "transactions"
        self.active_path = root / "active.json"
        self.inhibition_path = root / "inhibition.json"
        self.lock_path = root / "writer.lock"
        self.fault_hook = fault_hook
        if create:
            self._ensure_layout()

    def _ensure_layout(self) -> None:
        for path in (self.root, self.identities, self.peers, self.transactions):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                metadata = path.lstat()
            except OSError:
                raise ManagedMTLSError("managed mTLS state directory is unavailable") from None
            if (
                path.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise ManagedMTLSError("managed mTLS state directory permissions are invalid")
        if not self.lock_path.exists():
            _write_once(self.lock_path, b"")

    def _fault(self, label: str, path: Path) -> None:
        if self.fault_hook is not None:
            self.fault_hook(label, path)

    def acquire_writer_lock(self, *, blocking: bool = True) -> int | None:
        """Acquire the mTLS writer lock for a cross-component critical section."""

        try:
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        except OSError as error:
            raise ManagedMTLSError("managed mTLS writer lock is unavailable") from error
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError:
            os.close(descriptor)
            return None
        except OSError as error:
            os.close(descriptor)
            raise ManagedMTLSError("managed mTLS writer lock is unavailable") from error
        return descriptor

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = self.acquire_writer_lock()
        if descriptor is None:  # pragma: no cover - blocking acquisition cannot return None
            raise ManagedMTLSError("managed mTLS writer lock is unavailable")
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _transaction_path(self, operation_id: str) -> Path:
        return self.transactions / f"{_require_sha256('operation identity', operation_id)}.json"

    def _identity_paths(
        self, fingerprint: str, *, create: bool = False
    ) -> tuple[Path, Path]:
        identity_root = self.identities / _require_sha256("certificate fingerprint", fingerprint)
        if create:
            identity_root.mkdir(mode=0o700, exist_ok=True)
        try:
            descriptor = os.open(
                identity_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
            )
        except OSError:
            raise ManagedMTLSError("managed mTLS identity directory is unavailable") from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise ManagedMTLSError(
                    "managed mTLS identity directory permissions are invalid"
                )
        finally:
            os.close(descriptor)
        return identity_root / "certificate.pem", identity_root / "private-key.pem"

    def _peer_path(self, fingerprint: str) -> Path:
        return self.peers / f"{_require_sha256('peer certificate fingerprint', fingerprint)}.pem"

    def _load_active(self) -> Mapping[str, Any] | None:
        if not self.active_path.exists():
            return None
        value = _read_json(self.active_path)
        if (
            set(value)
            != {
                "schema",
                "cluster_id",
                "node_id",
                "compute_id",
                "epoch",
                "certificate_fingerprint",
                "spki_fingerprint",
                "peers",
                "operation_id",
            }
            or value.get("schema") != _ACTIVE_SCHEMA
        ):
            raise ManagedMTLSError("managed mTLS active state is corrupt")
        return value

    def _load_transaction(self, operation_id: str) -> Mapping[str, Any] | None:
        path = self._transaction_path(operation_id)
        if not path.exists():
            return None
        value = _read_json(path)
        if value.get("schema") != _TRANSACTION_SCHEMA or value.get("operation_id") != operation_id:
            raise ManagedMTLSError("managed mTLS transaction is corrupt")
        return value

    def inhibition_operation_id(
        self,
        *,
        cluster_id: str | None = None,
        node_id: str | None = None,
        generation_id: str | None = None,
    ) -> str | None:
        if not self.inhibition_path.exists():
            return None
        value = _read_json(self.inhibition_path)
        if not (
            set(value)
            == {"schema", "operation_id", "cluster_id", "node_id", "generation_id"}
            and value.get("schema") == "nebius-vpngw/vm-ha-mtls-inhibition-v1"
            and _SHA256_RE.fullmatch(str(value.get("operation_id")))
            and _SHA256_RE.fullmatch(str(value.get("generation_id")))
            and isinstance(value.get("cluster_id"), str)
            and bool(value["cluster_id"])
            and isinstance(value.get("node_id"), str)
            and bool(value["node_id"])
        ):
            raise ManagedMTLSError("managed mTLS inhibition is invalid")
        if (
            (cluster_id is not None and value["cluster_id"] != cluster_id)
            or (node_id is not None and value["node_id"] != node_id)
            or (generation_id is not None and value["generation_id"] != generation_id)
        ):
            raise ManagedMTLSError("managed mTLS inhibition belongs to another runtime")
        return str(value["operation_id"])

    def install_inhibition(
        self,
        *,
        operation_id: str,
        cluster_id: str,
        node_id: str,
        generation_id: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "nebius-vpngw/vm-ha-mtls-inhibition-v1",
            "operation_id": _require_sha256("operation identity", operation_id),
            "cluster_id": _require_identifier("cluster identity", cluster_id),
            "node_id": _require_identifier("node identity", node_id),
            "generation_id": _require_sha256("generation identity", generation_id),
        }
        with self._locked():
            if self.inhibition_path.exists():
                existing = _read_json(self.inhibition_path)
                if existing != payload:
                    raise ManagedMTLSError("managed mTLS inhibition conflicts")
                return payload
            _atomic_write_json(self.inhibition_path, payload)
            self._fault("inhibition-installed", self.inhibition_path)
        return payload

    def release_inhibition(self, operation_id: str) -> dict[str, object]:
        operation_id = _require_sha256("operation identity", operation_id)
        with self._locked():
            current = self.inhibition_operation_id()
            if current is None:
                return {"released": True, "operation_id": operation_id}
            if current != operation_id:
                raise ManagedMTLSError("managed mTLS inhibition belongs to another operation")
            self.inhibition_path.unlink()
            _fsync_directory(self.root)
            self._fault("inhibition-released", self.inhibition_path)
        return {"released": True, "operation_id": operation_id}

    def _write_transaction(self, value: Mapping[str, Any], label: str) -> None:
        path = self._transaction_path(str(value["operation_id"]))
        _atomic_write_json(path, value)
        self._fault(label, path)

    @staticmethod
    def _transaction_receipt(value: Mapping[str, Any], certificate_pem: bytes) -> MTLSReceipt:
        pending = value["pending_local"]
        if not isinstance(pending, Mapping):
            raise ManagedMTLSError("managed mTLS transaction is corrupt")
        return MTLSReceipt(
            operation_id=str(value["operation_id"]),
            operation_kind=MTLSOperationKind(str(value["operation_kind"])),
            cluster_id=str(value["cluster_id"]),
            node_id=str(value["node_id"]),
            compute_id=str(value["compute_id"]),
            epoch=int(value["target_epoch"]),
            certificate_fingerprint=str(pending["certificate_fingerprint"]),
            spki_fingerprint=str(pending["spki_fingerprint"]),
            certificate_pem=certificate_pem,
        )

    def prepare_identity(
        self,
        *,
        operation_id: str,
        operation_kind: MTLSOperationKind,
        cluster_id: str,
        node_id: str,
        compute_id: str,
        target_epoch: int,
        peer_epoch: int | None = None,
    ) -> MTLSReceipt:
        _require_identifier("cluster identity", cluster_id)
        _require_identifier("node identity", node_id)
        _require_identifier("Compute identity", compute_id)
        if not isinstance(target_epoch, int) or isinstance(target_epoch, bool) or target_epoch < 1:
            raise ManagedMTLSError("managed mTLS target epoch is invalid")
        expected_peer_epoch = target_epoch if peer_epoch is None else peer_epoch
        if (
            not isinstance(expected_peer_epoch, int)
            or isinstance(expected_peer_epoch, bool)
            or expected_peer_epoch < 1
        ):
            raise ManagedMTLSError("managed mTLS peer epoch is invalid")
        with self._locked():
            existing = self._load_transaction(operation_id)
            if existing is not None:
                expected = (
                    str(existing.get("operation_kind")) == operation_kind.value
                    and str(existing.get("cluster_id")) == cluster_id
                    and str(existing.get("node_id")) == node_id
                    and str(existing.get("compute_id")) == compute_id
                    and existing.get("target_epoch") == target_epoch
                    and existing.get("peer_target_epoch") == expected_peer_epoch
                    and existing.get("preserve_local") is False
                )
                if not expected:
                    raise ManagedMTLSError("managed mTLS operation identity conflicts")
                pending = existing.get("pending_local")
                if not isinstance(pending, Mapping):
                    raise ManagedMTLSError("managed mTLS transaction is corrupt")
                certificate_path, _key_path = self._identity_paths(
                    str(pending["certificate_fingerprint"])
                )
                certificate_pem = _safe_read(certificate_path)
                return self._transaction_receipt(existing, certificate_pem)

            active = self._load_active()
            if active is not None and target_epoch <= int(active["epoch"]):
                raise ManagedMTLSError("managed mTLS target epoch does not advance")
            identity = generate_vm_ha_managed_identity(node_id)
            validated = validate_vm_ha_managed_certificate(
                node_id,
                identity.certificate_pem,
                private_key_pem=identity.private_key_pem,
            )
            certificate_path, key_path = self._identity_paths(
                validated.certificate_fingerprint, create=True
            )
            _write_once(key_path, identity.private_key_pem)
            self._fault("private-key-written", key_path)
            _write_once(certificate_path, identity.certificate_pem)
            self._fault("certificate-written", certificate_path)
            old_local: dict[str, object] | None = None
            old_peers: list[object] = []
            if active is not None:
                old_local = {
                    "epoch": active["epoch"],
                    "certificate_fingerprint": active["certificate_fingerprint"],
                    "spki_fingerprint": active["spki_fingerprint"],
                }
                old_peers = list(active["peers"])
            transaction: dict[str, object] = {
                "schema": _TRANSACTION_SCHEMA,
                "operation_id": operation_id,
                "operation_kind": operation_kind.value,
                "cluster_id": cluster_id,
                "node_id": node_id,
                "compute_id": compute_id,
                "target_epoch": target_epoch,
                "peer_target_epoch": expected_peer_epoch,
                "preserve_local": False,
                "phase": MTLSTransactionPhase.PREPARED.value,
                "old_local": old_local,
                "old_peers": old_peers,
                "pending_local": {
                    "certificate_fingerprint": validated.certificate_fingerprint,
                    "spki_fingerprint": validated.spki_fingerprint,
                },
                "pending_peer": None,
                "new_identity_observed": False,
                "observation_ids": [],
            }
            self._write_transaction(transaction, "prepared")
            return self._transaction_receipt(transaction, identity.certificate_pem)

    def prepare_peer_replacement(
        self,
        *,
        operation_id: str,
        cluster_id: str,
        node_id: str,
        compute_id: str,
        target_peer_epoch: int,
    ) -> MTLSReceipt:
        """Prepare the survivor to trust a replacement without rotating locally."""

        _require_identifier("cluster identity", cluster_id)
        _require_identifier("node identity", node_id)
        _require_identifier("Compute identity", compute_id)
        if (
            not isinstance(target_peer_epoch, int)
            or isinstance(target_peer_epoch, bool)
            or target_peer_epoch < 1
        ):
            raise ManagedMTLSError("managed mTLS peer epoch is invalid")
        with self._locked():
            active = self._load_active()
            if active is None:
                raise ManagedMTLSError("managed mTLS survivor identity is unavailable")
            if (
                active.get("cluster_id") != cluster_id
                or active.get("node_id") != node_id
                or active.get("compute_id") != compute_id
            ):
                raise ManagedMTLSError("managed mTLS survivor identity is outside the operation")
            existing = self._load_transaction(operation_id)
            if existing is not None:
                if not (
                    existing.get("operation_kind") == MTLSOperationKind.REPLACEMENT.value
                    and existing.get("cluster_id") == cluster_id
                    and existing.get("node_id") == node_id
                    and existing.get("compute_id") == compute_id
                    and existing.get("peer_target_epoch") == target_peer_epoch
                    and existing.get("preserve_local") is True
                ):
                    raise ManagedMTLSError("managed mTLS operation identity conflicts")
                certificate_path, _key_path = self._identity_paths(
                    str(active["certificate_fingerprint"])
                )
                return self._transaction_receipt(existing, _safe_read(certificate_path))
            certificate_path, _key_path = self._identity_paths(
                str(active["certificate_fingerprint"])
            )
            transaction: dict[str, object] = {
                "schema": _TRANSACTION_SCHEMA,
                "operation_id": operation_id,
                "operation_kind": MTLSOperationKind.REPLACEMENT.value,
                "cluster_id": cluster_id,
                "node_id": node_id,
                "compute_id": compute_id,
                "target_epoch": active["epoch"],
                "peer_target_epoch": target_peer_epoch,
                "preserve_local": True,
                "phase": MTLSTransactionPhase.PREPARED.value,
                "old_local": {
                    "epoch": active["epoch"],
                    "certificate_fingerprint": active["certificate_fingerprint"],
                    "spki_fingerprint": active["spki_fingerprint"],
                },
                "old_peers": list(active["peers"]),
                "pending_local": {
                    "certificate_fingerprint": active["certificate_fingerprint"],
                    "spki_fingerprint": active["spki_fingerprint"],
                },
                "pending_peer": None,
                "new_identity_observed": False,
                "observation_ids": [],
            }
            self._write_transaction(transaction, "prepared-peer-replacement")
            return self._transaction_receipt(transaction, _safe_read(certificate_path))

    def stage_peer_leaf(
        self,
        *,
        operation_id: str,
        peer_node_id: str,
        peer_compute_id: str,
        peer_epoch: int,
        certificate_pem: bytes,
    ) -> PeerLeaf:
        _require_identifier("peer node identity", peer_node_id)
        _require_identifier("peer Compute identity", peer_compute_id)
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            if not transaction:
                raise ManagedMTLSError("managed mTLS transaction is unavailable")
            if (
                peer_node_id == transaction["node_id"]
                or peer_epoch != transaction["peer_target_epoch"]
            ):
                raise ManagedMTLSError("managed mTLS peer receipt is outside the operation")
            certificate = validate_vm_ha_managed_certificate(peer_node_id, certificate_pem)
            pending_local = transaction.get("pending_local")
            if not isinstance(
                pending_local, Mapping
            ) or certificate.spki_fingerprint == pending_local.get("spki_fingerprint"):
                raise ManagedMTLSError("managed mTLS members must use distinct keys")
            peer = PeerLeaf(
                node_id=peer_node_id,
                compute_id=peer_compute_id,
                epoch=peer_epoch,
                certificate_fingerprint=certificate.certificate_fingerprint,
                spki_fingerprint=certificate.spki_fingerprint,
            )
            current = transaction.get("pending_peer")
            if current is not None and PeerLeaf.from_mapping(current) != peer:
                raise ManagedMTLSError("managed mTLS peer receipt conflicts")
            if current is not None and _phase_at_least(
                transaction, MTLSTransactionPhase.PEER_STAGED
            ):
                return peer
            peer_path = self._peer_path(peer.certificate_fingerprint)
            _write_once(peer_path, certificate_pem)
            self._fault("peer-certificate-written", peer_path)
            transaction["pending_peer"] = peer.to_dict()
            transaction["phase"] = MTLSTransactionPhase.PEER_STAGED.value
            self._write_transaction(transaction, "peer-staged")
            return peer

    def stage_peer_receipt(self, operation_id: str, receipt: MTLSReceipt) -> PeerLeaf:
        transaction = self._load_transaction(operation_id)
        if transaction is None:
            raise ManagedMTLSError("managed mTLS transaction is unavailable")
        if (
            receipt.operation_id != operation_id
            or receipt.operation_kind.value != transaction.get("operation_kind")
            or receipt.cluster_id != transaction.get("cluster_id")
            or receipt.epoch != transaction.get("peer_target_epoch")
        ):
            raise ManagedMTLSError("managed mTLS peer receipt is outside the operation")
        return self.stage_peer_leaf(
            operation_id=operation_id,
            peer_node_id=receipt.node_id,
            peer_compute_id=receipt.compute_id,
            peer_epoch=receipt.epoch,
            certificate_pem=receipt.certificate_pem,
        )

    def expand_peer_trust(self, operation_id: str) -> MTLSSnapshot | None:
        """Trust old and pending peer leaves while retaining the old local identity."""

        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            active = self._load_active()
            if not transaction or transaction.get("pending_peer") is None:
                raise ManagedMTLSError("managed mTLS peer leaf is not staged")
            if _phase_at_least(transaction, MTLSTransactionPhase.TRUST_EXPANDED):
                return None if active is None else self.snapshot()
            if active is None:
                transaction["phase"] = MTLSTransactionPhase.TRUST_EXPANDED.value
                self._write_transaction(transaction, "trust-expanded")
                return None
            if (
                active.get("cluster_id") != transaction["cluster_id"]
                or active.get("node_id") != transaction["node_id"]
            ):
                raise ManagedMTLSError("managed mTLS active identity is outside the operation")
            peers = [PeerLeaf.from_mapping(item) for item in active["peers"]]
            pending = PeerLeaf.from_mapping(transaction["pending_peer"])
            by_fingerprint = {item.certificate_fingerprint: item for item in peers}
            by_fingerprint[pending.certificate_fingerprint] = pending
            expanded = dict(active)
            expanded["peers"] = [
                item.to_dict()
                for item in sorted(
                    by_fingerprint.values(), key=lambda item: item.certificate_fingerprint
                )
            ]
            expanded["operation_id"] = operation_id
            _atomic_write_json(self.active_path, expanded)
            self._fault("trust-expanded-active-written", self.active_path)
            transaction["phase"] = MTLSTransactionPhase.TRUST_EXPANDED.value
            self._write_transaction(transaction, "trust-expanded")
        return self.snapshot()

    def activate_identity(self, operation_id: str) -> MTLSSnapshot:
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            pending_local = transaction.get("pending_local")
            pending_peer = transaction.get("pending_peer")
            if not isinstance(pending_local, Mapping) or pending_peer is None:
                raise ManagedMTLSError("managed mTLS transaction is not ready to switch")
            if _phase_at_least(transaction, MTLSTransactionPhase.LOCAL_ACTIVE):
                return self.snapshot()
            old_active = self._load_active()
            peers: dict[str, PeerLeaf] = {}
            if old_active is not None:
                for item in old_active["peers"]:
                    peer = PeerLeaf.from_mapping(item)
                    peers[peer.certificate_fingerprint] = peer
            peer = PeerLeaf.from_mapping(pending_peer)
            peers[peer.certificate_fingerprint] = peer
            preserve_local = transaction.get("preserve_local") is True
            if preserve_local and old_active is None:
                raise ManagedMTLSError("managed mTLS survivor identity is unavailable")
            if preserve_local:
                assert old_active is not None
                active_epoch = old_active["epoch"]
                active_certificate_fingerprint = old_active["certificate_fingerprint"]
                active_spki_fingerprint = old_active["spki_fingerprint"]
            else:
                active_epoch = transaction["target_epoch"]
                active_certificate_fingerprint = pending_local["certificate_fingerprint"]
                active_spki_fingerprint = pending_local["spki_fingerprint"]
            active = {
                "schema": _ACTIVE_SCHEMA,
                "cluster_id": transaction["cluster_id"],
                "node_id": transaction["node_id"],
                "compute_id": transaction["compute_id"],
                "epoch": active_epoch,
                "certificate_fingerprint": active_certificate_fingerprint,
                "spki_fingerprint": active_spki_fingerprint,
                "peers": [
                    item.to_dict()
                    for item in sorted(
                        peers.values(), key=lambda item: item.certificate_fingerprint
                    )
                ],
                "operation_id": operation_id,
            }
            _atomic_write_json(self.active_path, active)
            self._fault("local-active-written", self.active_path)
            transaction["new_identity_observed"] = not preserve_local
            transaction["phase"] = MTLSTransactionPhase.LOCAL_ACTIVE.value
            self._write_transaction(transaction, "local-active")
        return self.snapshot()

    def record_fresh_observation(
        self,
        operation_id: str,
        *,
        local_certificate_fingerprint: str,
        peer_certificate_fingerprint: str,
        local_epoch: int,
        peer_epoch: int,
        observation_id: str,
    ) -> int:
        _require_sha256("observation identity", observation_id)
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            pending_local = transaction.get("pending_local")
            pending_peer = transaction.get("pending_peer")
            if not isinstance(pending_local, Mapping) or pending_peer is None:
                raise ManagedMTLSError("managed mTLS transaction is not ready for verification")
            peer = PeerLeaf.from_mapping(pending_peer)
            if (
                local_certificate_fingerprint != pending_local["certificate_fingerprint"]
                or peer_certificate_fingerprint != peer.certificate_fingerprint
                or local_epoch != transaction["target_epoch"]
                or peer_epoch != transaction["peer_target_epoch"]
            ):
                raise ManagedMTLSError("managed mTLS observation does not match the transaction")
            raw_observations = transaction.get("observation_ids")
            if not isinstance(raw_observations, list) or any(
                not isinstance(item, str) or not _SHA256_RE.fullmatch(item)
                for item in raw_observations
            ):
                raise ManagedMTLSError("managed mTLS observation evidence is corrupt")
            observations = list(dict.fromkeys(raw_observations))
            if _phase_at_least(transaction, MTLSTransactionPhase.COMMITTED):
                return len(observations)
            if observation_id not in observations:
                observations.append(observation_id)
            transaction["observation_ids"] = observations
            count = len(observations)
            transaction["new_identity_observed"] = True
            required = (
                _REQUIRED_ROTATION_OBSERVATIONS
                if transaction.get("operation_kind") == MTLSOperationKind.ROTATION.value
                else 1
            )
            if count >= required:
                transaction["phase"] = MTLSTransactionPhase.VERIFIED.value
            self._write_transaction(transaction, "observation-recorded")
            return count

    def commit_transaction(self, operation_id: str) -> MTLSSnapshot:
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            kind = MTLSOperationKind(str(transaction.get("operation_kind")))
            phase = str(transaction.get("phase"))
            required = _REQUIRED_ROTATION_OBSERVATIONS if kind is MTLSOperationKind.ROTATION else 1
            if phase in {
                MTLSTransactionPhase.COMMITTED.value,
                MTLSTransactionPhase.PRUNED.value,
            }:
                return self.snapshot()
            if (
                phase != MTLSTransactionPhase.VERIFIED.value
                or len(transaction.get("observation_ids") or ()) < required
            ):
                raise ManagedMTLSError("managed mTLS transaction lacks fresh verification")
            transaction["phase"] = MTLSTransactionPhase.COMMITTED.value
            self._write_transaction(transaction, "committed")
        return self.snapshot()

    def rollback_before_switch(self, operation_id: str) -> None:
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            if not transaction:
                raise ManagedMTLSError("managed mTLS transaction is unavailable")
            if transaction.get("new_identity_observed"):
                raise ManagedMTLSError("managed mTLS transaction must roll forward")
            active = self._load_active()
            if active is not None and active.get("operation_id") == operation_id:
                old_local = transaction.get("old_local")
                if not isinstance(old_local, Mapping):
                    self.active_path.unlink(missing_ok=True)
                    _fsync_directory(self.root)
                else:
                    restored = {
                        "schema": _ACTIVE_SCHEMA,
                        "cluster_id": transaction["cluster_id"],
                        "node_id": transaction["node_id"],
                        "compute_id": transaction["compute_id"],
                        "epoch": old_local["epoch"],
                        "certificate_fingerprint": old_local["certificate_fingerprint"],
                        "spki_fingerprint": old_local["spki_fingerprint"],
                        "peers": transaction["old_peers"],
                        "operation_id": None,
                    }
                    _atomic_write_json(self.active_path, restored)
            transaction["phase"] = MTLSTransactionPhase.ROLLED_BACK.value
            self._write_transaction(transaction, "rolled-back")

    def prune_obsolete(self, operation_id: str) -> MTLSSnapshot:
        with self._locked():
            transaction = dict(self._load_transaction(operation_id) or {})
            if transaction.get("phase") == MTLSTransactionPhase.PRUNED.value:
                return self.snapshot()
            if transaction.get("phase") != MTLSTransactionPhase.COMMITTED.value:
                raise ManagedMTLSError("managed mTLS transaction is not committed")
            active = dict(self._load_active() or {})
            pending_peer = PeerLeaf.from_mapping(transaction["pending_peer"])
            active["peers"] = [pending_peer.to_dict()]
            active["operation_id"] = None
            _atomic_write_json(self.active_path, active)
            self._fault("pruned-active-written", self.active_path)
            retained_local = str(active["certificate_fingerprint"])
            retained_peer = pending_peer.certificate_fingerprint
            for old in transaction.get("old_peers") or []:
                peer = PeerLeaf.from_mapping(old)
                if peer.certificate_fingerprint != retained_peer:
                    self._peer_path(peer.certificate_fingerprint).unlink(missing_ok=True)
            old_local = transaction.get("old_local")
            if isinstance(old_local, Mapping):
                fingerprint = str(old_local["certificate_fingerprint"])
                if fingerprint != retained_local:
                    certificate_path, key_path = self._identity_paths(fingerprint)
                    certificate_path.unlink(missing_ok=True)
                    key_path.unlink(missing_ok=True)
                    certificate_path.parent.rmdir()
            _fsync_directory(self.peers)
            _fsync_directory(self.identities)
            transaction["phase"] = MTLSTransactionPhase.PRUNED.value
            self._write_transaction(transaction, "pruned")
        return self.snapshot()

    def snapshot(self) -> MTLSSnapshot:
        active = self._load_active()
        if active is None:
            raise ManagedMTLSError("managed mTLS identity is not active")
        peers = tuple(PeerLeaf.from_mapping(item) for item in active["peers"])
        if not peers:
            raise ManagedMTLSError("managed mTLS peer trust is empty")
        certificate_path, key_path = self._identity_paths(str(active["certificate_fingerprint"]))
        certificate_pem = _safe_read(certificate_path)
        private_key_pem = _safe_read(key_path)
        local = validate_vm_ha_managed_certificate(
            str(active["node_id"]), certificate_pem, private_key_pem=private_key_pem
        )
        if (
            local.certificate_fingerprint != active["certificate_fingerprint"]
            or local.spki_fingerprint != active["spki_fingerprint"]
        ):
            raise ManagedMTLSError("managed mTLS active identity digest mismatch")
        peer_paths: list[Path] = []
        peer_pems: list[bytes] = []
        for peer in peers:
            peer_path = self._peer_path(peer.certificate_fingerprint)
            peer_pem = _safe_read(peer_path)
            peer_certificate = validate_vm_ha_managed_certificate(peer.node_id, peer_pem)
            if (
                peer_certificate.certificate_fingerprint != peer.certificate_fingerprint
                or peer_certificate.spki_fingerprint != peer.spki_fingerprint
            ):
                raise ManagedMTLSError("managed mTLS peer identity digest mismatch")
            peer_paths.append(peer_path)
            peer_pems.append(peer_pem)
        return MTLSSnapshot(
            cluster_id=_require_identifier("cluster identity", str(active["cluster_id"])),
            node_id=_require_identifier("node identity", str(active["node_id"])),
            compute_id=_require_identifier("Compute identity", str(active["compute_id"])),
            epoch=int(active["epoch"]),
            certificate_fingerprint=_require_sha256(
                "certificate fingerprint", str(active["certificate_fingerprint"])
            ),
            spki_fingerprint=_require_sha256("SPKI fingerprint", str(active["spki_fingerprint"])),
            certificate_path=certificate_path,
            private_key_path=key_path,
            peers=peers,
            peer_certificate_paths=tuple(peer_paths),
            peer_certificate_pems=tuple(peer_pems),
        )

    def status(self) -> MTLSStatus:
        active = self._load_active()
        inhibition_operation_id = self.inhibition_operation_id()
        transactions = sorted(self.transactions.glob("*.json"), key=lambda path: path.name)
        pending: Mapping[str, Any] | None = None
        if inhibition_operation_id is not None:
            inhibited_transaction = self._load_transaction(inhibition_operation_id)
            if (
                inhibited_transaction is not None
                and inhibited_transaction.get("phase")
                != MTLSTransactionPhase.ROLLED_BACK.value
            ):
                pending = inhibited_transaction
        for path in transactions:
            candidate = _read_json(path)
            if pending is None and candidate.get("phase") not in {
                MTLSTransactionPhase.PRUNED.value,
                MTLSTransactionPhase.ROLLED_BACK.value,
            }:
                pending = candidate
        if active is None:
            return MTLSStatus(
                state="missing" if pending is None else "transitioning",
                cluster_id=None if pending is None else str(pending["cluster_id"]),
                node_id=None if pending is None else str(pending["node_id"]),
                compute_id=None if pending is None else str(pending["compute_id"]),
                epoch=None,
                certificate_fingerprint=None,
                spki_fingerprint=None,
                peer_fingerprints=(),
                operation_id=None if pending is None else str(pending["operation_id"]),
                operation_kind=None if pending is None else str(pending["operation_kind"]),
                target_epoch=None if pending is None else int(pending["target_epoch"]),
                peer_target_epoch=(None if pending is None else int(pending["peer_target_epoch"])),
                preserve_local=(None if pending is None else bool(pending["preserve_local"])),
                inhibited=inhibition_operation_id is not None,
                inhibition_operation_id=inhibition_operation_id,
                phase=None if pending is None else str(pending["phase"]),
                recovery=None if pending is None else "resume",
            )
        peers = tuple(
            PeerLeaf.from_mapping(item).certificate_fingerprint for item in active["peers"]
        )
        return MTLSStatus(
            state="healthy" if pending is None else "transitioning",
            cluster_id=str(active["cluster_id"]),
            node_id=str(active["node_id"]),
            compute_id=str(active["compute_id"]),
            epoch=int(active["epoch"]),
            certificate_fingerprint=str(active["certificate_fingerprint"]),
            spki_fingerprint=str(active["spki_fingerprint"]),
            peer_fingerprints=peers,
            operation_id=None if pending is None else str(pending["operation_id"]),
            operation_kind=None if pending is None else str(pending["operation_kind"]),
            target_epoch=None if pending is None else int(pending["target_epoch"]),
            peer_target_epoch=None if pending is None else int(pending["peer_target_epoch"]),
            preserve_local=None if pending is None else bool(pending["preserve_local"]),
            inhibited=inhibition_operation_id is not None,
            inhibition_operation_id=inhibition_operation_id,
            phase=None if pending is None else str(pending["phase"]),
            recovery=None
            if pending is None
            else ("roll-forward" if pending.get("new_identity_observed") else "rollback-or-resume"),
        )
