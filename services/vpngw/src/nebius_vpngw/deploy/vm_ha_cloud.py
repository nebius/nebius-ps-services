"""Fail-closed cloud fencing for two-node VM HA.

This module owns policy-facing cloud observations.  SDK objects stay behind
the injected reader and mutation functions, and every ambiguous observation
raises instead of falling back to the ordinary provisioning scaffold path.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import typing as t
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

VM_HA_REQUEST_TIMEOUT_SECONDS = 30.0
VM_HA_PER_RETRY_TIMEOUT_SECONDS = 10.0
VM_HA_REQUEST_RETRIES = 3
VM_HA_OPERATION_TIMEOUT_SECONDS = 300.0
VM_HA_OPERATION_POLL_INTERVAL_SECONDS = 1.0


def nebius_request_error_code_is(error: BaseException, code_name: str) -> bool:
    """Match one typed Nebius SDK request status without parsing its message."""

    try:
        from nebius.aio.service_error import RequestError
    except ImportError:
        return False
    status_code = getattr(getattr(error, "status", None), "code", None)
    return isinstance(error, RequestError) and (getattr(status_code, "name", None) == code_name)


def operation_status_lookup_unsupported(error: BaseException) -> bool:
    """Return whether the SDK lacks only the generic operation lookup API."""

    return nebius_request_error_code_is(error, "UNIMPLEMENTED")


def vm_ha_request_kwargs(operation_id: str | None = None) -> dict[str, t.Any]:
    value: dict[str, t.Any] = {
        "timeout": VM_HA_REQUEST_TIMEOUT_SECONDS,
        "auth_timeout": VM_HA_REQUEST_TIMEOUT_SECONDS,
        "per_retry_timeout": VM_HA_PER_RETRY_TIMEOUT_SECONDS,
        "retries": VM_HA_REQUEST_RETRIES,
    }
    if operation_id:
        value["metadata"] = (("x-idempotency-key", operation_id),)
    return value


def wait_vm_ha_operation(operation: t.Any) -> None:
    if not callable(getattr(operation, "sync_wait", None)):
        raise RuntimeError("VM-HA mutation returned no bounded operation waiter")
    operation.sync_wait(
        interval=VM_HA_OPERATION_POLL_INTERVAL_SECONDS,
        timeout=VM_HA_OPERATION_TIMEOUT_SECONDS,
        poll_iteration_timeout=VM_HA_REQUEST_TIMEOUT_SECONDS,
        poll_per_retry_timeout=VM_HA_PER_RETRY_TIMEOUT_SECONDS,
        poll_retries=VM_HA_REQUEST_RETRIES,
        auth_timeout=VM_HA_REQUEST_TIMEOUT_SECONDS,
    )


@dataclass(frozen=True)
class AcceptedCloudOperation:
    action_operation_id: str
    kind: str
    cloud_operation_id: str


class VMHACloudOperationJournal:
    """Atomic private receipt for one accepted controller cloud operation."""

    _SCHEMA = "nebius-vpngw/vm-ha-cloud-operation-v1"

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AcceptedCloudOperation | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("VM-HA accepted cloud operation record is malformed") from error
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "action_operation_id",
                "cloud_operation_id",
                "kind",
                "schema",
            }
            or value.get("schema") != self._SCHEMA
        ):
            raise ValueError("VM-HA accepted cloud operation record is invalid")
        fields = tuple(
            value.get(key)
            for key in (
                "action_operation_id",
                "kind",
                "cloud_operation_id",
            )
        )
        if any(not isinstance(item, str) or not item for item in fields):
            raise ValueError("VM-HA accepted cloud operation identity is invalid")
        return AcceptedCloudOperation(
            t.cast(str, fields[0]), t.cast(str, fields[1]), t.cast(str, fields[2])
        )

    def save(self, record: AcceptedCloudOperation) -> None:
        existing = self.load()
        if existing is not None:
            if existing != record:
                raise ValueError("another VM-HA cloud operation receipt is pending")
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        payload = (
            json.dumps(
                {
                    "action_operation_id": record.action_operation_id,
                    "cloud_operation_id": record.cloud_operation_id,
                    "kind": record.kind,
                    "schema": self._SCHEMA,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
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
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def clear(self, expected: AcceptedCloudOperation) -> None:
        if self.load() != expected:
            raise ValueError("VM-HA accepted cloud operation receipt changed")
        self.path.unlink()
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class HACloudError(RuntimeError):
    """Base class for errors that must block HA promotion."""


class RetryableHACloudError(HACloudError):
    """A bounded operation did not reach its required postcondition."""


class PermanentHACloudError(HACloudError):
    """The request or observed resource conflicts with the HA contract."""


class AmbiguousHACloudError(HACloudError):
    """Cloud truth is unavailable or cannot prove the required state."""


class InstanceCloudState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    TRANSITIONAL = "transitional"
    ERROR = "error"
    UNKNOWN = "unknown"


class TransferStage(str, Enum):
    FORMER_OWNER_STOPPED = "former-owner-stopped"
    FORMER_ATTACHMENT_ABSENT = "former-attachment-absent"
    CANDIDATE_ATTACHMENT_EXACT = "candidate-attachment-exact"
    OWNERSHIP_CONFIRMED = "ownership-confirmed"


@dataclass(frozen=True)
class AllocationOwner:
    instance_id: str
    network_interface_name: str


@dataclass(frozen=True)
class AllocationObservation:
    allocation_id: str
    owner: AllocationOwner | None


@dataclass(frozen=True)
class AllocationTransferProof:
    allocation_id: str
    former_instance_id: str
    candidate: AllocationOwner
    former_compute_state: InstanceCloudState
    candidate_compute_state: InstanceCloudState
    ownership_epoch: str
    stages: tuple[TransferStage, ...]


@dataclass(frozen=True)
class ComputeObservation:
    """Strict Compute state and NIC attachment at one resource revision."""

    instance_id: str
    state: InstanceCloudState
    resource_version: str
    nic_allocations: tuple[tuple[str, str], ...]
    nic_alias_allocations: tuple[tuple[str, tuple[str, ...]], ...]

    def allocation_on(self, network_interface_name: str) -> str | None:
        matches = [
            allocation_id
            for name, allocation_id in self.nic_allocations
            if name == network_interface_name
        ]
        if len(matches) > 1:
            raise AmbiguousHACloudError(
                f"Compute instance {self.instance_id} has duplicate NIC identity "
                f"{network_interface_name}"
            )
        if not matches:
            raise AmbiguousHACloudError(
                f"Compute instance {self.instance_id} has no NIC {network_interface_name}"
            )
        return matches[0]

    def alias_allocations_on(self, network_interface_name: str) -> tuple[str, ...]:
        matches = [
            allocation_ids
            for name, allocation_ids in self.nic_alias_allocations
            if name == network_interface_name
        ]
        if len(matches) > 1:
            raise AmbiguousHACloudError(
                f"Compute instance {self.instance_id} has duplicate NIC identity "
                f"{network_interface_name}"
            )
        if not matches:
            raise AmbiguousHACloudError(
                f"Compute instance {self.instance_id} has no NIC {network_interface_name}"
            )
        return matches[0]

    def has_alias_allocation(
        self,
        network_interface_name: str,
        allocation_id: str,
    ) -> bool:
        return allocation_id in self.alias_allocations_on(network_interface_name)


@dataclass(frozen=True)
class ClusterCloudObservation:
    """One internally consistent read of both members and the allocation."""

    allocation: AllocationObservation
    former: ComputeObservation
    candidate: ComputeObservation
    former_owner: AllocationOwner
    candidate_owner: AllocationOwner

    @property
    def former_attachment_absent(self) -> bool:
        return not self.former.has_alias_allocation(
            self.former_owner.network_interface_name,
            self.allocation.allocation_id,
        )

    @property
    def former_attachment_exact(self) -> bool:
        return bool(
            self.allocation.owner == self.former_owner
            and self.former.has_alias_allocation(
                self.former_owner.network_interface_name,
                self.allocation.allocation_id,
            )
        )

    @property
    def candidate_attachment_exact(self) -> bool:
        return bool(
            self.allocation.owner == self.candidate_owner
            and self.candidate.has_alias_allocation(
                self.candidate_owner.network_interface_name,
                self.allocation.allocation_id,
            )
        )

    @property
    def candidate_attachment_absent(self) -> bool:
        return not self.candidate.has_alias_allocation(
            self.candidate_owner.network_interface_name,
            self.allocation.allocation_id,
        )


class NebiusSDKCloudClient:
    """Exact synchronous Nebius SDK calls used by the on-node HA service."""

    def __init__(
        self,
        sdk: t.Any,
        *,
        operation_journal: VMHACloudOperationJournal | None = None,
        request_timeout_provider: t.Callable[[], float] | None = None,
    ) -> None:
        self.sdk = sdk
        self.operation_journal = operation_journal
        self._request_timeout_provider = request_timeout_provider

    def _request_kwargs(self, operation_id: str | None = None) -> dict[str, t.Any]:
        kwargs = vm_ha_request_kwargs(operation_id)
        if self._request_timeout_provider is None:
            return kwargs
        remaining = float(self._request_timeout_provider())
        if not (0 < remaining < float("inf")):
            raise TimeoutError("VM-HA cloud request deadline has expired")
        request_timeout = min(VM_HA_REQUEST_TIMEOUT_SECONDS, remaining)
        kwargs.update(
            timeout=request_timeout,
            auth_timeout=request_timeout,
            per_retry_timeout=min(VM_HA_PER_RETRY_TIMEOUT_SECONDS, request_timeout),
        )
        return kwargs

    def _resume_operation(self, cloud_operation_id: str) -> t.Any:
        from nebius.api.nebius.common.v1 import GetOperationRequest, OperationServiceClient

        return (
            OperationServiceClient(self.sdk)
            .get(
                GetOperationRequest(id=cloud_operation_id),
                **self._request_kwargs(),
            )
            .wait()
        )

    @staticmethod
    def _require_operation_success(operation: t.Any) -> None:
        successful = getattr(operation, "successful", None)
        if not callable(successful):
            raise AmbiguousHACloudError("VM-HA cloud operation exposed no terminal success status")
        try:
            succeeded = bool(successful())
        except Exception as error:
            raise AmbiguousHACloudError(
                "VM-HA cloud operation success status is unavailable"
            ) from error
        if not succeeded:
            raise PermanentHACloudError("VM-HA cloud operation finished unsuccessfully")

    def _mutate(
        self,
        *,
        action_operation_id: str | None,
        kind: str,
        submit: t.Callable[[], t.Any],
    ) -> None:
        journal = self.operation_journal
        accepted = None if journal is None else journal.load()
        if accepted is not None:
            assert journal is not None
            if (
                not action_operation_id
                or accepted.action_operation_id != action_operation_id
                or accepted.kind != kind
            ):
                raise PermanentHACloudError(
                    "a different accepted VM-HA cloud operation is still pending"
                )
            try:
                operation = self._resume_operation(accepted.cloud_operation_id)
            except Exception as error:
                if not operation_status_lookup_unsupported(error):
                    raise
                operation = submit().wait()
                resumed_operation_id = str(getattr(operation, "id", "") or "")
                if resumed_operation_id != accepted.cloud_operation_id:
                    raise AmbiguousHACloudError(
                        "VM-HA idempotent replay returned a different cloud operation identity"
                    ) from error
            wait_vm_ha_operation(operation)
            self._require_operation_success(operation)
            journal.clear(accepted)
            return
        operation = submit().wait()
        cloud_operation_id = str(getattr(operation, "id", "") or "")
        if journal is not None:
            if not action_operation_id or not cloud_operation_id:
                raise AmbiguousHACloudError("VM-HA mutation returned no durable operation identity")
            accepted = AcceptedCloudOperation(
                action_operation_id=action_operation_id,
                kind=kind,
                cloud_operation_id=cloud_operation_id,
            )
            journal.save(accepted)
        wait_vm_ha_operation(operation)
        self._require_operation_success(operation)
        if journal is not None and accepted is not None:
            journal.clear(accepted)

    def finalize_accepted_operation(self, expected: AcceptedCloudOperation) -> None:
        """Read and clear one exact terminal operation without resubmitting it."""

        journal = self.operation_journal
        if journal is None:
            raise PermanentHACloudError("VM-HA cloud operation journal is unavailable")
        accepted = journal.load()
        if accepted != expected:
            raise PermanentHACloudError("accepted VM-HA cloud operation identity changed")
        operation = self._resume_operation(accepted.cloud_operation_id)
        wait_vm_ha_operation(operation)
        self._require_operation_success(operation)
        journal.clear(accepted)

    def get_instance(self, instance_id: str) -> t.Any:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        return (
            InstanceServiceClient(self.sdk)
            .get(
                GetInstanceRequest(id=instance_id),
                **self._request_kwargs(),
            )
            .wait()
        )

    def stop_instance(self, instance_id: str, operation_id: str | None = None) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StopInstanceRequest

        self._mutate(
            action_operation_id=operation_id,
            kind="stop-instance",
            submit=lambda: InstanceServiceClient(self.sdk).stop(
                StopInstanceRequest(id=instance_id),
                **self._request_kwargs(operation_id),
            ),
        )

    def start_instance(self, instance_id: str, operation_id: str | None = None) -> None:
        """Start one exact Compute instance through the bounded mutation path."""

        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StartInstanceRequest

        self._mutate(
            action_operation_id=operation_id,
            kind="start-instance",
            submit=lambda: InstanceServiceClient(self.sdk).start(
                StartInstanceRequest(id=instance_id),
                **self._request_kwargs(operation_id),
            ),
        )

    def get_allocation(self, allocation_id: str) -> t.Any:
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        return (
            AllocationServiceClient(self.sdk)
            .get(
                GetAllocationRequest(id=allocation_id),
                **self._request_kwargs(),
            )
            .wait()
        )

    def set_alias_allocation(
        self,
        instance_id: str,
        network_interface_name: str,
        allocation_id: str,
        present: bool,
        operation_id: str | None = None,
    ) -> None:
        from nebius.api.nebius.compute.v1 import (
            InstanceServiceClient,
            IPAlias,
            UpdateInstanceRequest,
        )

        instance = self.get_instance(instance_id)
        original_spec = getattr(instance, "spec", None)
        original_metadata = getattr(instance, "metadata", None)
        if original_spec is None or original_metadata is None:
            raise AmbiguousHACloudError("Compute instance has no mutable spec metadata")
        # Nebius SDK response messages are not deepcopy-safe: deserialized
        # metadata and repeated NIC fields are cleared by deepcopy.
        spec = clone_nebius_sdk_message(original_spec)
        metadata = clone_nebius_sdk_message(original_metadata)
        interfaces = list(getattr(spec, "network_interfaces", ()) or ())
        matches = [
            (index, interface)
            for index, interface in enumerate(interfaces)
            if str(getattr(interface, "name", "")) == network_interface_name
        ]
        if len(matches) != 1:
            raise AmbiguousHACloudError("Compute instance has an ambiguous NIC identity")
        index, interface = matches[0]
        current_ids = network_interface_alias_allocation_ids(
            interface,
            description=f"Compute instance {instance_id} NIC {network_interface_name}",
        )
        if (allocation_id in current_ids) is present:
            return
        updated = clone_nebius_sdk_message(interface)
        desired_ids = (
            (*current_ids, allocation_id)
            if present
            else tuple(current_id for current_id in current_ids if current_id != allocation_id)
        )
        updated.aliases = [IPAlias(allocation_id=current_id) for current_id in desired_ids]
        interfaces[index] = updated
        spec.network_interfaces = interfaces
        self._mutate(
            action_operation_id=operation_id,
            kind="set-alias-present" if present else "set-alias-absent",
            submit=lambda: InstanceServiceClient(self.sdk).update(
                UpdateInstanceRequest(metadata=metadata, spec=spec),
                **self._request_kwargs(operation_id),
            ),
        )


InstanceReader = t.Callable[[str], t.Any]
InstanceStopper = t.Callable[..., None]
AllocationReader = t.Callable[[str], t.Any]
AliasAllocationSetter = t.Callable[..., None]


def _enum_name(value: t.Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()
    text = str(value).strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    numeric_states = {
        "0": "UNSPECIFIED",
        "1": "CREATING",
        "2": "UPDATING",
        "3": "STARTING",
        "4": "RUNNING",
        "5": "STOPPING",
        "6": "STOPPED",
        "7": "DELETING",
        "8": "ERROR",
    }
    return numeric_states.get(text, text)


def network_interface_alias_allocation_ids(
    interface: t.Any,
    *,
    description: str,
) -> tuple[str, ...]:
    """Return exact secondary-alias allocation IDs and reject ambiguous state."""

    raw_aliases = list(getattr(interface, "aliases", ()) or ())
    allocation_ids = tuple(
        str(getattr(alias, "allocation_id", None) or "") for alias in raw_aliases
    )
    if any(not allocation_id for allocation_id in allocation_ids):
        raise AmbiguousHACloudError(f"{description} has an alias without an allocation ID")
    if len(set(allocation_ids)) != len(allocation_ids):
        raise AmbiguousHACloudError(f"{description} has duplicate alias allocation IDs")
    return allocation_ids


def clone_nebius_sdk_message(message: t.Any) -> t.Any:
    """Clone generated Nebius messages across supported SDK representations."""

    protobuf = getattr(message, "__pb2_message__", None)
    if protobuf is not None:
        cloned_protobuf = type(protobuf)()
        cloned_protobuf.CopyFrom(protobuf)
        return type(message)(cloned_protobuf)
    if hasattr(message, "_values"):
        return type(message)(message)
    raise TypeError("Unsupported Nebius SDK generated-message representation")


def instance_cloud_state(instance: t.Any) -> InstanceCloudState:
    """Translate a Compute instance status without treating uncertainty as stopped."""
    raw_state = getattr(getattr(instance, "status", None), "state", None)
    state = _enum_name(raw_state)
    if state == "STOPPED":
        return InstanceCloudState.STOPPED
    if state == "RUNNING":
        return InstanceCloudState.RUNNING
    if state == "STOPPING":
        return InstanceCloudState.STOPPING
    if state == "ERROR":
        return InstanceCloudState.ERROR
    if state in {"CREATING", "UPDATING", "STARTING", "DELETING"}:
        return InstanceCloudState.TRANSITIONAL
    return InstanceCloudState.UNKNOWN


def compute_observation(instance_id: str, instance: t.Any) -> ComputeObservation:
    """Normalize one Compute resource without inventing an ownership revision."""

    metadata = getattr(instance, "metadata", None)
    raw_resource_version = getattr(metadata, "resource_version", None)
    if raw_resource_version is None:
        raise AmbiguousHACloudError(
            f"Compute instance {instance_id} has no valid metadata.resource_version"
        )
    if isinstance(raw_resource_version, bool) or not isinstance(raw_resource_version, (int, str)):
        raise AmbiguousHACloudError(
            f"Compute instance {instance_id} has no valid metadata.resource_version"
        )
    raw_text = str(raw_resource_version)
    if not raw_text.isdecimal():
        raise AmbiguousHACloudError(
            f"Compute instance {instance_id} has no valid metadata.resource_version"
        )
    numeric_resource_version = int(raw_text)
    if numeric_resource_version <= 0:
        raise AmbiguousHACloudError(
            f"Compute instance {instance_id} has no positive metadata.resource_version"
        )

    interfaces = getattr(getattr(instance, "spec", None), "network_interfaces", None)
    if interfaces is None:
        raise AmbiguousHACloudError(
            f"Compute instance {instance_id} has no network interface specification"
        )
    nic_allocations: list[tuple[str, str]] = []
    nic_alias_allocations: list[tuple[str, tuple[str, ...]]] = []
    seen_names: set[str] = set()
    for interface in interfaces:
        name = str(getattr(interface, "name", None) or "")
        if not name or name in seen_names:
            raise AmbiguousHACloudError(
                f"Compute instance {instance_id} has an incomplete or duplicate NIC identity"
            )
        seen_names.add(name)
        allocation_id = str(
            getattr(getattr(interface, "ip_address", None), "allocation_id", None) or ""
        )
        nic_allocations.append((name, allocation_id))
        nic_alias_allocations.append(
            (
                name,
                network_interface_alias_allocation_ids(
                    interface,
                    description=f"Compute instance {instance_id} NIC {name}",
                ),
            )
        )
    return ComputeObservation(
        instance_id=instance_id,
        state=instance_cloud_state(instance),
        resource_version=str(numeric_resource_version),
        nic_allocations=tuple(nic_allocations),
        nic_alias_allocations=tuple(nic_alias_allocations),
    )


def allocation_observation(allocation_id: str, allocation: t.Any) -> AllocationObservation:
    """Return the authoritative network-interface owner, rejecting odd assignments."""
    status = getattr(allocation, "status", None)
    state = _enum_name(getattr(status, "state", None))
    if state not in {"ALLOCATED", "ASSIGNED"}:
        raise AmbiguousHACloudError(
            f"allocation {allocation_id} is {state or 'UNKNOWN'}, not stable"
        )
    assignment = getattr(status, "assignment", None)
    if assignment is None:
        if state == "ASSIGNED":
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} reports ASSIGNED without an owner"
            )
        return AllocationObservation(allocation_id=allocation_id, owner=None)

    network_interface = getattr(assignment, "network_interface", None)
    instance_id = getattr(network_interface, "instance_id", None)
    interface_name = getattr(network_interface, "name", None)
    load_balancer = getattr(assignment, "load_balancer", None)
    if load_balancer or bool(instance_id) != bool(interface_name):
        raise AmbiguousHACloudError(
            f"allocation {allocation_id} has a non-VM or incomplete assignment"
        )
    if not instance_id:
        if state == "ASSIGNED":
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} reports ASSIGNED without an owner"
            )
        return AllocationObservation(allocation_id=allocation_id, owner=None)
    if state != "ASSIGNED":
        raise AmbiguousHACloudError(
            f"allocation {allocation_id} reports {state} with an attached owner"
        )
    return AllocationObservation(
        allocation_id=allocation_id,
        owner=AllocationOwner(str(instance_id), str(interface_name)),
    )


class VMHACloudAdapter:
    """Strict, retry-safe fencing and private-allocation ownership operations."""

    def __init__(
        self,
        *,
        instance_reader: InstanceReader,
        instance_stopper: InstanceStopper,
        allocation_reader: AllocationReader,
        alias_allocation_setter: AliasAllocationSetter,
        attempts: int = 10,
        poll_interval: float = 1.0,
        sleeper: t.Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        if poll_interval < 0:
            raise ValueError("poll_interval must be non-negative")
        self._instance_reader = instance_reader
        self._instance_stopper = instance_stopper
        self._allocation_reader = allocation_reader
        self._alias_allocation_setter = alias_allocation_setter
        self._attempts = attempts
        self._poll_interval = poll_interval
        self._sleep = sleeper

    @staticmethod
    def _call(description: str, operation: t.Callable[[], t.Any]) -> t.Any:
        try:
            return operation()
        except HACloudError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            try:
                code = code() if callable(code) else code
            except Exception:
                code = None
            code_name = _enum_name(code)
            if isinstance(exc, (TimeoutError, ConnectionError)) or code_name in {
                "ABORTED",
                "DEADLINE_EXCEEDED",
                "RESOURCE_EXHAUSTED",
                "UNAVAILABLE",
            }:
                raise RetryableHACloudError(f"{description}: {exc}") from exc
            if isinstance(exc, PermissionError) or code_name in {
                "FAILED_PRECONDITION",
                "INVALID_ARGUMENT",
                "NOT_FOUND",
                "PERMISSION_DENIED",
                "UNAUTHENTICATED",
            }:
                raise PermanentHACloudError(f"{description}: {exc}") from exc
            raise AmbiguousHACloudError(f"{description}: {exc}") from exc

    def _read_instance(self, instance_id: str) -> ComputeObservation:
        instance = self._call(
            f"cannot read Compute instance {instance_id}",
            lambda: self._instance_reader(instance_id),
        )
        if instance is None:
            raise AmbiguousHACloudError(f"Compute instance {instance_id} was not returned")
        return compute_observation(instance_id, instance)

    def _read_allocation(self, allocation_id: str) -> AllocationObservation:
        allocation = self._call(
            f"cannot read allocation {allocation_id}",
            lambda: self._allocation_reader(allocation_id),
        )
        if allocation is None:
            raise AmbiguousHACloudError(f"allocation {allocation_id} was not returned")
        return allocation_observation(allocation_id, allocation)

    def observe_cluster(
        self,
        *,
        allocation_id: str,
        former_owner: AllocationOwner,
        candidate: AllocationOwner,
    ) -> ClusterCloudObservation:
        """Read exact two-sided ownership, rejecting a changing allocation."""

        before = self._read_allocation(allocation_id)
        former = self._read_instance(former_owner.instance_id)
        current = self._read_instance(candidate.instance_id)
        after = self._read_allocation(allocation_id)
        if before != after:
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} ownership changed during observation"
            )
        return ClusterCloudObservation(
            allocation=after,
            former=former,
            candidate=current,
            former_owner=former_owner,
            candidate_owner=candidate,
        )

    def require_stopped(
        self,
        instance_id: str,
        operation_id: str | None = None,
    ) -> ComputeObservation:
        """Stop a running former owner and require an authoritative STOPPED read."""
        stop_requested = False
        for attempt in range(self._attempts):
            observation = self._read_instance(instance_id)
            state = observation.state
            if state is InstanceCloudState.STOPPED:
                return observation
            if state is InstanceCloudState.RUNNING and not stop_requested:
                self._call(
                    f"cannot stop Compute instance {instance_id}",
                    lambda: (
                        self._instance_stopper(instance_id, operation_id)
                        if operation_id is not None
                        else self._instance_stopper(instance_id)
                    ),
                )
                stop_requested = True
            elif state in {InstanceCloudState.ERROR, InstanceCloudState.UNKNOWN}:
                raise AmbiguousHACloudError(
                    f"Compute instance {instance_id} is {state.value}, not authoritatively stopped"
                )
            if attempt + 1 < self._attempts:
                self._sleep(self._poll_interval)
        raise RetryableHACloudError(
            f"Compute instance {instance_id} did not become STOPPED within the bounded poll"
        )

    def require_compute_attachment(
        self,
        allocation_id: str,
        owner: AllocationOwner,
        *,
        present: bool,
    ) -> ComputeObservation:
        """Require the exact NIC specification to agree with allocation ownership."""

        for attempt in range(self._attempts):
            observation = self._read_instance(owner.instance_id)
            if observation.state in {InstanceCloudState.ERROR, InstanceCloudState.UNKNOWN}:
                raise AmbiguousHACloudError(
                    f"Compute instance {owner.instance_id} is {observation.state.value}"
                )
            if observation.state in {
                InstanceCloudState.STOPPING,
                InstanceCloudState.TRANSITIONAL,
            }:
                if attempt + 1 < self._attempts:
                    self._sleep(self._poll_interval)
                    continue
                raise RetryableHACloudError(
                    f"Compute instance {owner.instance_id} did not reach a stable state"
                )
            matches = observation.has_alias_allocation(
                owner.network_interface_name,
                allocation_id,
            )
            if matches is present:
                return observation
            if attempt + 1 < self._attempts:
                self._sleep(self._poll_interval)
        expected = "contain" if present else "exclude"
        raise RetryableHACloudError(
            f"Compute instance {owner.instance_id} NIC {owner.network_interface_name} "
            f"did not {expected} allocation {allocation_id}"
        )

    def require_former_attachment_absent(
        self,
        allocation_id: str,
        former_owner: AllocationOwner,
        candidate: AllocationOwner,
        operation_id: str | None = None,
    ) -> None:
        """Detach only the exact former attachment and prove that it is absent."""
        detach_requested = False
        for attempt in range(self._attempts):
            owner = self._read_allocation(allocation_id).owner
            if owner is None or owner == candidate:
                return
            if owner != former_owner:
                raise PermanentHACloudError(
                    f"allocation {allocation_id} belongs to unexpected owner {owner.instance_id}/"
                    f"{owner.network_interface_name}"
                )
            if not detach_requested:
                self._call(
                    f"cannot detach allocation {allocation_id} from former owner",
                    lambda: self._alias_allocation_setter(
                        former_owner.instance_id,
                        former_owner.network_interface_name,
                        allocation_id,
                        False,
                        *(() if operation_id is None else (operation_id,)),
                    ),
                )
                detach_requested = True
            if attempt + 1 < self._attempts:
                self._sleep(self._poll_interval)
        raise RetryableHACloudError(f"allocation {allocation_id} remained attached to former owner")

    def require_candidate_attachment(
        self,
        allocation_id: str,
        candidate: AllocationOwner,
        operation_id: str | None = None,
    ) -> None:
        """Attach to the exact candidate and require an exact ownership re-read."""
        attach_requested = False
        for attempt in range(self._attempts):
            owner = self._read_allocation(allocation_id).owner
            if owner == candidate:
                return
            if owner is not None:
                raise PermanentHACloudError(
                    f"allocation {allocation_id} became attached to unexpected owner "
                    f"{owner.instance_id}/{owner.network_interface_name}"
                )
            if not attach_requested:
                self._call(
                    f"cannot attach allocation {allocation_id} to candidate",
                    lambda: self._alias_allocation_setter(
                        candidate.instance_id,
                        candidate.network_interface_name,
                        allocation_id,
                        True,
                        *(() if operation_id is None else (operation_id,)),
                    ),
                )
                attach_requested = True
            if attempt + 1 < self._attempts:
                self._sleep(self._poll_interval)
        raise RetryableHACloudError(
            f"allocation {allocation_id} was not confirmed on the candidate"
        )

    def transfer_private_allocation(
        self,
        *,
        allocation_id: str,
        former_owner: AllocationOwner,
        candidate: AllocationOwner,
    ) -> AllocationTransferProof:
        """Perform the only safe ownership sequence and return ordered proof stages."""
        if not allocation_id or not all(
            (
                former_owner.instance_id,
                former_owner.network_interface_name,
                candidate.instance_id,
                candidate.network_interface_name,
            )
        ):
            raise PermanentHACloudError("allocation and owner identities must be non-empty")
        if former_owner.instance_id == candidate.instance_id:
            raise PermanentHACloudError(
                "former owner and candidate must be distinct Compute instances"
            )

        former_compute = self.require_stopped(former_owner.instance_id)
        stages = [TransferStage.FORMER_OWNER_STOPPED]
        self.require_former_attachment_absent(allocation_id, former_owner, candidate)
        former_compute = self.require_compute_attachment(allocation_id, former_owner, present=False)
        stages.append(TransferStage.FORMER_ATTACHMENT_ABSENT)
        self.require_candidate_attachment(allocation_id, candidate)
        stages.append(TransferStage.CANDIDATE_ATTACHMENT_EXACT)

        final_owner = self._read_allocation(allocation_id).owner
        if final_owner != candidate:
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} ownership changed before final confirmation"
            )
        candidate_compute = self.require_compute_attachment(allocation_id, candidate, present=True)
        former_compute = self.require_stopped(former_owner.instance_id)
        former_compute = self.require_compute_attachment(allocation_id, former_owner, present=False)
        final_owner = self._read_allocation(allocation_id).owner
        if final_owner != candidate:
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} ownership changed after candidate Compute read"
            )
        stages.append(TransferStage.OWNERSHIP_CONFIRMED)
        return AllocationTransferProof(
            allocation_id=allocation_id,
            former_instance_id=former_owner.instance_id,
            candidate=candidate,
            former_compute_state=former_compute.state,
            candidate_compute_state=candidate_compute.state,
            ownership_epoch=candidate_compute.resource_version,
            stages=tuple(stages),
        )
