"""Identity-bound, resumable teardown for ordinary and VM-HA gateways."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import typing as t
from dataclasses import dataclass, replace
from pathlib import Path

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.nebius_pagination import (
    collect_nebius_pages,
    nebius_resource_id,
)

from .vm_ha_cloud import (
    allocation_observation,
    nebius_request_error_code_is,
    operation_status_lookup_unsupported,
    vm_ha_request_kwargs,
    wait_vm_ha_operation,
)
from .vm_ha_lifecycle import (
    VMHAApplyLock,
    VMHALifecycleJournal,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    vm_ha_effective_resource_bindings,
)

_RECEIPT_SCHEMA = "nebius-vpngw/destroy-lifecycle-v2"
_MAX_RECEIPT_BYTES = 1024 * 1024
_DESTROY_REASON_CODES = frozenset(
    {
        "destroy-authority-conflict",
        "destroy-allocation-delete-failed",
        "destroy-compute-delete-failed",
        "destroy-compute-fence-failed",
        "destroy-disk-delete-failed",
        "destroy-operation-failed",
        "destroy-predecessor-resume-failed",
        "destroy-route-delete-failed",
        "destroy-terminal-verification-failed",
    }
)


class DestroyFailure(RuntimeError):
    """Internal destroy failure with one closed, identity-free public reason."""

    def __init__(self, reason_code: str, detail: str) -> None:
        if reason_code not in _DESTROY_REASON_CODES:
            raise ValueError("Destroy failure reason code is invalid")
        super().__init__(detail)
        self.reason_code = reason_code


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _resource_id(resource: object | None) -> str:
    if resource is None:
        return ""
    value = getattr(resource, "id", None) or getattr(
        getattr(resource, "metadata", None), "id", None
    )
    return str(value or "")


def _resource_name(resource: object | None) -> str:
    return str(getattr(getattr(resource, "metadata", None), "name", "") or "")


def _resource_parent_id(resource: object | None) -> str:
    return str(getattr(getattr(resource, "metadata", None), "parent_id", "") or "")


def _resource_revision(resource: object | None) -> str:
    return str(getattr(getattr(resource, "metadata", None), "resource_version", "") or "")


@dataclass(frozen=True)
class DestroyResource:
    """One exact cloud resource selected for deletion or retention."""

    kind: str
    name: str
    resource_id: str
    revision: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"allocation", "compute", "disk", "public-allocation"}:
            raise ValueError("Destroy resource kind is invalid")
        if not self.name or not self.resource_id:
            raise ValueError("Destroy resource identity is incomplete")

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "resource_id": self.resource_id,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: object) -> DestroyResource:
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "name",
            "resource_id",
            "revision",
        }:
            raise ValueError("Destroy resource record is malformed")
        if any(not isinstance(value.get(field), str) for field in value):
            raise ValueError("Destroy resource record is malformed")
        return cls(
            kind=t.cast(str, value["kind"]),
            name=t.cast(str, value["name"]),
            resource_id=t.cast(str, value["resource_id"]),
            revision=t.cast(str, value["revision"]),
        )


@dataclass(frozen=True)
class DestroyRoute:
    """One exact route whose cloud authority permits deletion."""

    route_id: str
    name: str
    route_table_id: str
    allocation_id: str
    revision: str = ""

    def __post_init__(self) -> None:
        if not self.route_id or not self.route_table_id or not self.allocation_id:
            raise ValueError("Destroy route identity is incomplete")

    def to_dict(self) -> dict[str, str]:
        return {
            "allocation_id": self.allocation_id,
            "name": self.name,
            "revision": self.revision,
            "route_id": self.route_id,
            "route_table_id": self.route_table_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> DestroyRoute:
        expected = {
            "allocation_id",
            "name",
            "revision",
            "route_id",
            "route_table_id",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Destroy route record is malformed")
        if any(not isinstance(value.get(field), str) for field in expected):
            raise ValueError("Destroy route record is malformed")
        return cls(**t.cast(dict[str, str], value))


@dataclass(frozen=True)
class DestroyPlan:
    """Immutable delete and retention manifest approved by one invocation."""

    project_id: str
    gateway_name: str
    topology: str
    config_digest: str
    expected_compute_names: tuple[str, ...]
    expected_disk_names: tuple[str, ...]
    expected_allocation_names: tuple[str, ...]
    compute: tuple[DestroyResource, ...]
    disks: tuple[DestroyResource, ...]
    allocations: tuple[DestroyResource, ...]
    routes: tuple[DestroyRoute, ...]
    retained_public_allocations: tuple[DestroyResource, ...]
    retained_route_table_ids: tuple[str, ...]
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.topology not in {"ordinary", "vm-ha", "vm-ha-unbound"}:
            raise ValueError("Destroy topology is invalid")
        if (
            not self.project_id
            or not self.gateway_name
            or re.fullmatch(r"[0-9a-f]{64}", self.config_digest) is None
        ):
            raise ValueError("Destroy plan scope is incomplete")
        ordered_fields = (
            self.expected_compute_names,
            self.expected_disk_names,
            self.expected_allocation_names,
            self.retained_route_table_ids,
            self.blockers,
        )
        if any(value != tuple(sorted(set(value))) for value in ordered_fields):
            raise ValueError("Destroy plan string sets are not canonical")
        for resources in (
            self.compute,
            self.disks,
            self.allocations,
            self.retained_public_allocations,
        ):
            if resources != tuple(
                sorted(resources, key=lambda item: (item.name, item.resource_id))
            ):
                raise ValueError("Destroy plan resources are not canonical")
            if len({item.name for item in resources}) != len(resources) or len(
                {item.resource_id for item in resources}
            ) != len(resources):
                raise ValueError("Destroy plan contains duplicate resource authority")
        if self.routes != tuple(
            sorted(self.routes, key=lambda item: (item.route_table_id, item.route_id))
        ):
            raise ValueError("Destroy plan routes are not canonical")
        if len({route.route_id for route in self.routes}) != len(self.routes):
            raise ValueError("Destroy plan contains duplicate route authority")
        expected_by_kind = {
            "compute": set(self.expected_compute_names),
            "disk": set(self.expected_disk_names),
            "allocation": set(self.expected_allocation_names),
        }
        for kind, resources in (
            ("compute", self.compute),
            ("disk", self.disks),
            ("allocation", self.allocations),
        ):
            if any(
                resource.kind != kind or resource.name not in expected_by_kind[kind]
                for resource in resources
            ):
                raise ValueError("Destroy plan resource is outside its configured name scope")
        allocation_ids = {resource.resource_id for resource in self.allocations}
        if any(route.allocation_id not in allocation_ids for route in self.routes):
            raise ValueError("Destroy plan route is outside its allocation scope")
        public_ids = {resource.resource_id for resource in self.retained_public_allocations}
        if allocation_ids & public_ids or any(
            resource.kind != "public-allocation" for resource in self.retained_public_allocations
        ):
            raise ValueError("Destroy plan public allocation authority overlaps deletion scope")

    @property
    def effect_count(self) -> int:
        return len(self.compute) + len(self.disks) + len(self.allocations) + len(self.routes)

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "allocations": [item.to_dict() for item in self.allocations],
            "blockers": list(self.blockers),
            "compute": [item.to_dict() for item in self.compute],
            "config_digest": self.config_digest,
            "disks": [item.to_dict() for item in self.disks],
            "expected_allocation_names": list(self.expected_allocation_names),
            "expected_compute_names": list(self.expected_compute_names),
            "expected_disk_names": list(self.expected_disk_names),
            "gateway_name": self.gateway_name,
            "project_id": self.project_id,
            "retained_public_allocations": [
                item.to_dict() for item in self.retained_public_allocations
            ],
            "retained_route_table_ids": list(self.retained_route_table_ids),
            "routes": [item.to_dict() for item in self.routes],
            "topology": self.topology,
        }

    def to_json(self) -> str:
        value = _canonical_json(self.to_dict())
        if len(value.encode("utf-8")) > _MAX_RECEIPT_BYTES // 2:
            raise ValueError("Destroy plan exceeds the durable checkpoint size limit")
        return value

    @classmethod
    def from_dict(cls, value: object) -> DestroyPlan:
        expected = {
            "allocations",
            "blockers",
            "compute",
            "config_digest",
            "disks",
            "expected_allocation_names",
            "expected_compute_names",
            "expected_disk_names",
            "gateway_name",
            "project_id",
            "retained_public_allocations",
            "retained_route_table_ids",
            "routes",
            "topology",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Destroy plan is malformed")
        string_fields = ("config_digest", "gateway_name", "project_id", "topology")
        list_fields = expected - set(string_fields)
        if any(not isinstance(value.get(field), str) for field in string_fields) or any(
            not isinstance(value.get(field), list) for field in list_fields
        ):
            raise ValueError("Destroy plan is malformed")
        for field in (
            "blockers",
            "expected_allocation_names",
            "expected_compute_names",
            "expected_disk_names",
            "retained_route_table_ids",
        ):
            if any(not isinstance(item, str) for item in t.cast(list[object], value[field])):
                raise ValueError("Destroy plan string set is malformed")
        return cls(
            project_id=t.cast(str, value["project_id"]),
            gateway_name=t.cast(str, value["gateway_name"]),
            topology=t.cast(str, value["topology"]),
            config_digest=t.cast(str, value["config_digest"]),
            expected_compute_names=tuple(t.cast(list[str], value["expected_compute_names"])),
            expected_disk_names=tuple(t.cast(list[str], value["expected_disk_names"])),
            expected_allocation_names=tuple(t.cast(list[str], value["expected_allocation_names"])),
            compute=tuple(
                DestroyResource.from_dict(item) for item in t.cast(list[object], value["compute"])
            ),
            disks=tuple(
                DestroyResource.from_dict(item) for item in t.cast(list[object], value["disks"])
            ),
            allocations=tuple(
                DestroyResource.from_dict(item)
                for item in t.cast(list[object], value["allocations"])
            ),
            routes=tuple(
                DestroyRoute.from_dict(item) for item in t.cast(list[object], value["routes"])
            ),
            retained_public_allocations=tuple(
                DestroyResource.from_dict(item)
                for item in t.cast(list[object], value["retained_public_allocations"])
            ),
            retained_route_table_ids=tuple(t.cast(list[str], value["retained_route_table_ids"])),
            blockers=tuple(t.cast(list[str], value["blockers"])),
        )

    @classmethod
    def from_json(cls, value: str) -> DestroyPlan:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Destroy plan JSON is malformed") from error
        return cls.from_dict(payload)


@dataclass(frozen=True)
class DestroyReceipt:
    """One ordinary/non-authoritative-HA durable effect journal."""

    operation_id: str
    plan: DestroyPlan
    pending_effect: str | None = None
    accepted_operation_kind: str | None = None
    accepted_cloud_operation_id: str | None = None
    failed_operations: tuple[tuple[str, str, str], ...] = ()
    completed_effects: tuple[str, ...] = ()
    revision: int = 1
    predecessor_sha256: str | None = None
    terminal: bool = False

    def __post_init__(self) -> None:
        if (
            self.operation_id != _digest({"destroy": self.plan.digest})
            or self.revision < 1
            or (self.revision == 1) != (self.predecessor_sha256 is None)
            or (
                self.predecessor_sha256 is not None
                and re.fullmatch(r"[0-9a-f]{64}", self.predecessor_sha256) is None
            )
        ):
            raise ValueError("Destroy receipt identity is invalid")
        if self.completed_effects != tuple(sorted(set(self.completed_effects))):
            raise ValueError("Destroy receipt completed effects are not canonical")
        accepted = (self.accepted_operation_kind, self.accepted_cloud_operation_id)
        if (accepted[0] is None) != (accepted[1] is None):
            raise ValueError("Destroy accepted operation is incomplete")
        if accepted[0] is not None and self.pending_effect is None:
            raise ValueError("Destroy accepted operation has no pending effect")
        if any(
            len(item) != 3 or not all(isinstance(value, str) and value for value in item)
            for item in self.failed_operations
        ) or len(set(self.failed_operations)) != len(self.failed_operations):
            raise ValueError("Destroy failed operation history is invalid")
        if accepted[1] is not None and any(
            cloud_operation_id == accepted[1]
            for _effect, _kind, cloud_operation_id in self.failed_operations
        ):
            raise ValueError("Destroy accepted operation was already superseded")
        if self.terminal and self.pending_effect is not None:
            raise ValueError("Destroy terminal receipt has a pending effect")

    @property
    def record_sha256(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted_cloud_operation_id": self.accepted_cloud_operation_id,
            "accepted_operation_kind": self.accepted_operation_kind,
            "completed_effects": list(self.completed_effects),
            "failed_operations": [
                {
                    "cloud_operation_id": cloud_operation_id,
                    "effect": effect,
                    "kind": kind,
                }
                for effect, kind, cloud_operation_id in self.failed_operations
            ],
            "operation_id": self.operation_id,
            "pending_effect": self.pending_effect,
            "plan": self.plan.to_dict(),
            "predecessor_sha256": self.predecessor_sha256,
            "revision": self.revision,
            "schema": _RECEIPT_SCHEMA,
            "terminal": self.terminal,
        }

    @classmethod
    def from_dict(cls, value: object) -> DestroyReceipt:
        expected = {
            "accepted_cloud_operation_id",
            "accepted_operation_kind",
            "completed_effects",
            "failed_operations",
            "operation_id",
            "pending_effect",
            "plan",
            "predecessor_sha256",
            "revision",
            "schema",
            "terminal",
        }
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value.get("schema") != _RECEIPT_SCHEMA
            or not isinstance(value.get("operation_id"), str)
            or not isinstance(value.get("revision"), int)
            or isinstance(value.get("revision"), bool)
            or not isinstance(value.get("terminal"), bool)
            or not isinstance(value.get("completed_effects"), list)
            or not isinstance(value.get("failed_operations"), list)
        ):
            raise ValueError("Destroy receipt is malformed")
        for field in (
            "accepted_cloud_operation_id",
            "accepted_operation_kind",
            "pending_effect",
            "predecessor_sha256",
        ):
            if value.get(field) is not None and not isinstance(value.get(field), str):
                raise ValueError("Destroy receipt is malformed")
        completed = t.cast(list[object], value["completed_effects"])
        if any(not isinstance(item, str) for item in completed):
            raise ValueError("Destroy receipt is malformed")
        failed_operations: list[tuple[str, str, str]] = []
        for item in t.cast(list[object], value["failed_operations"]):
            if (
                not isinstance(item, dict)
                or set(item) != {"cloud_operation_id", "effect", "kind"}
                or any(not isinstance(item.get(field), str) for field in item)
            ):
                raise ValueError("Destroy receipt is malformed")
            failed_operations.append(
                (
                    t.cast(str, item["effect"]),
                    t.cast(str, item["kind"]),
                    t.cast(str, item["cloud_operation_id"]),
                )
            )
        return cls(
            operation_id=t.cast(str, value["operation_id"]),
            plan=DestroyPlan.from_dict(value["plan"]),
            pending_effect=t.cast(str | None, value["pending_effect"]),
            accepted_operation_kind=t.cast(str | None, value["accepted_operation_kind"]),
            accepted_cloud_operation_id=t.cast(str | None, value["accepted_cloud_operation_id"]),
            failed_operations=tuple(failed_operations),
            completed_effects=tuple(t.cast(list[str], completed)),
            revision=t.cast(int, value["revision"]),
            predecessor_sha256=t.cast(str | None, value["predecessor_sha256"]),
            terminal=t.cast(bool, value["terminal"]),
        )


class DestroyReceiptStore:
    """Owner-controlled atomic CAS store for non-HA destruction."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.with_name(f"{config_path.name}.destroy-lifecycle.json")
        self._lock_path = self.path.with_name(f".{self.path.name}.write.lock")

    def read(self) -> DestroyReceipt | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("Destroy receipt path is not safely readable") from error
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size <= 0
                or before.st_size > _MAX_RECEIPT_BYTES
                or before.st_nlink != 1
                or before.st_mode & 0o022
                or before.st_uid != os.getuid()
            ):
                raise ValueError("Destroy receipt path has unsafe local metadata")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_nlink,
                before.st_mode,
                before.st_uid,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_nlink,
                after.st_mode,
                after.st_uid,
            )
            if remaining or len(content) != before.st_size or before_identity != after_identity:
                raise ValueError("Destroy receipt changed while it was read")
        except OSError as error:
            raise ValueError("Destroy receipt is unreadable") from error
        finally:
            os.close(descriptor)
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Destroy receipt is malformed") from error
        return DestroyReceipt.from_dict(value)

    def write_verified(
        self,
        receipt: DestroyReceipt,
        *,
        predecessor_sha256: str | None,
    ) -> None:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
            ):
                raise ValueError("Destroy receipt lock is not owner controlled")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            previous = self.read()
            if previous is None:
                if predecessor_sha256 is not None or receipt.revision != 1:
                    raise ValueError("Destroy receipt initial compare-and-swap failed")
            elif previous == receipt:
                return
            else:
                common_successor = (
                    predecessor_sha256 == previous.record_sha256
                    and receipt.predecessor_sha256 == previous.record_sha256
                    and receipt.revision == previous.revision + 1
                )
                same_operation = (
                    receipt.operation_id == previous.operation_id
                    and receipt.plan == previous.plan
                    and set(previous.completed_effects).issubset(receipt.completed_effects)
                    and previous.failed_operations
                    == receipt.failed_operations[: len(previous.failed_operations)]
                )
                fresh_after_terminal = (
                    previous.terminal
                    and receipt.operation_id != previous.operation_id
                    and receipt.plan != previous.plan
                    and receipt.pending_effect is None
                    and receipt.accepted_operation_kind is None
                    and receipt.accepted_cloud_operation_id is None
                    and not receipt.failed_operations
                    and not receipt.completed_effects
                    and not receipt.terminal
                )
                if not common_successor or not (same_operation or fresh_after_terminal):
                    raise ValueError("Destroy receipt successor compare-and-swap failed")
            payload = (_canonical_json(receipt.to_dict()) + "\n").encode("utf-8")
            if len(payload) > _MAX_RECEIPT_BYTES:
                raise ValueError("Destroy receipt exceeds the durable checkpoint size limit")
            temporary_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(temporary_descriptor, 0o600)
                with os.fdopen(temporary_descriptor, "wb") as stream:
                    temporary_descriptor = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if temporary_descriptor >= 0:
                    os.close(temporary_descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            if self.read() != receipt:
                raise RuntimeError("Destroy receipt did not verify after atomic write")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class _DestroyJournal(t.Protocol):
    plan: DestroyPlan

    @property
    def completed_effects(self) -> frozenset[str]: ...

    @property
    def pending_effect(self) -> str | None: ...

    @property
    def accepted_operation(self) -> tuple[str, str] | None: ...

    def begin(self, effect: str) -> str: ...

    def record_cloud_operation(self, effect: str, kind: str, cloud_operation_id: str) -> None: ...

    def supersede_failed_operation(
        self,
        effect: str,
        kind: str,
        cloud_operation_id: str,
    ) -> None: ...

    def complete(self, effect: str) -> None: ...

    def finish(self) -> None: ...


class OrdinaryDestroyJournal:
    """Effect journal backed by :class:`DestroyReceiptStore`."""

    def __init__(self, store: DestroyReceiptStore, receipt: DestroyReceipt) -> None:
        self.store = store
        self.receipt = receipt
        self.plan = receipt.plan

    @classmethod
    def load_or_start(
        cls,
        store: DestroyReceiptStore,
        plan: DestroyPlan,
    ) -> OrdinaryDestroyJournal:
        receipt = store.read()
        if receipt is not None:
            if receipt.plan == plan:
                return cls(store, receipt)
            if not receipt.terminal:
                raise ValueError("Destroy configuration or exact resource plan changed")
            successor = DestroyReceipt(
                operation_id=_digest({"destroy": plan.digest}),
                plan=plan,
                revision=receipt.revision + 1,
                predecessor_sha256=receipt.record_sha256,
            )
            store.write_verified(successor, predecessor_sha256=receipt.record_sha256)
            return cls(store, successor)
        receipt = DestroyReceipt(operation_id=_digest({"destroy": plan.digest}), plan=plan)
        store.write_verified(receipt, predecessor_sha256=None)
        return cls(store, receipt)

    @property
    def completed_effects(self) -> frozenset[str]:
        return frozenset(self.receipt.completed_effects)

    @property
    def pending_effect(self) -> str | None:
        return self.receipt.pending_effect

    @property
    def accepted_operation(self) -> tuple[str, str] | None:
        if self.receipt.accepted_operation_kind is None:
            return None
        return (
            self.receipt.accepted_operation_kind,
            t.cast(str, self.receipt.accepted_cloud_operation_id),
        )

    def _write(self, successor: DestroyReceipt) -> None:
        self.store.write_verified(
            successor,
            predecessor_sha256=self.receipt.record_sha256,
        )
        self.receipt = successor

    def _advance(self, **changes: t.Any) -> DestroyReceipt:
        return replace(
            self.receipt,
            revision=self.receipt.revision + 1,
            predecessor_sha256=self.receipt.record_sha256,
            **changes,
        )

    def begin(self, effect: str) -> str:
        attempt = (
            sum(
                1
                for failed_effect, _kind, _cloud_id in self.receipt.failed_operations
                if failed_effect == effect
            )
            + 1
        )
        if effect in self.completed_effects:
            return _digest(
                {
                    "attempt": attempt,
                    "effect": effect,
                    "operation": self.receipt.operation_id,
                }
            )
        if self.pending_effect not in {None, effect}:
            raise ValueError("Destroy receipt has another pending effect")
        if self.pending_effect is None:
            self._write(
                self._advance(
                    pending_effect=effect,
                    accepted_operation_kind=None,
                    accepted_cloud_operation_id=None,
                )
            )
        return _digest(
            {
                "attempt": attempt,
                "effect": effect,
                "operation": self.receipt.operation_id,
            }
        )

    def record_cloud_operation(self, effect: str, kind: str, cloud_operation_id: str) -> None:
        if self.pending_effect != effect or not kind or not cloud_operation_id:
            raise ValueError("Destroy accepted cloud operation is not bound to its effect")
        existing = self.accepted_operation
        if existing is not None:
            if existing != (kind, cloud_operation_id):
                raise ValueError("Destroy cloud operation identity changed")
            return
        self._write(
            self._advance(
                accepted_operation_kind=kind,
                accepted_cloud_operation_id=cloud_operation_id,
            )
        )

    def supersede_failed_operation(
        self,
        effect: str,
        kind: str,
        cloud_operation_id: str,
    ) -> None:
        if self.pending_effect != effect or self.accepted_operation != (
            kind,
            cloud_operation_id,
        ):
            raise ValueError("Destroy failed operation is outside its pending effect")
        self._write(
            self._advance(
                accepted_operation_kind=None,
                accepted_cloud_operation_id=None,
                failed_operations=(
                    *self.receipt.failed_operations,
                    (effect, kind, cloud_operation_id),
                ),
            )
        )

    def complete(self, effect: str) -> None:
        if effect in self.completed_effects:
            return
        if self.pending_effect != effect:
            raise ValueError("Destroy effect completion has no matching intent")
        self._write(
            self._advance(
                pending_effect=None,
                accepted_operation_kind=None,
                accepted_cloud_operation_id=None,
                completed_effects=tuple(sorted((*self.receipt.completed_effects, effect))),
            )
        )

    def finish(self) -> None:
        if self.receipt.terminal:
            return
        if self.pending_effect is not None:
            raise ValueError("Destroy cannot finish with a pending effect")
        self._write(self._advance(terminal=True))


class VMHADestroyJournal:
    """Adapter keeping HA destruction inside the canonical VM-HA lifecycle."""

    def __init__(self, journal: VMHALifecycleJournal) -> None:
        transaction = journal.state.transaction
        if transaction is None or transaction.approval_kind != "destruction":
            raise ValueError("VM-HA destroy journal requires a destruction transaction")
        plan_json = dict(transaction.resource_bindings).get("destroy-plan")
        if not plan_json:
            raise ValueError("VM-HA destruction transaction lost its exact plan")
        self.journal = journal
        self.plan = DestroyPlan.from_json(plan_json)
        if (
            self.plan.topology != "vm-ha"
            or self.plan.project_id != journal.state.project_id
            or self.plan.gateway_name != journal.state.gateway_name
            or transaction.approval_digest != self.plan.digest
        ):
            raise ValueError("VM-HA destruction plan is outside its lifecycle authority")

    @property
    def completed_effects(self) -> frozenset[str]:
        transaction = t.cast(object, self.journal.state.transaction)
        return frozenset(t.cast(t.Any, transaction).completed_effects)

    @property
    def pending_effect(self) -> str | None:
        transaction = t.cast(object, self.journal.state.transaction)
        return t.cast(str | None, t.cast(t.Any, transaction).pending_effect)

    @property
    def accepted_operation(self) -> tuple[str, str] | None:
        transaction = self.journal.state.transaction
        if transaction is None or transaction.accepted_cloud_operation_id is None:
            return None
        effect = transaction.accepted_cloud_operation_effect
        if effect is None:
            raise ValueError("VM-HA destroy accepted operation lost its effect")
        kind = effect.split("-", 2)[1]
        return kind, transaction.accepted_cloud_operation_id

    def begin(self, effect: str) -> str:
        self.journal.begin(effect)
        transaction = self.journal.state.transaction
        if transaction is None:
            raise ValueError("VM-HA destruction transaction disappeared")
        attempt = len(self._failed_operations(effect)) + 1
        return _digest(
            {
                "attempt": attempt,
                "effect": effect,
                "operation": transaction.operation_id,
            }
        )

    def _failed_operations(self, effect: str) -> tuple[str, ...]:
        transaction = self.journal.state.transaction
        if transaction is None:
            raise ValueError("VM-HA destruction transaction disappeared")
        prefix = f"destroy-failed-operation:{effect}:"
        indexed: dict[int, str] = {}
        for key, value in transaction.resource_bindings:
            if not key.startswith(prefix):
                continue
            raw_sequence = key.removeprefix(prefix)
            if not raw_sequence.isdigit() or int(raw_sequence) < 1:
                raise ValueError("VM-HA destroy failed operation history is malformed")
            sequence = int(raw_sequence)
            if sequence in indexed:
                raise ValueError("VM-HA destroy failed operation history is ambiguous")
            indexed[sequence] = value
        if sorted(indexed) != list(range(1, len(indexed) + 1)):
            raise ValueError("VM-HA destroy failed operation history is not contiguous")
        return tuple(indexed[index] for index in sorted(indexed))

    def record_cloud_operation(self, effect: str, kind: str, cloud_operation_id: str) -> None:
        expected_kind = effect.split("-", 2)[1]
        if kind != expected_kind:
            raise ValueError("VM-HA destroy operation kind changed")
        self.journal.record_cloud_operation(effect, cloud_operation_id)

    def supersede_failed_operation(
        self,
        effect: str,
        kind: str,
        cloud_operation_id: str,
    ) -> None:
        if self.pending_effect != effect or self.accepted_operation != (
            kind,
            cloud_operation_id,
        ):
            raise ValueError("VM-HA destroy failed operation is outside its pending effect")
        state = self.journal.state
        transaction = state.transaction
        if transaction is None:
            raise ValueError("VM-HA destruction transaction disappeared")
        sequence = len(self._failed_operations(effect)) + 1
        key = f"destroy-failed-operation:{effect}:{sequence}"
        successor = replace(
            state,
            transaction=transaction.advance(
                predecessor_sha256=state.record_sha256,
                checkpoint=f"failed-{effect}-{sequence}",
                pending_effect=effect,
                resource_updates={key: cloud_operation_id},
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )
        self.journal.transition(successor)

    def complete(self, effect: str) -> None:
        self.journal.complete(effect)

    def finish(self) -> None:
        if self.journal.state.status is VMHALifecycleStatus.DESTROYED:
            return
        self.journal.transition(
            self.journal.state.with_status(
                VMHALifecycleStatus.DESTROYED,
                checkpoint="destruction-verified-absent",
            )
        )


class DestroyBackend(t.Protocol):
    """Cloud boundary consumed by the deterministic coordinator."""

    def preflight(self, plan: DestroyPlan, *, require_revisions: bool = False) -> None: ...

    def instance_fenced(self, resource: DestroyResource) -> bool: ...

    def route_absent(self, route: DestroyRoute) -> bool: ...

    def resource_absent(self, resource: DestroyResource) -> bool: ...

    def submit_stop(self, resource: DestroyResource, operation_id: str) -> object | None: ...

    def submit_delete_route(self, route: DestroyRoute, operation_id: str) -> object | None: ...

    def submit_delete_resource(
        self, resource: DestroyResource, operation_id: str
    ) -> object | None: ...

    def operation_id(self, operation: object) -> str: ...

    def wait_operation(self, operation: object) -> None: ...

    def resume_operation(
        self,
        kind: str,
        cloud_operation_id: str,
    ) -> DestroyOperationResume: ...

    def stable_verification(self, plan: DestroyPlan) -> tuple[str, str]: ...


@dataclass(frozen=True)
class DestroyResult:
    topology: str
    deleted_compute: int
    deleted_disks: int
    deleted_routes: int
    deleted_allocations: int
    already_absent: bool


@dataclass(frozen=True)
class PredecessorOperationResolution:
    """One terminal predecessor operation admitted into destroy planning."""

    cloud_operation_id: str
    succeeded: bool
    resource_id: str


@dataclass(frozen=True)
class DestroyOperationResume:
    """Closed result of reading one destroy-owned accepted operation."""

    lookup_supported: bool
    succeeded: bool | None

    def __post_init__(self) -> None:
        if self.lookup_supported != (self.succeeded is not None):
            raise ValueError("Destroy operation resume outcome is inconsistent")


class DestroyCoordinator:
    """Run one ordered idempotent teardown against an exact manifest."""

    def __init__(self, backend: DestroyBackend, journal: _DestroyJournal) -> None:
        self.backend = backend
        self.journal = journal
        self._cloud_effect_executed = False

    @staticmethod
    def _effect(kind: str, identity: str) -> str:
        return f"destroy-{kind}-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    def _run_effect(
        self,
        *,
        effect: str,
        kind: str,
        reason_code: str,
        postcondition: t.Callable[[], bool],
        submit: t.Callable[[str], object | None],
    ) -> None:
        if effect in self.journal.completed_effects:
            if not self._read_postcondition(reason_code, postcondition):
                raise DestroyFailure(
                    "destroy-authority-conflict",
                    "A completed destroy effect no longer satisfies its postcondition",
                )
            return
        operation_key = self.journal.begin(effect)
        accepted = self.journal.accepted_operation
        if accepted is not None:
            accepted_kind, cloud_operation_id = accepted
            if accepted_kind != kind:
                raise DestroyFailure(
                    "destroy-authority-conflict",
                    "Destroy accepted operation kind changed",
                )
            self._cloud_effect_executed = True
            try:
                resumed = self.backend.resume_operation(kind, cloud_operation_id)
            except Exception as error:
                self._complete_after_external_error(
                    effect=effect,
                    reason_code=reason_code,
                    postcondition=postcondition,
                    error=error,
                )
                return
            if resumed.lookup_supported and resumed.succeeded is False:
                if self._read_postcondition(reason_code, postcondition):
                    self.journal.complete(effect)
                    return
                self.journal.supersede_failed_operation(
                    effect,
                    kind,
                    cloud_operation_id,
                )
                operation_key = self.journal.begin(effect)
                accepted = None
            if not resumed.lookup_supported or accepted is None:
                self._cloud_effect_executed = True
                try:
                    operation = submit(operation_key)
                except Exception as error:
                    self._complete_after_external_error(
                        effect=effect,
                        reason_code=reason_code,
                        postcondition=postcondition,
                        error=error,
                    )
                    return
                if operation is not None:
                    replayed_operation_id = self.backend.operation_id(operation)
                    if accepted is not None and replayed_operation_id != cloud_operation_id:
                        raise DestroyFailure(
                            reason_code,
                            "Destroy idempotent replay returned a different cloud operation",
                        )
                    if accepted is None:
                        self.journal.record_cloud_operation(
                            effect,
                            kind,
                            replayed_operation_id,
                        )
                    try:
                        self.backend.wait_operation(operation)
                    except Exception as error:
                        self._complete_after_external_error(
                            effect=effect,
                            reason_code=reason_code,
                            postcondition=postcondition,
                            error=error,
                        )
                        return
        elif self._read_postcondition(reason_code, postcondition):
            self.journal.complete(effect)
            return
        else:
            self._cloud_effect_executed = True
            try:
                operation = submit(operation_key)
            except Exception as error:
                self._complete_after_external_error(
                    effect=effect,
                    reason_code=reason_code,
                    postcondition=postcondition,
                    error=error,
                )
                return
            if operation is not None:
                self._cloud_effect_executed = True
                cloud_operation_id = self.backend.operation_id(operation)
                if not cloud_operation_id:
                    raise DestroyFailure(
                        reason_code,
                        "Destroy cloud mutation returned no operation identity",
                    )
                self.journal.record_cloud_operation(effect, kind, cloud_operation_id)
                try:
                    self.backend.wait_operation(operation)
                except Exception as error:
                    self._complete_after_external_error(
                        effect=effect,
                        reason_code=reason_code,
                        postcondition=postcondition,
                        error=error,
                    )
                    return
        if not self._read_postcondition(reason_code, postcondition):
            raise DestroyFailure(
                reason_code,
                "Destroy effect postcondition was not observed",
            )
        self.journal.complete(effect)

    @staticmethod
    def _read_postcondition(
        reason_code: str,
        postcondition: t.Callable[[], bool],
    ) -> bool:
        try:
            return postcondition()
        except Exception as error:
            raise DestroyFailure(
                reason_code,
                "Destroy effect postcondition could not be classified",
            ) from error

    def _complete_after_external_error(
        self,
        *,
        effect: str,
        reason_code: str,
        postcondition: t.Callable[[], bool],
        error: Exception,
    ) -> None:
        """Accept an error only when the exact effect postcondition is already true."""

        completed = self._read_postcondition(reason_code, postcondition)
        if not completed:
            raise DestroyFailure(
                reason_code,
                "Destroy effect failed before its exact postcondition was observed",
            ) from error
        self.journal.complete(effect)

    def run(self) -> DestroyResult:
        plan = self.journal.plan
        if plan.blockers:
            raise DestroyFailure(
                "destroy-authority-conflict",
                "Destroy is blocked: " + "; ".join(plan.blockers),
            )
        self.backend.preflight(plan)
        for resource in plan.compute:

            def fenced(resource: DestroyResource = resource) -> bool:
                return self.backend.instance_fenced(resource)

            def stop(
                operation_id: str,
                resource: DestroyResource = resource,
            ) -> object | None:
                return self.backend.submit_stop(resource, operation_id)

            self._run_effect(
                effect=self._effect("compute-stop", resource.resource_id),
                kind="compute",
                reason_code="destroy-compute-fence-failed",
                postcondition=fenced,
                submit=stop,
            )
        for route in plan.routes:

            def route_absent(route: DestroyRoute = route) -> bool:
                return self.backend.route_absent(route)

            def delete_route(
                operation_id: str,
                route: DestroyRoute = route,
            ) -> object | None:
                return self.backend.submit_delete_route(route, operation_id)

            self._run_effect(
                effect=self._effect("route-delete", route.route_id),
                kind="route",
                reason_code="destroy-route-delete-failed",
                postcondition=route_absent,
                submit=delete_route,
            )
        for resource in plan.compute:

            def compute_absent(resource: DestroyResource = resource) -> bool:
                return self.backend.resource_absent(resource)

            def delete_compute(
                operation_id: str,
                resource: DestroyResource = resource,
            ) -> object | None:
                return self.backend.submit_delete_resource(resource, operation_id)

            self._run_effect(
                effect=self._effect("compute-delete", resource.resource_id),
                kind="compute",
                reason_code="destroy-compute-delete-failed",
                postcondition=compute_absent,
                submit=delete_compute,
            )
        for resource in plan.disks:

            def disk_absent(resource: DestroyResource = resource) -> bool:
                return self.backend.resource_absent(resource)

            def delete_disk(
                operation_id: str,
                resource: DestroyResource = resource,
            ) -> object | None:
                return self.backend.submit_delete_resource(resource, operation_id)

            self._run_effect(
                effect=self._effect("disk-delete", resource.resource_id),
                kind="disk",
                reason_code="destroy-disk-delete-failed",
                postcondition=disk_absent,
                submit=delete_disk,
            )
        # Re-read all exact name and route bindings immediately before private
        # allocation deletion. This closes the window in which a same-name
        # replacement or new foreign route could otherwise be adopted.
        for resource in plan.allocations:
            self.backend.preflight(plan)

            def allocation_absent(resource: DestroyResource = resource) -> bool:
                return self.backend.resource_absent(resource)

            def delete_allocation(
                operation_id: str,
                resource: DestroyResource = resource,
            ) -> object | None:
                return self.backend.submit_delete_resource(resource, operation_id)

            self._run_effect(
                effect=self._effect("allocation-delete", resource.resource_id),
                kind="allocation",
                reason_code="destroy-allocation-delete-failed",
                postcondition=allocation_absent,
                submit=delete_allocation,
            )
        try:
            first, second = self.backend.stable_verification(plan)
        except Exception as error:
            raise DestroyFailure(
                "destroy-terminal-verification-failed",
                "Destroy terminal verification could not prove exact absence",
            ) from error
        if first != second:
            raise DestroyFailure(
                "destroy-terminal-verification-failed",
                "Destroy verification changed between fresh observations",
            )
        self.journal.finish()
        return DestroyResult(
            topology=plan.topology,
            deleted_compute=len(plan.compute),
            deleted_disks=len(plan.disks),
            deleted_routes=len(plan.routes),
            deleted_allocations=len(plan.allocations),
            already_absent=not self._cloud_effect_executed,
        )


class NebiusDestroyBackend:
    """Strict synchronous Nebius SDK adapter for one gateway destroy scope."""

    def __init__(
        self,
        vm_manager: object,
        spec: GatewayGroupSpec,
        *,
        project_id: str,
        lifecycle_state: VMHALifecycleState | None,
        resolved_predecessor_effect: str | None = None,
        resolved_predecessor_resource_id: str | None = None,
    ) -> None:
        client_factory = getattr(vm_manager, "_get_client", None)
        if not callable(client_factory):
            raise RuntimeError("Destroy requires a Nebius VM manager")
        self.client = client_factory()
        if self.client is None:
            raise RuntimeError("Nebius SDK client is unavailable for destroy")
        self.vm_manager = vm_manager
        self.spec = spec
        self.project_id = project_id
        self.lifecycle_state = lifecycle_state
        self.resolved_predecessor_effect = resolved_predecessor_effect
        self.resolved_predecessor_resource_id = resolved_predecessor_resource_id
        if (resolved_predecessor_effect is None) != (resolved_predecessor_resource_id is None):
            raise ValueError("Resolved predecessor destroy authority is incomplete")

        from nebius.api.nebius.compute.v1 import (  # type: ignore
            DiskServiceClient,
            InstanceServiceClient,
        )
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationServiceClient,
            RouteServiceClient,
            RouteTableServiceClient,
        )

        self.instances = InstanceServiceClient(self.client)
        self.disks = DiskServiceClient(self.client)
        self.allocations = AllocationServiceClient(self.client)
        self.routes = RouteServiceClient(self.client)
        self.route_tables = RouteTableServiceClient(self.client)

    @staticmethod
    def _wait(value: object) -> object:
        waiter = getattr(value, "wait", None)
        return waiter() if callable(waiter) else value

    @staticmethod
    def _not_found(error: BaseException) -> bool:
        return nebius_request_error_code_is(error, "NOT_FOUND")

    def _get_instance_by_name(self, name: str) -> object | None:
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

        try:
            value = self._wait(
                self.instances.get_by_name(
                    GetByNameRequest(parent_id=self.project_id, name=name),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(f"Compute {name} could not be classified") from error
        if (
            not _resource_id(value)
            or _resource_name(value) != name
            or _resource_parent_id(value) != self.project_id
        ):
            raise RuntimeError(f"Compute {name} returned an inexact identity")
        return value

    def _get_instance_by_id(self, resource_id: str) -> object | None:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest  # type: ignore

        try:
            return self._wait(
                self.instances.get(
                    GetInstanceRequest(id=resource_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError("Compute identity could not be classified") from error

    def _get_disk_by_name(self, name: str) -> object | None:
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

        try:
            value = self._wait(
                self.disks.get_by_name(
                    GetByNameRequest(parent_id=self.project_id, name=name),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(f"Disk {name} could not be classified") from error
        if (
            not _resource_id(value)
            or _resource_name(value) != name
            or _resource_parent_id(value) != self.project_id
        ):
            raise RuntimeError(f"Disk {name} returned an inexact identity")
        return value

    def _get_disk_by_id(self, resource_id: str) -> object | None:
        from nebius.api.nebius.compute.v1 import GetDiskRequest  # type: ignore

        try:
            return self._wait(
                self.disks.get(
                    GetDiskRequest(id=resource_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError("Disk identity could not be classified") from error

    def _get_allocation_by_name(self, name: str) -> object | None:
        from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore

        try:
            value = self._wait(
                self.allocations.get_by_name(
                    GetAllocationByNameRequest(parent_id=self.project_id, name=name),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(f"Allocation {name} could not be classified") from error
        if (
            not _resource_id(value)
            or _resource_name(value) != name
            or _resource_parent_id(value) != self.project_id
        ):
            raise RuntimeError(f"Allocation {name} returned an inexact identity")
        return value

    def _get_allocation_by_id(self, resource_id: str) -> object | None:
        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

        try:
            return self._wait(
                self.allocations.get(
                    GetAllocationRequest(id=resource_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError("Allocation identity could not be classified") from error

    @classmethod
    def _list_items(
        cls,
        client: object,
        request_type: t.Callable[..., object],
        *,
        parent_id: str,
    ) -> tuple[object, ...]:
        return collect_nebius_pages(
            lambda page_token: t.cast(t.Any, client).list(
                request_type(
                    parent_id=parent_id,
                    page_size=1000,
                    page_token=page_token,
                ),
                **vm_ha_request_kwargs(),
            ),
            context="Destroy resource",
            item_identity=nebius_resource_id,
        )

    def _route_inventory(self) -> tuple[tuple[str, object], ...]:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            ListRoutesRequest,
            ListRouteTablesRequest,
        )

        tables = self._list_items(
            self.route_tables,
            ListRouteTablesRequest,
            parent_id=self.project_id,
        )
        rows: list[tuple[str, object]] = []
        for table in tables:
            table_id = _resource_id(table)
            if not table_id:
                raise RuntimeError("Route table inventory contains an identity-less table")
            routes = self._list_items(
                self.routes,
                ListRoutesRequest,
                parent_id=table_id,
            )
            rows.extend((table_id, route) for route in routes)
        return tuple(rows)

    @staticmethod
    def _route_allocation_id(route: object) -> str:
        spec = getattr(route, "spec", None)
        next_hop = getattr(spec, "next_hop", None) if spec is not None else None
        allocation = getattr(next_hop, "allocation", None) if next_hop is not None else None
        return str(getattr(allocation, "id", "") or "")

    @staticmethod
    def _route_labels(route: object) -> dict[str, str]:
        labels = getattr(getattr(route, "metadata", None), "labels", {}) or {}
        if not isinstance(labels, t.Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()
        ):
            raise RuntimeError("Route labels are malformed")
        return dict(labels)

    def _ha_route_is_owned(self, route: object, route_table_id: str, allocation_id: str) -> bool:
        state = self.lifecycle_state
        if state is None:
            return False
        from nebius_vpngw.schema import VMHARouteTarget

        from .route_manager import NebiusSDKRouteBackend

        labels = self._route_labels(route)
        authority_labels = set(labels) & NebiusSDKRouteBackend._AUTHORITY_LABEL_KEYS
        if authority_labels and not (labels.keys() >= NebiusSDKRouteBackend._AUTHORITY_LABEL_KEYS):
            raise RuntimeError("VM-HA route has partial authority labels")
        if authority_labels:
            if allocation_id != state.allocation_id:
                return False
            for raw_target in state.route_targets:
                try:
                    target = VMHARouteTarget.model_validate(json.loads(raw_target))
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError("VM-HA route target is malformed") from error
                if target.route_table_id != route_table_id:
                    continue
                for kind in ("bgp", "static"):
                    expected = NebiusSDKRouteBackend._authority_labels(
                        cluster_id=state.cluster_id,
                        allocation_id=state.allocation_id,
                        route_target=target,
                        route_kind=kind,
                    )
                    if labels == expected:
                        return True
            return False

        _compute, _disks, private_allocations, _public = self._ha_expected_ids()
        if allocation_id not in {value for value in private_allocations.values() if value}:
            return False
        metadata = getattr(route, "metadata", None)
        name = str(getattr(metadata, "name", "") or "")
        spec = getattr(route, "spec", None)
        destination = getattr(spec, "destination", None) if spec is not None else None
        cidr = str(getattr(destination, "cidr", "") or "")
        if not cidr:
            return False
        return name == f"vpngw-{cidr.replace('/', '-')}"[:63]

    @staticmethod
    def _instance_boot_disk_id(instance: object | None) -> str:
        spec = getattr(instance, "spec", None)
        boot = getattr(spec, "boot_disk", None) if spec is not None else None
        existing = getattr(boot, "existing_disk", None) if boot is not None else None
        return str(getattr(existing, "id", "") or getattr(boot, "disk_id", "") or "")

    @staticmethod
    def _instance_allocations(instance: object | None) -> tuple[tuple[str, str], ...]:
        interfaces = list(getattr(getattr(instance, "spec", None), "network_interfaces", []) or [])
        values: list[tuple[str, str]] = []
        for index, interface in enumerate(interfaces):
            name = str(getattr(interface, "name", "") or f"eth{index}")
            primary = str(
                getattr(getattr(interface, "ip_address", None), "allocation_id", "") or ""
            )
            public = str(
                getattr(
                    getattr(interface, "public_ip_address", None),
                    "allocation_id",
                    "",
                )
                or ""
            )
            if primary:
                values.append((f"primary:{name}", primary))
            if public:
                values.append((f"public:{name}", public))
        return tuple(values)

    @staticmethod
    def _allocation_shape(allocation: object) -> str:
        from .vm_manager import _protobuf_field_present

        spec = getattr(allocation, "spec", None)
        private = _protobuf_field_present(spec, "ipv4_private")
        public = _protobuf_field_present(spec, "ipv4_public")
        if private == public:
            return "unknown"
        return "private" if private else "public"

    def _ha_expected_ids(
        self,
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
        state = self.lifecycle_state
        if state is None or state.transaction is None:
            return {}, {}, {}, {}
        bindings = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
        compute: dict[str, str] = {}
        disks: dict[str, str] = {}
        allocations: dict[str, str] = {}
        public_allocations: dict[str, str] = {}
        for member in state.members:
            compute[member.instance_name] = bindings.get(
                f"compute:{member.instance_name}", member.compute_id
            )
            disks[f"{member.instance_name}-boot"] = bindings.get(
                f"disk:{member.instance_name}", member.disk_id
            )
            allocations[f"{member.instance_name}-eth0-private-ip"] = bindings.get(
                f"primary-allocation:{member.instance_name}:eth0",
                member.primary_allocation_id,
            )
            public_allocations[member.instance_name] = bindings.get(
                f"public-allocation:{member.instance_name}:eth0",
                member.public_allocation_id,
            )
        allocations[state.allocation_name] = bindings.get(
            "shared-allocation-id", state.allocation_id
        )
        self._apply_resolved_predecessor_authority(
            compute,
            disks,
            allocations,
            public_allocations,
        )
        return compute, disks, allocations, public_allocations

    def _apply_resolved_predecessor_authority(
        self,
        compute: dict[str, str],
        disks: dict[str, str],
        allocations: dict[str, str],
        public_allocations: dict[str, str],
    ) -> None:
        """Adopt only the exact result of the predecessor's accepted operation."""

        effect = self.resolved_predecessor_effect
        resource_id = self.resolved_predecessor_resource_id
        state = self.lifecycle_state
        if effect is None or resource_id is None or state is None:
            return
        member = next(
            (
                item.instance_name
                for item in state.members
                if f"-{item.instance_name}-" in f"-{effect}-"
            ),
            None,
        )
        if effect == "provision-shared-allocation":
            allocations[state.allocation_name] = resource_id
        elif effect.endswith("-public-allocation") and member is not None:
            public_allocations[member] = resource_id
        elif effect.endswith("-primary-allocation") and member is not None:
            matched = re.search(r"-(eth[0-9]+)-primary-allocation$", effect)
            if matched is None:
                raise RuntimeError("Resolved VM-HA allocation effect is malformed")
            allocations[f"{member}-{matched.group(1)}-private-ip"] = resource_id
        elif effect.endswith(("-boot-disk", "-create-boot-disk")) and member is not None:
            disks[f"{member}-boot"] = resource_id
        elif effect.endswith("-compute") and member is not None:
            compute[member] = resource_id

    def _ha_authoritative_disk_ids(self, current: t.Mapping[str, str]) -> frozenset[str]:
        state = self.lifecycle_state
        if state is None or state.transaction is None:
            return frozenset(value for value in current.values() if value)
        disk_binding = re.compile(
            r"^(?:disk:|retired(?:-[2-9][0-9]*)?-disk:|"
            r"replacement(?:-[2-9][0-9]*)?-disk:)"
        )
        return frozenset(
            {
                *(value for value in current.values() if value),
                *(
                    value
                    for key, value in state.transaction.resource_bindings
                    if disk_binding.match(key)
                ),
            }
        )

    def _ha_authoritative_compute_ids(
        self,
        current: t.Mapping[str, str],
    ) -> dict[str, str]:
        state = self.lifecycle_state
        if state is None or state.transaction is None:
            return {resource_id: name for name, resource_id in current.items() if resource_id}
        compute_binding = re.compile(
            r"^(?:compute|retired(?:-[2-9][0-9]*)?-compute|"
            r"replacement(?:-[2-9][0-9]*)?-compute):(.+)$"
        )
        member_names = {member.instance_name for member in state.members}
        authoritative = {resource_id: name for name, resource_id in current.items() if resource_id}
        for key, resource_id in state.transaction.resource_bindings:
            matched = compute_binding.fullmatch(key)
            if matched is None:
                continue
            name = matched.group(1)
            if name not in member_names:
                raise RuntimeError("VM-HA Compute binding names a foreign member")
            existing_name = authoritative.get(resource_id)
            if existing_name not in {None, name}:
                raise RuntimeError("VM-HA Compute identity is bound to multiple members")
            authoritative[resource_id] = name
        return authoritative

    def build_plan(self, config_digest: str) -> DestroyPlan:
        ha = self.spec.vm_ha is not None
        topology = (
            "vm-ha"
            if ha and self.lifecycle_state is not None
            else ("vm-ha-unbound" if ha else "ordinary")
        )
        count = 2 if ha else self.spec.instance_count
        compute_names = tuple(sorted(f"{self.spec.name}-{index}" for index in range(count)))
        configured_disk_names = tuple(sorted(f"{name}-boot" for name in compute_names))
        nic_count = int(self.spec.vm_spec.get("num_nics") or 1)
        allocation_names = {
            f"{name}-eth{nic}-private-ip" for name in compute_names for nic in range(nic_count)
        }
        if ha and self.spec.vm_ha is not None:
            allocation_names.add(f"{self.spec.name}-{self.spec.vm_ha.cluster_id}-shared-private-ip")

        (
            expected_compute_ids,
            expected_disk_ids,
            expected_allocation_ids,
            expected_public_allocation_ids,
        ) = self._ha_expected_ids() if ha and self.lifecycle_state is not None else ({}, {}, {}, {})
        authoritative_compute_ids = (
            self._ha_authoritative_compute_ids(expected_compute_ids)
            if ha and self.lifecycle_state is not None
            else {}
        )
        authoritative_disk_ids = (
            self._ha_authoritative_disk_ids(expected_disk_ids)
            if ha and self.lifecycle_state is not None
            else frozenset()
        )
        compute: list[DestroyResource] = []
        disks: list[DestroyResource] = []
        allocations: dict[str, DestroyResource] = {}
        public: dict[str, DestroyResource] = {}
        blockers: list[str] = []

        for name in compute_names:
            instance = self._get_instance_by_name(name)
            expected_id = expected_compute_ids.get(name, "")
            if instance is None:
                if expected_id and self._get_instance_by_id(expected_id) is not None:
                    raise RuntimeError(f"Compute {name} changed name")
                continue
            actual_id = _resource_id(instance)
            if ha and self.lifecycle_state is not None and not expected_id:
                blockers.append(f"unbound VM-HA Compute occupies {name}")
                continue
            if ha and self.lifecycle_state is not None:
                if authoritative_compute_ids.get(actual_id) != name:
                    blockers.append(f"VM-HA Compute identity changed for {name}")
                    continue
            elif expected_id and actual_id != expected_id:
                blockers.append(f"VM-HA Compute identity changed for {name}")
                continue
            compute.append(
                DestroyResource("compute", name, actual_id, _resource_revision(instance))
            )
            boot_id = self._instance_boot_disk_id(instance)
            if boot_id and ha and self.lifecycle_state is not None:
                if boot_id not in authoritative_disk_ids:
                    blockers.append(f"boot disk identity changed for {name}")
            elif boot_id:
                expected_disk_ids[f"{name}-boot"] = expected_disk_ids.get(f"{name}-boot", boot_id)
                if expected_disk_ids[f"{name}-boot"] != boot_id:
                    blockers.append(f"boot disk identity changed for {name}")
            for allocation_kind, allocation_id in self._instance_allocations(instance):
                allocation = self._get_allocation_by_id(allocation_id)
                if allocation is None:
                    blockers.append(f"attached allocation disappeared from {name}")
                    continue
                expected_shape = "public" if allocation_kind.startswith("public:") else "private"
                if self._allocation_shape(allocation) != expected_shape:
                    blockers.append(f"attached allocation shape changed for {name}")
                    continue
                resource = DestroyResource(
                    "public-allocation" if allocation_kind.startswith("public:") else "allocation",
                    _resource_name(allocation) or f"{name}-{allocation_kind.split(':', 1)[1]}",
                    allocation_id,
                    _resource_revision(allocation),
                )
                if resource.kind == "public-allocation":
                    public[resource.resource_id] = resource
                else:
                    expected_allocation_id = expected_allocation_ids.get(resource.name, "")
                    if resource.name not in allocation_names:
                        blockers.append(f"unconfigured private allocation is attached to {name}")
                        continue
                    if (
                        ha
                        and self.lifecycle_state is not None
                        and (
                            not expected_allocation_id
                            or expected_allocation_id != resource.resource_id
                        )
                    ):
                        blockers.append(
                            f"VM-HA private allocation identity changed for {resource.name}"
                        )
                        continue
                    allocations[resource.resource_id] = resource

        if ha and self.lifecycle_state is not None:
            planned_compute_ids = {resource.resource_id for resource in compute}
            for authoritative_id, bound_name in sorted(authoritative_compute_ids.items()):
                if authoritative_id in planned_compute_ids:
                    continue
                instance = self._get_instance_by_id(authoritative_id)
                if instance is None:
                    continue
                if _resource_name(instance) != bound_name:
                    blockers.append("lifecycle-bound Compute changed its exact name")
                    continue
                by_name = self._get_instance_by_name(bound_name)
                if by_name is None or _resource_id(by_name) != authoritative_id:
                    blockers.append(f"Compute name {bound_name} is occupied by another resource")
                    continue
                compute.append(
                    DestroyResource(
                        "compute",
                        bound_name,
                        authoritative_id,
                        _resource_revision(instance),
                    )
                )
                planned_compute_ids.add(authoritative_id)

        expected_disk_names = set(configured_disk_names)
        if ha and self.lifecycle_state is not None:
            for disk_id in sorted(authoritative_disk_ids):
                disk = self._get_disk_by_id(disk_id)
                if disk is None:
                    continue
                actual_name = _resource_name(disk)
                if not actual_name:
                    blockers.append("lifecycle-bound boot disk has no exact name")
                    continue
                expected_disk_names.add(actual_name)
                by_name = self._get_disk_by_name(actual_name)
                if by_name is None or _resource_id(by_name) != disk_id:
                    blockers.append(f"boot disk name binding changed for {actual_name}")
                    continue
                disks.append(
                    DestroyResource("disk", actual_name, disk_id, _resource_revision(disk))
                )
            for name in configured_disk_names:
                by_name = self._get_disk_by_name(name)
                if by_name is not None and _resource_id(by_name) not in authoritative_disk_ids:
                    blockers.append(f"unbound VM-HA disk occupies {name}")
        else:
            for name in configured_disk_names:
                expected_id = expected_disk_ids.get(name, "")
                disk = (
                    self._get_disk_by_id(expected_id)
                    if expected_id
                    else self._get_disk_by_name(name)
                )
                if disk is None:
                    by_name = self._get_disk_by_name(name) if expected_id else None
                    if by_name is not None:
                        blockers.append(f"boot disk name {name} was reused")
                    continue
                actual_id = _resource_id(disk)
                actual_name = _resource_name(disk)
                if not actual_name:
                    blockers.append(f"boot disk for {name} has no exact name")
                    continue
                expected_disk_names.add(actual_name)
                if expected_id and actual_id != expected_id:
                    blockers.append(f"boot disk identity changed for {name}")
                    continue
                by_actual_name = self._get_disk_by_name(actual_name)
                if by_actual_name is None or _resource_id(by_actual_name) != actual_id:
                    blockers.append(f"boot disk name binding changed for {actual_name}")
                    continue
                by_configured_name = self._get_disk_by_name(name)
                if by_configured_name is not None and _resource_id(by_configured_name) != actual_id:
                    blockers.append(f"boot disk name {name} is occupied by another resource")
                    continue
                disks.append(
                    DestroyResource("disk", actual_name, actual_id, _resource_revision(disk))
                )

        for name in sorted(allocation_names):
            allocation = self._get_allocation_by_name(name)
            expected_id = expected_allocation_ids.get(name, "")
            if allocation is None:
                if expected_id and self._get_allocation_by_id(expected_id) is not None:
                    blockers.append(f"private allocation {name} changed name")
                continue
            actual_id = _resource_id(allocation)
            if self._allocation_shape(allocation) != "private":
                blockers.append(f"private allocation shape changed for {name}")
                continue
            if ha and self.lifecycle_state is not None and not expected_id:
                blockers.append(f"unbound VM-HA allocation occupies {name}")
                continue
            if expected_id and actual_id != expected_id:
                blockers.append(f"VM-HA allocation identity changed for {name}")
                continue
            allocations[actual_id] = DestroyResource(
                "allocation", name, actual_id, _resource_revision(allocation)
            )

        for member_name, allocation_id in sorted(expected_public_allocation_ids.items()):
            if not allocation_id or allocation_id in public:
                continue
            allocation = self._get_allocation_by_id(allocation_id)
            if allocation is None:
                continue
            if self._allocation_shape(allocation) != "public":
                blockers.append(f"retained public allocation shape changed for {member_name}")
                continue
            actual_name = _resource_name(allocation)
            if not actual_name:
                blockers.append(f"retained public allocation for {member_name} has no exact name")
                continue
            by_name = self._get_allocation_by_name(actual_name)
            if by_name is None or _resource_id(by_name) != allocation_id:
                blockers.append(
                    f"retained public allocation name binding changed for {member_name}"
                )
                continue
            public[allocation_id] = DestroyResource(
                "public-allocation",
                actual_name,
                allocation_id,
                _resource_revision(allocation),
            )

        target_allocation_ids = frozenset(allocations)
        routes: list[DestroyRoute] = []
        retained_route_tables: set[str] = set()
        for table_id, route in self._route_inventory():
            allocation_id = self._route_allocation_id(route)
            if allocation_id not in target_allocation_ids:
                continue
            route_id = _resource_id(route)
            route_name = _resource_name(route)
            owned = (
                self._ha_route_is_owned(route, table_id, allocation_id)
                if ha and self.lifecycle_state is not None
                else route_name.startswith("vpngw-")
            )
            retained_route_tables.add(table_id)
            if not owned:
                blockers.append(
                    f"foreign route {route_name or route_id or '<unknown>'} references a gateway allocation"
                )
                continue
            if not route_id:
                blockers.append("managed gateway route has no exact identity")
                continue
            routes.append(
                DestroyRoute(
                    route_id=route_id,
                    name=route_name,
                    route_table_id=table_id,
                    allocation_id=allocation_id,
                    revision=_resource_revision(route),
                )
            )

        return DestroyPlan(
            project_id=self.project_id,
            gateway_name=self.spec.name,
            topology=topology,
            config_digest=config_digest,
            expected_compute_names=compute_names,
            expected_disk_names=tuple(sorted(expected_disk_names)),
            expected_allocation_names=tuple(sorted(allocation_names)),
            compute=tuple(sorted(compute, key=lambda item: (item.name, item.resource_id))),
            disks=tuple(sorted(disks, key=lambda item: (item.name, item.resource_id))),
            allocations=tuple(
                sorted(allocations.values(), key=lambda item: (item.name, item.resource_id))
            ),
            routes=tuple(sorted(routes, key=lambda item: (item.route_table_id, item.route_id))),
            retained_public_allocations=tuple(
                sorted(public.values(), key=lambda item: (item.name, item.resource_id))
            ),
            retained_route_table_ids=tuple(sorted(retained_route_tables)),
            blockers=tuple(sorted(set(blockers))),
        )

    def _strict_resource(self, resource: DestroyResource) -> object | None:
        if resource.kind == "compute":
            observed = self._get_instance_by_id(resource.resource_id)
            by_name = self._get_instance_by_name(resource.name)
        elif resource.kind == "disk":
            observed = self._get_disk_by_id(resource.resource_id)
            by_name = self._get_disk_by_name(resource.name)
        else:
            observed = self._get_allocation_by_id(resource.resource_id)
            by_name = self._get_allocation_by_name(resource.name)
        if observed is None:
            if by_name is not None:
                raise RuntimeError(f"{resource.kind} name {resource.name} was reused")
            return None
        if (
            _resource_id(observed) != resource.resource_id
            or _resource_name(observed) != resource.name
        ):
            raise RuntimeError(f"{resource.kind} identity changed for {resource.name}")
        if by_name is None or _resource_id(by_name) != resource.resource_id:
            raise RuntimeError(f"{resource.kind} name binding changed for {resource.name}")
        if resource.kind in {"allocation", "public-allocation"}:
            expected_shape = "private" if resource.kind == "allocation" else "public"
            if self._allocation_shape(observed) != expected_shape:
                raise RuntimeError(f"{resource.kind} shape changed for {resource.name}")
        return observed

    def _validate_route_identity(self, route: DestroyRoute, observed: object) -> None:
        metadata = getattr(observed, "metadata", None)
        parent_id = str(getattr(metadata, "parent_id", "") or "")
        if (
            _resource_id(observed) != route.route_id
            or _resource_name(observed) != route.name
            or self._route_allocation_id(observed) != route.allocation_id
            or (parent_id and parent_id != route.route_table_id)
            or (route.revision and _resource_revision(observed) != route.revision)
        ):
            raise RuntimeError("Route identity changed during destroy")
        owned = (
            self._ha_route_is_owned(observed, route.route_table_id, route.allocation_id)
            if self.lifecycle_state is not None
            else route.name.startswith("vpngw-")
        )
        if not owned:
            raise RuntimeError("Route ownership changed during destroy")

    def _route_by_id(self, route: DestroyRoute) -> object | None:
        from nebius.api.nebius.vpc.v1 import GetRouteRequest  # type: ignore

        try:
            observed = self._wait(
                self.routes.get(
                    GetRouteRequest(id=route.route_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError("Route identity could not be classified") from error
        self._validate_route_identity(route, observed)
        return observed

    def preflight(self, plan: DestroyPlan, *, require_revisions: bool = False) -> None:
        if plan.project_id != self.project_id or plan.gateway_name != self.spec.name:
            raise RuntimeError("Destroy plan scope changed")
        planned_names = {
            "compute": {resource.name for resource in plan.compute},
            "disk": {resource.name for resource in plan.disks},
            "allocation": {resource.name for resource in plan.allocations},
        }
        getters: dict[str, t.Callable[[str], object | None]] = {
            "compute": self._get_instance_by_name,
            "disk": self._get_disk_by_name,
            "allocation": self._get_allocation_by_name,
        }
        for kind, expected_names in (
            ("compute", plan.expected_compute_names),
            ("disk", plan.expected_disk_names),
            ("allocation", plan.expected_allocation_names),
        ):
            for name in expected_names:
                if name not in planned_names[kind] and getters[kind](name) is not None:
                    raise RuntimeError(f"{kind} name {name} appeared after destroy planning")
        for resource in (*plan.compute, *plan.disks, *plan.allocations):
            observed = self._strict_resource(resource)
            if (
                require_revisions
                and observed is not None
                and resource.revision
                and (_resource_revision(observed) != resource.revision)
            ):
                raise RuntimeError(f"{resource.kind} revision changed for {resource.name}")
        for public in plan.retained_public_allocations:
            if self._strict_resource(public) is None:
                raise RuntimeError(f"retained public allocation disappeared: {public.name}")
        for route in plan.routes:
            observed = self._route_by_id(route)
            if (
                require_revisions
                and observed is not None
                and route.revision
                and _resource_revision(observed) != route.revision
            ):
                raise RuntimeError(f"route revision changed for {route.name or route.route_id}")
        planned_routes = {route.route_id for route in plan.routes}
        allocation_ids = {resource.resource_id for resource in plan.allocations}
        for _table_id, observed in self._route_inventory():
            if (
                self._route_allocation_id(observed) in allocation_ids
                and _resource_id(observed) not in planned_routes
            ):
                raise RuntimeError(
                    "An unplanned route references a private allocation selected for destroy"
                )

    def instance_fenced(self, resource: DestroyResource) -> bool:
        instance = self._strict_resource(resource)
        if instance is None:
            return True
        from .vm_ha_cloud import InstanceCloudState, instance_cloud_state

        return instance_cloud_state(instance) is InstanceCloudState.STOPPED

    def route_absent(self, route: DestroyRoute) -> bool:
        return self._route_by_id(route) is None

    def resource_absent(self, resource: DestroyResource) -> bool:
        observed = self._strict_resource(resource)
        if observed is None:
            return True
        if resource.kind == "allocation":
            from .vm_ha_cloud import allocation_observation

            owner = allocation_observation(resource.resource_id, observed).owner
            if owner is not None:
                raise RuntimeError(
                    f"private allocation {resource.name} remains assigned to Compute"
                )
        return False

    def submit_stop(self, resource: DestroyResource, operation_id: str) -> object | None:
        if self.instance_fenced(resource):
            return None
        from nebius.api.nebius.compute.v1 import DeleteInstanceRequest  # type: ignore

        try:
            return self._wait(
                self.instances.delete(
                    DeleteInstanceRequest(id=resource.resource_id),
                    **vm_ha_request_kwargs(operation_id),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(f"Compute {resource.name} could not be fenced") from error

    def submit_delete_route(self, route: DestroyRoute, operation_id: str) -> object | None:
        if self.route_absent(route):
            return None
        from nebius.api.nebius.vpc.v1 import DeleteRouteRequest  # type: ignore

        try:
            return self._wait(
                self.routes.delete(
                    DeleteRouteRequest(id=route.route_id),
                    **vm_ha_request_kwargs(operation_id),
                )
            )
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(
                f"Route {route.name or route.route_id} could not be deleted"
            ) from error

    def submit_delete_resource(self, resource: DestroyResource, operation_id: str) -> object | None:
        if self.resource_absent(resource):
            return None
        if resource.kind == "compute":
            from nebius.api.nebius.compute.v1 import DeleteInstanceRequest  # type: ignore

            call = self.instances.delete
            request: object = DeleteInstanceRequest(id=resource.resource_id)
        elif resource.kind == "disk":
            from nebius.api.nebius.compute.v1 import DeleteDiskRequest  # type: ignore

            call = self.disks.delete
            request = DeleteDiskRequest(id=resource.resource_id)
        elif resource.kind == "allocation":
            from nebius.api.nebius.vpc.v1 import DeleteAllocationRequest  # type: ignore

            call = self.allocations.delete
            request = DeleteAllocationRequest(id=resource.resource_id)
        else:
            raise ValueError("Destroy cannot delete a retained public allocation")
        try:
            return self._wait(call(request, **vm_ha_request_kwargs(operation_id)))
        except Exception as error:
            if self._not_found(error):
                return None
            raise RuntimeError(f"{resource.kind} {resource.name} could not be deleted") from error

    @staticmethod
    def operation_id(operation: object) -> str:
        return str(getattr(operation, "id", "") or "")

    @staticmethod
    def wait_operation(operation: object) -> None:
        try:
            wait_vm_ha_operation(operation)
        except Exception as error:
            raise RuntimeError("Destroy cloud operation wait failed") from error
        successful = getattr(operation, "successful", None)
        if callable(successful) and not successful():
            raise RuntimeError("Destroy cloud operation did not succeed")

    def _operation_service(self, kind: str) -> object:
        services = {
            "allocation": self.allocations,
            "compute": self.instances,
            "disk": self.disks,
            "route": self.routes,
        }
        try:
            factory = t.cast(t.Any, services[kind]).operation_service
        except (KeyError, AttributeError) as error:
            raise RuntimeError("Destroy operation service binding is unavailable") from error
        return factory()

    def resume_operation(
        self,
        kind: str,
        cloud_operation_id: str,
    ) -> DestroyOperationResume:
        from nebius.api.nebius.common.v1 import GetOperationRequest  # type: ignore

        service = self._operation_service(kind)
        try:
            operation = self._wait(
                t.cast(t.Any, service).get(
                    GetOperationRequest(id=cloud_operation_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            if operation_status_lookup_unsupported(error):
                return DestroyOperationResume(
                    lookup_supported=False,
                    succeeded=None,
                )
            raise RuntimeError("Destroy accepted cloud operation could not be resumed") from error
        try:
            wait_vm_ha_operation(operation)
        except Exception as error:
            done = getattr(operation, "done", None)
            if not callable(done) or not done():
                raise RuntimeError("Destroy accepted cloud operation remains ambiguous") from error
        done = getattr(operation, "done", None)
        successful = getattr(operation, "successful", None)
        if not callable(done) or not done() or not callable(successful):
            raise RuntimeError("Destroy accepted cloud operation is not terminal")
        try:
            succeeded = bool(successful())
        except Exception as error:
            raise RuntimeError("Destroy accepted cloud operation status is unavailable") from error
        return DestroyOperationResume(
            lookup_supported=True,
            succeeded=succeeded,
        )

    def resume_prior_vm_ha_operation(
        self,
        effect: str,
        cloud_operation_id: str,
    ) -> PredecessorOperationResolution:
        service_factory = getattr(self.vm_manager, "_vm_ha_operation_service", None)
        if not callable(service_factory):
            raise RuntimeError("VM-HA accepted operation resolver is unavailable")
        from nebius.api.nebius.common.v1 import GetOperationRequest  # type: ignore

        try:
            service = service_factory(self.client, effect)
            operation = self._wait(
                service.get(
                    GetOperationRequest(id=cloud_operation_id),
                    **vm_ha_request_kwargs(),
                )
            )
        except Exception as error:
            raise RuntimeError("VM-HA predecessor cloud operation could not be resolved") from error
        try:
            wait_vm_ha_operation(operation)
        except Exception as error:
            done = getattr(operation, "done", None)
            if not callable(done) or not done():
                raise RuntimeError("VM-HA predecessor cloud operation remains ambiguous") from error
        done = getattr(operation, "done", None)
        successful = getattr(operation, "successful", None)
        if not callable(done) or not done() or not callable(successful):
            raise RuntimeError("VM-HA predecessor cloud operation is not terminal")
        try:
            succeeded = bool(successful())
        except Exception as error:
            raise RuntimeError("VM-HA predecessor cloud operation status is unavailable") from error
        resource = getattr(getattr(operation, "result", None), "resource", None)
        return PredecessorOperationResolution(
            cloud_operation_id=cloud_operation_id,
            succeeded=succeeded,
            resource_id=(
                _resource_id(resource) or str(getattr(operation, "resource_id", "") or "")
            ),
        )

    def _verification_observation(self, plan: DestroyPlan) -> str:
        present: list[str] = []
        for name in plan.expected_compute_names:
            if self._get_instance_by_name(name) is not None:
                present.append(f"compute:{name}")
        for name in plan.expected_disk_names:
            if self._get_disk_by_name(name) is not None:
                present.append(f"disk:{name}")
        for name in plan.expected_allocation_names:
            if self._get_allocation_by_name(name) is not None:
                present.append(f"allocation:{name}")
        route_ids = {route.route_id for route in plan.routes}
        present.extend(
            f"route:{_resource_id(route)}"
            for _table_id, route in self._route_inventory()
            if _resource_id(route) in route_ids
        )
        if present:
            raise RuntimeError("Destroy targets remain present: " + ", ".join(sorted(present)))
        retained = []
        for public in plan.retained_public_allocations:
            observed = self._strict_resource(public)
            if observed is None:
                raise RuntimeError(f"retained public allocation disappeared: {public.name}")
            if allocation_observation(public.resource_id, observed).owner is not None:
                raise RuntimeError(f"retained public allocation is still attached: {public.name}")
            retained.append(public.resource_id)
        return _digest({"absent": sorted(present), "retained_public": sorted(retained)})

    def stable_verification(self, plan: DestroyPlan) -> tuple[str, str]:
        return self._verification_observation(plan), self._verification_observation(plan)


def execute_destroy(
    *,
    config_path: Path,
    config_digest: str,
    spec: GatewayGroupSpec,
    project_id: str,
    vm_manager: object,
    local_prefixes: object | None = None,
) -> DestroyResult:
    """Plan, checkpoint, execute, and verify one gateway teardown."""

    if not project_id or not spec.name:
        raise ValueError("Destroy requires exact project and gateway identities")
    with VMHAApplyLock(project_id=project_id, gateway_name=spec.name):
        lifecycle_store = VMHALifecycleStore(config_path)
        snapshot = lifecycle_store.read_hardened(
            expected_project_id=project_id,
            expected_gateway_name=spec.name,
        )
        lifecycle_state = None if snapshot is None else snapshot.state
        if (
            spec.vm_ha is None
            and lifecycle_state is not None
            and lifecycle_state.status
            not in {VMHALifecycleStatus.REMOVED, VMHALifecycleStatus.DESTROYED}
        ):
            raise RuntimeError(
                "Ordinary destroy is blocked by an authoritative active VM-HA lifecycle"
            )
        if spec.vm_ha is not None and lifecycle_state is not None:
            configured_members = {
                (
                    member.instance_index,
                    f"{spec.name}-{member.instance_index}",
                    member.node_id,
                    member.role.value,
                )
                for member in spec.vm_ha.members
            }
            recorded_members = {
                (
                    member.instance_index,
                    member.instance_name,
                    member.node_id,
                    member.role,
                )
                for member in lifecycle_state.members
            }
            if (
                spec.vm_ha.cluster_id != lifecycle_state.cluster_id
                or configured_members != recorded_members
            ):
                raise RuntimeError("VM-HA destroy configuration conflicts with lifecycle identity")
        backend = NebiusDestroyBackend(
            vm_manager,
            spec,
            project_id=project_id,
            lifecycle_state=(lifecycle_state if spec.vm_ha is not None else None),
        )

        if spec.vm_ha is None:
            receipt_store = DestroyReceiptStore(config_path)
            existing = receipt_store.read()
            if existing is not None and not existing.terminal:
                if existing.plan.config_digest != config_digest:
                    raise RuntimeError("Destroy config changed after durable intent was written")
                journal: _DestroyJournal = OrdinaryDestroyJournal(receipt_store, existing)
            else:
                plan = backend.build_plan(config_digest)
                if plan.blockers:
                    raise DestroyFailure(
                        "destroy-authority-conflict",
                        "Destroy is blocked: " + "; ".join(plan.blockers),
                    )
                backend.preflight(plan, require_revisions=True)
                journal = OrdinaryDestroyJournal.load_or_start(receipt_store, plan)
            return DestroyCoordinator(backend, journal).run()

        if lifecycle_state is None:
            plan = backend.build_plan(config_digest)
            if plan.effect_count or plan.blockers:
                raise RuntimeError(
                    "Explicit VM-HA resources exist without a trustworthy lifecycle authority"
                )
            receipt_store = DestroyReceiptStore(config_path)
            journal = OrdinaryDestroyJournal.load_or_start(receipt_store, plan)
            return DestroyCoordinator(backend, journal).run()
        if lifecycle_state.record_version != 4 or lifecycle_state.transaction is None:
            raise RuntimeError("VM-HA destroy requires the current v4 lifecycle authority")

        transaction = lifecycle_state.transaction
        if transaction.approval_kind == "destruction":
            if transaction.desired_state_digest != _digest(
                {"config": config_digest, "gateway": spec.name, "state": "absent"}
            ):
                raise RuntimeError("VM-HA destroy configuration changed after durable intent")
            journal = VMHADestroyJournal(VMHALifecycleJournal(lifecycle_store, lifecycle_state))
            return DestroyCoordinator(backend, journal).run()
        if lifecycle_state.status is VMHALifecycleStatus.DESTROYED:
            raise RuntimeError("VM-HA destroyed lifecycle has no destruction transaction")

        resolved_operation_id: str | None = None
        if transaction.accepted_cloud_operation_id is not None:
            if transaction.pending_effect is None:
                raise RuntimeError("VM-HA accepted cloud operation lost its pending effect")
            try:
                resolution = backend.resume_prior_vm_ha_operation(
                    transaction.pending_effect,
                    transaction.accepted_cloud_operation_id,
                )
            except Exception as error:
                raise DestroyFailure(
                    "destroy-predecessor-resume-failed",
                    "VM-HA predecessor cloud operation could not be classified",
                ) from error
            resolved_operation_id = resolution.cloud_operation_id
            if resolution.resource_id:
                backend = NebiusDestroyBackend(
                    vm_manager,
                    spec,
                    project_id=project_id,
                    lifecycle_state=lifecycle_state,
                    resolved_predecessor_effect=transaction.pending_effect,
                    resolved_predecessor_resource_id=resolution.resource_id,
                )

        plan = backend.build_plan(config_digest)
        if plan.blockers:
            raise DestroyFailure(
                "destroy-authority-conflict",
                "Destroy is blocked: " + "; ".join(plan.blockers),
            )
        backend.preflight(plan, require_revisions=True)
        plan_digest = plan.digest
        desired_digest = _digest({"config": config_digest, "gateway": spec.name, "state": "absent"})
        current_observation = {
            "schema": "nebius-vpngw/destroy-observation-v1",
            "plan_digest": plan_digest,
        }
        destruction = VMHALifecycleState.start_destruction(
            lifecycle_state,
            operation_id=_digest(
                {
                    "domain": "nebius-vpngw/destroy-operation-v1",
                    "plan": plan_digest,
                    "predecessor": lifecycle_state.record_sha256,
                }
            ),
            approval_digest=plan_digest,
            desired_state_digest=desired_digest,
            current_state_digest=_digest(current_observation),
            destroy_plan_json=plan.to_json(),
            current_observation=current_observation,
            resolved_cloud_operation_id=resolved_operation_id,
        )
        lifecycle_store.write_verified(
            destruction,
            predecessor_sha256=lifecycle_state.record_sha256,
        )
        journal = VMHADestroyJournal(VMHALifecycleJournal(lifecycle_store, destruction))
        return DestroyCoordinator(backend, journal).run()
