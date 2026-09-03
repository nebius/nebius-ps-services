"""Secret-free durable compensation for operation-created IAM credentials."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

CREDENTIAL_COMPENSATION_SCHEMA = "nebius-cxcli-credential-compensation/v2"
CREDENTIAL_JOURNAL_ROOT_ENV = "NEBIUS_CXCLI_CREDENTIAL_JOURNAL_DIR"
_JOURNAL_FIELDS = {
    "schema",
    "status",
    "operationId",
    "projectIdSha256",
    "scopeSha256",
    "credentials",
    "delivery",
}
_ACTIVE_STATUSES = {
    "creating",
    "delivery-pending",
    "delivery-uncertain",
    "compensation-pending",
}
_TERMINAL_STATUSES = {"delivered", "compensated"}


class CredentialCompensationError(RuntimeError):
    """Credential ownership or compensation could not be proved complete."""


CredentialDelete = Callable[[str, str], None]
CredentialResolve = Callable[[Mapping[str, Any], str], tuple[str, ...]]


class CredentialDeliveryDisposition(StrEnum):
    """Independently observable delivery outcomes understood by recovery."""

    DELIVERED = "delivered"
    NOT_DELIVERED = "not-delivered"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class CredentialDeliveryIntent:
    """Secret-free durable identity for one credential delivery destination."""

    kind: str
    target_sha256: str
    marker_sha256: str
    credential_ids_sha256: str


class CredentialDeliveryAdapter[CredentialResult](Protocol):
    """A delivery boundary that can classify its durable postcondition."""

    def prepare(
        self,
        result: CredentialResult,
        *,
        operation_id: str,
        credentials: tuple[tuple[str, str], ...],
    ) -> CredentialDeliveryIntent: ...

    def deliver(
        self,
        result: CredentialResult,
        intent: CredentialDeliveryIntent,
    ) -> CredentialDeliveryDisposition: ...

    def probe(self, intent: CredentialDeliveryIntent) -> CredentialDeliveryDisposition: ...


@dataclass(frozen=True)
class CallbackCredentialDeliveryAdapter[CredentialResult]:
    """Typed adapter for callback destinations, with an optional durable probe."""

    kind: str
    target: str
    deliver_callback: Callable[[CredentialResult, CredentialDeliveryIntent], None]
    probe_callback: Callable[[CredentialDeliveryIntent], CredentialDeliveryDisposition] | None = (
        None
    )

    def prepare(
        self,
        result: CredentialResult,
        *,
        operation_id: str,
        credentials: tuple[tuple[str, str], ...],
    ) -> CredentialDeliveryIntent:
        del result
        if not self.kind.strip() or not self.target.strip() or not credentials:
            raise ValueError("credential delivery identity is incomplete")
        credentials_payload = json.dumps(
            credentials,
            sort_keys=True,
            separators=(",", ":"),
        )
        return CredentialDeliveryIntent(
            kind=self.kind.strip(),
            target_sha256=_sha256(self.target.strip()),
            marker_sha256=_sha256(f"{operation_id}:{self.kind.strip()}"),
            credential_ids_sha256=_sha256(credentials_payload),
        )

    def deliver(
        self,
        result: CredentialResult,
        intent: CredentialDeliveryIntent,
    ) -> CredentialDeliveryDisposition:
        self.deliver_callback(result, intent)
        return CredentialDeliveryDisposition.DELIVERED

    def probe(self, intent: CredentialDeliveryIntent) -> CredentialDeliveryDisposition:
        if self.probe_callback is None:
            return CredentialDeliveryDisposition.AMBIGUOUS
        return self.probe_callback(intent)


@dataclass(frozen=True)
class TransformedCredentialDeliveryAdapter[CredentialResult, DeliveredResult]:
    """Adapt an issuer result to the destination's delivered value type."""

    transform: Callable[[CredentialResult], DeliveredResult]
    destination: CredentialDeliveryAdapter[DeliveredResult]

    def prepare(
        self,
        result: CredentialResult,
        *,
        operation_id: str,
        credentials: tuple[tuple[str, str], ...],
    ) -> CredentialDeliveryIntent:
        return self.destination.prepare(
            self.transform(result),
            operation_id=operation_id,
            credentials=credentials,
        )

    def deliver(
        self,
        result: CredentialResult,
        intent: CredentialDeliveryIntent,
    ) -> CredentialDeliveryDisposition:
        return self.destination.deliver(self.transform(result), intent)

    def probe(self, intent: CredentialDeliveryIntent) -> CredentialDeliveryDisposition:
        return self.destination.probe(intent)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _journal_root() -> Path:
    override = os.environ.get(CREDENTIAL_JOURNAL_ROOT_ENV, "").strip()
    if override:
        return Path(os.path.abspath(Path(override).expanduser()))
    return Path(os.path.abspath(Path.home() / ".config" / "nebius-cxcli" / "credentials"))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


