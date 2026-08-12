"""Fail-closed cloud fencing for two-node VM HA.

This module owns policy-facing cloud observations.  SDK objects stay behind
the injected reader and mutation functions, and every ambiguous observation
raises instead of falling back to the ordinary provisioning scaffold path.
"""

from __future__ import annotations

import time
import typing as t
from dataclasses import dataclass
from enum import Enum


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
    stages: tuple[TransferStage, ...]


InstanceReader = t.Callable[[str], t.Any]
InstanceStopper = t.Callable[[str], None]
AllocationReader = t.Callable[[str], t.Any]
AllocationSetter = t.Callable[[str, str, str | None], None]


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
        allocation_setter: AllocationSetter,
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
        self._allocation_setter = allocation_setter
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

    def _read_instance_state(self, instance_id: str) -> InstanceCloudState:
        instance = self._call(
            f"cannot read Compute instance {instance_id}",
            lambda: self._instance_reader(instance_id),
        )
        if instance is None:
            raise AmbiguousHACloudError(f"Compute instance {instance_id} was not returned")
        return instance_cloud_state(instance)

    def _read_allocation(self, allocation_id: str) -> AllocationObservation:
        allocation = self._call(
            f"cannot read allocation {allocation_id}",
            lambda: self._allocation_reader(allocation_id),
        )
        if allocation is None:
            raise AmbiguousHACloudError(f"allocation {allocation_id} was not returned")
        return allocation_observation(allocation_id, allocation)

    def require_stopped(self, instance_id: str) -> None:
        """Stop a running former owner and require an authoritative STOPPED read."""
        stop_requested = False
        for attempt in range(self._attempts):
            state = self._read_instance_state(instance_id)
            if state is InstanceCloudState.STOPPED:
                return
            if state is InstanceCloudState.RUNNING and not stop_requested:
                self._call(
                    f"cannot stop Compute instance {instance_id}",
                    lambda: self._instance_stopper(instance_id),
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

    def require_former_attachment_absent(
        self,
        allocation_id: str,
        former_owner: AllocationOwner,
        candidate: AllocationOwner,
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
                    lambda: self._allocation_setter(
                        former_owner.instance_id,
                        former_owner.network_interface_name,
                        None,
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
                    lambda: self._allocation_setter(
                        candidate.instance_id,
                        candidate.network_interface_name,
                        allocation_id,
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

        self.require_stopped(former_owner.instance_id)
        stages = [TransferStage.FORMER_OWNER_STOPPED]
        self.require_former_attachment_absent(allocation_id, former_owner, candidate)
        stages.append(TransferStage.FORMER_ATTACHMENT_ABSENT)
        self.require_candidate_attachment(allocation_id, candidate)
        stages.append(TransferStage.CANDIDATE_ATTACHMENT_EXACT)

        final_owner = self._read_allocation(allocation_id).owner
        if final_owner != candidate:
            raise AmbiguousHACloudError(
                f"allocation {allocation_id} ownership changed before final confirmation"
            )
        stages.append(TransferStage.OWNERSHIP_CONFIRMED)
        return AllocationTransferProof(
            allocation_id=allocation_id,
            former_instance_id=former_owner.instance_id,
            candidate=candidate,
            stages=tuple(stages),
        )