class CredentialCompensationJournal:
    """One serialized credential issue/delivery/compensation scope."""

    def __init__(self, *, project_id: str, scope: str) -> None:
        if not project_id.strip() or not scope.strip():
            raise ValueError("credential journal requires project_id and scope")
        identity = _sha256(f"{project_id.strip()}\0{scope.strip()}").removeprefix("sha256:")
        self.project_id_sha256 = _sha256(project_id.strip())
        self.scope_sha256 = _sha256(scope.strip())
        self.root = _journal_root()
        self.path = self.root / f"{identity}.json"
        self.lock_path = self.root / f"{identity}.lock"
        self.payload: dict[str, Any] | None = None

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise CredentialCompensationError("credential journal directory is unsafe")
        os.chmod(self.root, 0o700)

    @contextmanager
    def locked(self) -> Iterator[CredentialCompensationJournal]:
        self._ensure_root()
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise CredentialCompensationError("credential journal lock is unsafe")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.payload = self._read()
            yield self
        finally:
            self.payload = None
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self) -> dict[str, Any] | None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise CredentialCompensationError("credential journal is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialCompensationError("credential journal is invalid") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != _JOURNAL_FIELDS
            or payload.get("schema") != CREDENTIAL_COMPENSATION_SCHEMA
        ):
            raise CredentialCompensationError("credential journal has an unsupported schema")
        if (
            payload.get("projectIdSha256") != self.project_id_sha256
            or payload.get("scopeSha256") != self.scope_sha256
        ):
            raise CredentialCompensationError("credential journal identity is invalid")
        operation_id = payload.get("operationId")
        if (
            not isinstance(operation_id, str)
            or len(operation_id) != 32
            or any(character not in "0123456789abcdef" for character in operation_id)
        ):
            raise CredentialCompensationError("credential journal operation identity is invalid")
        status = payload.get("status")
        if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
            raise CredentialCompensationError("credential journal status is invalid")
        credentials = payload.get("credentials")
        if not isinstance(credentials, list):
            raise CredentialCompensationError("credential journal entries are invalid")
        for item in credentials:
            if not isinstance(item, dict):
                raise CredentialCompensationError("credential journal entry is invalid")
            if set(item) != {
                "kind",
                "ownershipSha256",
                "resourceId",
                "serviceAccountId",
                "status",
            }:
                raise CredentialCompensationError("credential journal entry fields are invalid")
            if item.get("kind") not in {"auth-public-key", "access-key", "static-key"}:
                raise CredentialCompensationError("credential journal entry kind is invalid")
            if item.get("status") not in {"intent", "created", "deleted"}:
                raise CredentialCompensationError("credential journal entry status is invalid")
            ownership_sha256 = item.get("ownershipSha256")
            service_account_id = item.get("serviceAccountId")
            resource_id = item.get("resourceId")
            if (
                not isinstance(ownership_sha256, str)
                or len(ownership_sha256) != 71
                or not ownership_sha256.startswith("sha256:")
                or any(
                    character not in "0123456789abcdef"
                    for character in ownership_sha256.removeprefix("sha256:")
                )
                or not isinstance(service_account_id, str)
                or not service_account_id.strip()
            ):
                raise CredentialCompensationError("credential journal entry ownership is invalid")
            if item["status"] == "intent" and resource_id is not None:
                raise CredentialCompensationError("credential journal intent has a result identity")
            if item["status"] in {"created", "deleted"} and (
                not isinstance(resource_id, str) or not resource_id.strip()
            ):
                raise CredentialCompensationError("credential journal result identity is invalid")
        delivery = payload.get("delivery")
        if delivery is not None:
            if not isinstance(delivery, dict) or set(delivery) != {
                "kind",
                "targetSha256",
                "markerSha256",
                "credentialIdsSha256",
            }:
                raise CredentialCompensationError("credential delivery intent is invalid")
            if not isinstance(delivery.get("kind"), str) or not delivery["kind"].strip():
                raise CredentialCompensationError("credential delivery kind is invalid")
            for key in ("targetSha256", "markerSha256", "credentialIdsSha256"):
                digest = delivery.get(key)
                if (
                    not isinstance(digest, str)
                    or len(digest) != 71
                    or not digest.startswith("sha256:")
                    or any(
                        character not in "0123456789abcdef"
                        for character in digest.removeprefix("sha256:")
                    )
                ):
                    raise CredentialCompensationError("credential delivery identity is invalid")
        if status in {"delivery-pending", "delivery-uncertain", "delivered"} and delivery is None:
            raise CredentialCompensationError("credential delivery intent is missing")
        if status == "delivered" and (
            not credentials or any(item["status"] != "created" for item in credentials)
        ):
            raise CredentialCompensationError("delivered credential journal is incomplete")
        if status == "compensated" and any(item["status"] != "deleted" for item in credentials):
            raise CredentialCompensationError("compensated credential journal is incomplete")
        return payload

    def _write(self) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        _atomic_json(self.path, self.payload)

    def recover(
        self,
        *,
        delete: CredentialDelete,
        resolve: CredentialResolve,
        delivery: CredentialDeliveryAdapter[Any],
    ) -> None:
        """Finish pending reverse compensation before a new credential issue."""

        if self.payload is None:
            return
        status = self.payload.get("status")
        if status in _TERMINAL_STATUSES:
            return
        if status not in _ACTIVE_STATUSES:
            raise CredentialCompensationError("credential journal status is invalid")
        credentials = self.payload["credentials"]
        for item in credentials:
            if item["status"] != "intent":
                continue
            matches = tuple(dict.fromkeys(resolve(item, str(self.payload["operationId"]))))
            if len(matches) != 1:
                self.payload["status"] = "compensation-pending"
                self._write()
                detail = "no" if not matches else "multiple"
                raise CredentialCompensationError(
                    f"pending credential intent resolved to {detail} provider resources"
                )
            item["resourceId"] = matches[0]
            item["status"] = "created"
            self.payload["status"] = "compensation-pending"
            self._write()
        delivery_payload = self.payload.get("delivery")
        if isinstance(delivery_payload, Mapping):
            intent = CredentialDeliveryIntent(
                kind=str(delivery_payload["kind"]),
                target_sha256=str(delivery_payload["targetSha256"]),
                marker_sha256=str(delivery_payload["markerSha256"]),
                credential_ids_sha256=str(delivery_payload["credentialIdsSha256"]),
            )
            disposition = delivery.probe(intent)
            if disposition is CredentialDeliveryDisposition.DELIVERED:
                self.mark_delivered()
                return
            if disposition is CredentialDeliveryDisposition.AMBIGUOUS:
                self.mark_delivery_uncertain()
                raise CredentialCompensationError(
                    "credential delivery remains ambiguous; created credentials were preserved"
                )
            if disposition is not CredentialDeliveryDisposition.NOT_DELIVERED:
                raise CredentialCompensationError(
                    "credential delivery probe returned an invalid disposition"
                )
        self.compensate(delete=delete)

    def begin(self) -> str:
        if self.payload is not None and self.payload.get("status") not in {
            None,
            "delivered",
            "compensated",
        }:
            raise CredentialCompensationError(
                "pending credential compensation must finish before another credential is created"
            )
        operation_id = uuid.uuid4().hex
        self.payload = {
            "schema": CREDENTIAL_COMPENSATION_SCHEMA,
            "status": "creating",
            "operationId": operation_id,
            "projectIdSha256": self.project_id_sha256,
            "scopeSha256": self.scope_sha256,
            "credentials": [],
            "delivery": None,
        }
        self._write()
        return operation_id

    def record_intent(
        self,
        *,
        kind: str,
        ownership_sha256: str,
        service_account_id: str,
    ) -> None:
        if self.payload is None or self.payload.get("status") not in {
            "creating",
            "delivery-pending",
        }:
            raise CredentialCompensationError("credential issue intent is not active")
        if kind not in {"auth-public-key", "access-key", "static-key"}:
            raise ValueError(f"unsupported compensated credential kind: {kind}")
        if not ownership_sha256.startswith("sha256:") or not service_account_id.strip():
            raise ValueError("credential intent ownership evidence is incomplete")
        self.payload["credentials"].append(
            {
                "kind": kind,
                "ownershipSha256": ownership_sha256,
                "resourceId": None,
                "serviceAccountId": service_account_id.strip(),
                "status": "intent",
            }
        )
        self._write()

    def record_created(self, *, kind: str, resource_id: str) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        if not resource_id.strip():
            raise ValueError("credential result identity is required")
        for item in reversed(self.payload["credentials"]):
            if item["kind"] == kind and item["status"] == "intent":
                item["resourceId"] = resource_id.strip()
                item["status"] = "created"
                self.payload["status"] = "creating"
                self._write()
                return
        raise CredentialCompensationError("credential result has no matching write-ahead intent")

    def created_credentials(self) -> tuple[tuple[str, str], ...]:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        created: list[tuple[str, str]] = []
        for item in self.payload["credentials"]:
            if item["status"] != "created":
                raise CredentialCompensationError(
                    "credential delivery cannot start with unresolved provider mutations"
                )
            created.append((str(item["kind"]), str(item["resourceId"])))
        if not created:
            raise CredentialCompensationError("credential delivery requires created credentials")
        return tuple(created)

    def record_delivery_intent(self, intent: CredentialDeliveryIntent) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        if self.payload.get("delivery") is not None:
            raise CredentialCompensationError("credential delivery intent is already recorded")
        self.created_credentials()
        payload = asdict(intent)
        self.payload["delivery"] = {
            "kind": payload["kind"],
            "targetSha256": payload["target_sha256"],
            "markerSha256": payload["marker_sha256"],
            "credentialIdsSha256": payload["credential_ids_sha256"],
        }
        self.payload["status"] = "delivery-pending"
        self._write()

    def mark_delivery_uncertain(self) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        if self.payload.get("delivery") is None:
            raise CredentialCompensationError("credential delivery intent is missing")
        self.payload["status"] = "delivery-uncertain"
        self._write()

    def mark_delivered(self) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        if not self.payload["credentials"] or any(
            item["status"] != "created" for item in self.payload["credentials"]
        ):
            raise CredentialCompensationError(
                "credential delivery cannot complete unresolved intent"
            )
        if self.payload.get("delivery") is None:
            raise CredentialCompensationError("credential delivery intent is missing")
        self.payload["status"] = "delivered"
        self._write()

    def compensate(self, *, delete: CredentialDelete) -> None:
        if self.payload is None:
            raise RuntimeError("credential journal lock is not held")
        if self.payload.get("status") == "delivered":
            raise CredentialCompensationError("delivered credentials cannot be compensated")
        if self.payload.get("status") == "compensated":
            return
        if any(item["status"] == "intent" for item in self.payload["credentials"]):
            self.payload["status"] = "compensation-pending"
            self._write()
            raise CredentialCompensationError(
                "credential compensation cannot delete an unresolved provider mutation"
            )
        self.payload["status"] = "compensation-pending"
        self._write()
        for item in reversed(self.payload["credentials"]):
            if item["status"] == "deleted":
                continue
            resource_id = str(item["resourceId"] or "").strip()
            if not resource_id:
                raise CredentialCompensationError("credential compensation resource ID is missing")
            try:
                delete(str(item["kind"]), resource_id)
            except Exception:
                self._write()
                raise CredentialCompensationError(
                    f"credential compensation remains pending for {item['kind']}"
                ) from None
            item["status"] = "deleted"
            self._write()
        self.payload["status"] = "compensated"
        self._write()


__all__ = [
    "CREDENTIAL_COMPENSATION_SCHEMA",
    "CREDENTIAL_JOURNAL_ROOT_ENV",
    "CallbackCredentialDeliveryAdapter",
    "CredentialCompensationError",
    "CredentialCompensationJournal",
    "CredentialDeliveryAdapter",
    "CredentialDeliveryDisposition",
    "CredentialDeliveryIntent",
    "TransformedCredentialDeliveryAdapter",
]
