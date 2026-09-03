"""Independent start-only warm-standby restoration for VM HA."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from nebius_vpngw.agent.vm_ha.runtime import (
    RenewableNebiusSDK,
    begin_runtime_identity_preflight,
    build_identity_bound_sdk,
    record_runtime_identity_migration_required,
    runtime_identity_binding_is_explicit,
    validate_installed_credential_bundle,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AcceptedCloudOperation,
    AllocationObservation,
    AllocationOwner,
    AmbiguousHACloudError,
    ComputeObservation,
    InstanceCloudState,
    NebiusSDKCloudClient,
    PermanentHACloudError,
    RetryableHACloudError,
    VMHACloudOperationJournal,
    allocation_observation,
    compute_observation,
)
from nebius_vpngw.schema import VMHARuntimeBinding, VMHARuntimeNodeBinding

from .vm_ha.auto_healing import (
    AutoHealingPolicyError,
    AutoHealingPolicyPhase,
    AutoHealingPolicyStore,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryReason,
    AutoHealingRecoveryRecord,
    AutoHealingRecoveryStore,
    StandbyAutoHealing,
    load_peer_policy_heartbeat,
    peer_policy_agrees,
)
from .vm_ha.inhibition import standby_replacement_inhibition_operation_id
from .vm_ha.mtls import ManagedMTLSStore
from .vm_ha.restoration import (
    RestorationPhase,
    StandbyRestorationAuthorization,
    StandbyRestorationError,
    StandbyRestorationReason,
    StandbyRestorationStore,
    blocked_restoration_reason_code,
    policy_authorizes_restoration,
)

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
ENABLED_PATH = Path("/etc/nebius-vpngw/vm-ha-enabled")
STATE_DIR = Path("/var/lib/nebius-vpngw/vm-ha")
PROMOTION_RECEIPT_PATH = STATE_DIR / "promotion-receipt.json"
REARM_REQUEST_PATH = STATE_DIR / "rearm-request.json"
REARM_CHECKPOINT_PATH = STATE_DIR / "rearm-checkpoint.json"
REARM_OPERATION_PATH = STATE_DIR / "rearm-cloud-operation.json"
REARM_STATUS_PATH = STATE_DIR / "rearm-status.json"
REARM_LOCK_PATH = STATE_DIR / "rearm.lock"
APPLY_LOCK_PATH = STATE_DIR / "apply.lock"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def _boot_id() -> str:
    value = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("VM-HA current boot identity is unavailable")
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _durably_unlink(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(frozen=True)
class PromotionReceipt:
    receipt_id: str
    intent: str
    cluster_id: str
    owner_node_id: str
    former_owner_node_id: str
    allocation_id: str
    ownership_epoch: str
    generation_id: str
    digests: Mapping[str, str]
    route_operation_id: str
    committed_at: float
    common_cutover_seconds: float


@dataclass(frozen=True)
class RearmCheckpoint:
    receipt_id: str
    target_node_id: str
    stopped_revision: str
    operation_id: str
    phase: str
    attempted_at: float
    completed_at: float | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class RearmRetryRequest:
    cluster_id: str
    owner_node_id: str
    node_id: str
    generation_id: str
    target_node_id: str
    promotion_receipt_id: str
    request_id: str
    requested_at: float


class RearmCloudPort(Protocol):
    def observe_instance(self, instance_id: str) -> ComputeObservation: ...

    def observe_allocation(self, allocation_id: str) -> AllocationObservation: ...

    def start_instance(self, instance_id: str, operation_id: str) -> None: ...

    def accepted_operation(self) -> AcceptedCloudOperation | None: ...

    def finalize_accepted_start(self, operation: AcceptedCloudOperation) -> None: ...


class SDKRearmCloudPort:
    """Expose only read/start capabilities from the shared SDK adapter."""

    def __init__(self, sdk: Any, *, journal: VMHACloudOperationJournal) -> None:
        self.journal = journal
        self.calls = NebiusSDKCloudClient(sdk, operation_journal=journal)

    def observe_instance(self, instance_id: str) -> ComputeObservation:
        return compute_observation(instance_id, self.calls.get_instance(instance_id))

    def observe_allocation(self, allocation_id: str) -> AllocationObservation:
        return allocation_observation(allocation_id, self.calls.get_allocation(allocation_id))

    def start_instance(self, instance_id: str, operation_id: str) -> None:
        self.calls.start_instance(instance_id, operation_id)

    def accepted_operation(self) -> AcceptedCloudOperation | None:
        return self.journal.load()

    def finalize_accepted_start(self, operation: AcceptedCloudOperation) -> None:
        if operation.kind != "start-instance":
            raise RuntimeError("accepted VM-HA operation is not a Compute start")
        self.calls.finalize_accepted_operation(operation)


def _promotion_receipt(
    path: Path,
    *,
    binding: VMHARuntimeBinding,
    local: VMHARuntimeNodeBinding,
    peer: VMHARuntimeNodeBinding,
) -> PromotionReceipt | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "allocation_id",
        "cluster_id",
        "committed_at",
        "common_cutover_seconds",
        "digests",
        "former_owner_node_id",
        "generation_id",
        "intent",
        "owner_node_id",
        "ownership_epoch",
        "receipt_id",
        "route_operation_id",
        "schema",
    }
    expected_digests = {
        "configuration": binding.configuration_digest,
        "static_routes": binding.static_routes_digest,
        "bgp_policy": binding.bgp_policy_digest,
    }
    if not (
        isinstance(payload, dict)
        and set(payload) == expected_keys
        and payload.get("schema") == "nebius-vpngw/vm-ha-promotion-receipt-v1"
        and payload.get("cluster_id") == binding.cluster_id
        and payload.get("owner_node_id") == local.node_id
        and payload.get("former_owner_node_id") == peer.node_id
        and payload.get("allocation_id") == binding.shared_allocation_id
        and payload.get("generation_id") == binding.generation_id
        and payload.get("digests") == expected_digests
        and payload.get("intent")
        in {
            "planned-failover",
            "planned-failback",
            "automatic-failover",
            "apply-owner-adoption",
        }
        and all(
            isinstance(payload.get(field), str) and bool(payload[field])
            for field in ("receipt_id", "ownership_epoch", "route_operation_id")
        )
        and all(
            isinstance(payload.get(field), (int, float))
            and not isinstance(payload.get(field), bool)
            and float(payload[field]) >= 0
            and math.isfinite(float(payload[field]))
            for field in ("committed_at", "common_cutover_seconds")
        )
    ):
        raise ValueError("VM-HA promotion receipt is invalid or stale")
    return PromotionReceipt(
        receipt_id=str(payload["receipt_id"]),
        intent=str(payload["intent"]),
        cluster_id=str(payload["cluster_id"]),
        owner_node_id=str(payload["owner_node_id"]),
        former_owner_node_id=str(payload["former_owner_node_id"]),
        allocation_id=str(payload["allocation_id"]),
        ownership_epoch=str(payload["ownership_epoch"]),
        generation_id=str(payload["generation_id"]),
        digests=dict(payload["digests"]),
        route_operation_id=str(payload["route_operation_id"]),
        committed_at=float(payload["committed_at"]),
        common_cutover_seconds=float(payload["common_cutover_seconds"]),
    )


def _read_checkpoint(path: Path) -> RearmCheckpoint | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not (
        isinstance(payload, dict)
        and set(payload)
        == {
            "attempted_at",
            "completed_at",
            "failure_reason",
            "operation_id",
            "phase",
            "receipt_id",
            "schema",
            "stopped_revision",
            "target_node_id",
        }
        and payload.get("schema") == "nebius-vpngw/vm-ha-rearm-checkpoint-v1"
        and payload.get("phase") in {"start-requested", "starting", "running", "blocked"}
        and all(
            isinstance(payload.get(field), str) and bool(payload[field])
            for field in ("receipt_id", "target_node_id", "stopped_revision", "operation_id")
        )
        and isinstance(payload.get("attempted_at"), (int, float))
        and not isinstance(payload.get("attempted_at"), bool)
        and math.isfinite(float(payload["attempted_at"]))
        and (
            payload.get("completed_at") is None
            or (
                isinstance(payload.get("completed_at"), (int, float))
                and not isinstance(payload.get("completed_at"), bool)
                and math.isfinite(float(payload["completed_at"]))
            )
        )
        and (
            payload.get("failure_reason") is None or isinstance(payload.get("failure_reason"), str)
        )
    ):
        raise ValueError("VM-HA rearm checkpoint is invalid")
    return RearmCheckpoint(
        receipt_id=str(payload["receipt_id"]),
        target_node_id=str(payload["target_node_id"]),
        stopped_revision=str(payload["stopped_revision"]),
        operation_id=str(payload["operation_id"]),
        phase=str(payload["phase"]),
        attempted_at=float(payload["attempted_at"]),
        completed_at=(None if payload["completed_at"] is None else float(payload["completed_at"])),
        failure_reason=(
            None if payload["failure_reason"] is None else str(payload["failure_reason"])
        ),
    )


def _write_checkpoint(path: Path, checkpoint: RearmCheckpoint) -> None:
    _atomic_write_json(
        path,
        {
            "schema": "nebius-vpngw/vm-ha-rearm-checkpoint-v1",
            "receipt_id": checkpoint.receipt_id,
            "target_node_id": checkpoint.target_node_id,
            "stopped_revision": checkpoint.stopped_revision,
            "operation_id": checkpoint.operation_id,
            "phase": checkpoint.phase,
            "attempted_at": checkpoint.attempted_at,
            "completed_at": checkpoint.completed_at,
            "failure_reason": checkpoint.failure_reason,
        },
    )


def _operation_id(receipt_id: str, target_node_id: str, revision: str) -> str:
    digest = hashlib.sha256(
        f"vm-ha-rearm-v1\0{receipt_id}\0{target_node_id}\0{revision}".encode()
    ).digest()[:16]
    return str(uuid.UUID(bytes=digest, version=4))


def _request_matches(
    path: Path,
    *,
    receipt: PromotionReceipt,
    target_node_id: str,
) -> RearmRetryRequest | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not (
        isinstance(payload, dict)
        and set(payload)
        == {
            "cluster_id",
            "owner_node_id",
            "node_id",
            "generation_id",
            "promotion_receipt_id",
            "request_id",
            "requested_at",
            "schema",
            "target_node_id",
        }
        and payload.get("schema") == "nebius-vpngw/vm-ha-rearm-request-v1"
        and payload.get("cluster_id") == receipt.cluster_id
        and payload.get("owner_node_id") == receipt.owner_node_id
        and payload.get("node_id") == receipt.owner_node_id
        and payload.get("generation_id") == receipt.generation_id
        and payload.get("target_node_id") == target_node_id
        and payload.get("promotion_receipt_id") == receipt.receipt_id
        and isinstance(payload.get("request_id"), str)
        and bool(payload["request_id"])
        and isinstance(payload.get("requested_at"), (int, float))
        and not isinstance(payload.get("requested_at"), bool)
        and math.isfinite(float(payload["requested_at"]))
    ):
        raise ValueError("VM-HA rearm request is invalid or stale")
    return RearmRetryRequest(
        cluster_id=str(payload["cluster_id"]),
        owner_node_id=str(payload["owner_node_id"]),
        node_id=str(payload["node_id"]),
        generation_id=str(payload["generation_id"]),
        target_node_id=str(payload["target_node_id"]),
        promotion_receipt_id=str(payload["promotion_receipt_id"]),
        request_id=str(payload["request_id"]),
        requested_at=float(payload["requested_at"]),
    )


def _consume_request(path: Path, expected: RearmRetryRequest) -> bool:
    """Compare and durably consume one exact retry authority."""

    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload != _retry_request_payload(expected):
        return False
    _durably_unlink(path)
    return True


def _acquire_rearm_lock(path: Path, *, timeout_seconds: float) -> int | None:
    """Acquire the single rearm writer lock within a monotonic deadline."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                return None
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))


def _retry_request_payload(request: RearmRetryRequest) -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-rearm-request-v1",
        "cluster_id": request.cluster_id,
        "owner_node_id": request.owner_node_id,
        "node_id": request.node_id,
        "generation_id": request.generation_id,
        "target_node_id": request.target_node_id,
        "promotion_receipt_id": request.promotion_receipt_id,
        "request_id": request.request_id,
        "requested_at": request.requested_at,
    }


class RearmRuntime:
    """Serialize one idempotent Compute start after a committed promotion."""

    def __init__(
        self,
        *,
        binding: VMHARuntimeBinding,
        local: VMHARuntimeNodeBinding,
        peer: VMHARuntimeNodeBinding,
        cloud: RearmCloudPort,
        state_dir: Path = STATE_DIR,
        enabled_path: Path = ENABLED_PATH,
        clock: Callable[[], float] = time.time,
        auto_healing_inhibition_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.binding = binding
        self.local = local
        self.peer = peer
        self.cloud = cloud
        self.state_dir = state_dir
        self.enabled_path = enabled_path
        self.clock = clock
        self.auto_healing_inhibition_provider = auto_healing_inhibition_provider

    def _status(
        self,
        phase: str,
        reason: str | None,
        *,
        receipt: PromotionReceipt | None = None,
        checkpoint: RearmCheckpoint | None = None,
        persist: bool = True,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "nebius-vpngw/vm-ha-rearm-status-v1",
            "phase": phase,
            "reason": reason,
            "promotion_receipt_id": None if receipt is None else receipt.receipt_id,
            "operation_id": None if checkpoint is None else checkpoint.operation_id,
            "completed_at": None if checkpoint is None else checkpoint.completed_at,
            "updated_at": self.clock(),
        }
        if persist:
            _atomic_write_json(self.state_dir / REARM_STATUS_PATH.name, payload)
        return payload

    def _inhibition_reason(self) -> str | None:
        if not self.enabled_path.exists():
            return "vm-ha-removal-or-disable-active"
        if (self.state_dir / APPLY_LOCK_PATH.name).exists():
            return "apply-lock-held"
        if (
            ManagedMTLSStore(self.state_dir / "mtls", create=False).inhibition_operation_id()
            is not None
        ):
            return "mtls-rotation-active"
        if (
            standby_replacement_inhibition_operation_id(
                self.state_dir,
                cluster_id=self.binding.cluster_id,
                node_id=self.local.node_id,
                generation_id=self.binding.generation_id,
            )
            is not None
        ):
            return "standby-replacement-active"
        return None

    def _auto_healing_inhibition_reason(self) -> str | None:
        if self.auto_healing_inhibition_provider is not None:
            return self.auto_healing_inhibition_provider()
        try:
            record = AutoHealingPolicyStore(self.state_dir).require_bound(
                cluster_id=self.binding.cluster_id,
                node_id=self.local.node_id,
                peer_node_id=self.peer.node_id,
                generation_id=self.binding.generation_id,
            )
            if record.phase is AutoHealingPolicyPhase.PREPARED:
                return "standby-auto-healing-policy-transition"
            if record.desired is StandbyAutoHealing.DISABLED:
                return "standby-auto-healing-policy-disabled"
            peer = load_peer_policy_heartbeat(
                self.state_dir,
                peer_node_id=self.peer.node_id,
            )
            if not peer_policy_agrees(record, peer, now=self.clock()):
                return "standby-auto-healing-peer-policy-unavailable"
        except AutoHealingPolicyError:
            return "standby-auto-healing-policy-invalid"
        return None

    def _matching_policy_recovery(
        self,
        *,
        receipt: PromotionReceipt,
        target: ComputeObservation,
        rearm_operation_id: str,
    ) -> AutoHealingRecoveryRecord | None:
        """Validate the one policy-owned exception to ordinary start admission."""

        try:
            recovery = AutoHealingRecoveryStore(self.state_dir).load()
        except AutoHealingPolicyError as error:
            raise AutoHealingPolicyError(AutoHealingRecoveryReason.INVALID.value) from error
        if recovery is None or recovery.phase is AutoHealingRecoveryPhase.COMPLETED:
            return None
        if not (
            recovery.cluster_id == self.binding.cluster_id
            and recovery.node_id == self.local.node_id
            and recovery.target_node_id == self.peer.node_id
            and recovery.generation_id == self.binding.generation_id
            and recovery.promotion_receipt_id == receipt.receipt_id
            and recovery.allocation_id == receipt.allocation_id
            and recovery.ownership_epoch == receipt.ownership_epoch
            and (
                (
                    recovery.phase is AutoHealingRecoveryPhase.ARMED
                    and recovery.stopped_revision == target.resource_version
                )
                or (
                    recovery.phase is AutoHealingRecoveryPhase.CONSUMED
                    and recovery.rearm_operation_id == rearm_operation_id
                    and rearm_operation_id
                    == _operation_id(
                        receipt.receipt_id,
                        self.peer.node_id,
                        recovery.stopped_revision,
                    )
                )
            )
        ):
            raise AutoHealingPolicyError(AutoHealingRecoveryReason.AUTHORITY_STALE_OR_FOREIGN.value)
        try:
            policy = AutoHealingPolicyStore(self.state_dir).require_bound(
                cluster_id=self.binding.cluster_id,
                node_id=self.local.node_id,
                peer_node_id=self.peer.node_id,
                generation_id=self.binding.generation_id,
            )
        except AutoHealingPolicyError as error:
            raise AutoHealingPolicyError(
                AutoHealingRecoveryReason.POLICY_UNAVAILABLE.value
            ) from error
        terminal_disabled = bool(
            policy.phase is AutoHealingPolicyPhase.COMMITTED
            and policy.desired is StandbyAutoHealing.DISABLED
            and policy.peer_ack_digest == policy.decision_digest
            and recovery.desired is StandbyAutoHealing.ENABLED
            and recovery.policy_digest == policy.decision_digest
            and recovery.predecessor_digest == policy.decision_digest
        )
        partial_same_operation = bool(
            policy.operation_id == recovery.operation_id
            and policy.desired is recovery.desired
            and policy.phase in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
            and recovery.policy_digest == policy.decision_digest
            and recovery.predecessor_digest == policy.predecessor_digest
        )
        if not (terminal_disabled or partial_same_operation):
            raise AutoHealingPolicyError(AutoHealingRecoveryReason.POLICY_CHANGED.value)
        return recovery

    def _matching_restoration_authority(
        self,
        *,
        receipt: PromotionReceipt,
    ) -> StandbyRestorationAuthorization | None:
        """Return only exact committed transfer-bound restoration authority."""

        record = StandbyRestorationStore(self.state_dir).load()
        if record is None or record.phase is RestorationPhase.COMPLETED:
            return None
        if not (
            record.cluster_id == receipt.cluster_id
            and record.owner_node_id == self.local.node_id == receipt.owner_node_id
            and record.former_owner_node_id == self.peer.node_id == receipt.former_owner_node_id
            and record.allocation_id == receipt.allocation_id
            and record.generation_id == receipt.generation_id
            and dict(record.digests) == dict(receipt.digests)
            and record.promotion_receipt_id == receipt.receipt_id
            and record.ownership_epoch == receipt.ownership_epoch
            and record.route_operation_id == receipt.route_operation_id
        ):
            raise StandbyRestorationError(
                "standby restoration authority is stale or foreign",
                reason=StandbyRestorationReason.AUTHORITY_STALE_OR_FOREIGN,
            )
        try:
            policy = AutoHealingPolicyStore(self.state_dir).require_bound(
                cluster_id=self.binding.cluster_id,
                node_id=self.local.node_id,
                peer_node_id=self.peer.node_id,
                generation_id=self.binding.generation_id,
            )
        except AutoHealingPolicyError as error:
            raise StandbyRestorationError(
                "standby restoration policy authority is unavailable",
                reason=StandbyRestorationReason.POLICY_UNAVAILABLE,
            ) from error
        if not policy_authorizes_restoration(policy, record):
            raise StandbyRestorationError(
                "standby restoration policy authority changed",
                reason=StandbyRestorationReason.POLICY_CHANGED,
            )
        if record.phase is RestorationPhase.BLOCKED:
            raise StandbyRestorationError(
                record.blocked_reason or "standby restoration is blocked",
                reason=StandbyRestorationReason(
                    blocked_restoration_reason_code(record.blocked_reason)
                ),
            )
        if record.phase is RestorationPhase.ARMED:
            raise StandbyRestorationError(
                "standby restoration authority is not committed",
                reason=StandbyRestorationReason.NOT_COMMITTED,
            )
        return record

    def _policy_start_admission(
        self,
        *,
        receipt: PromotionReceipt,
        target: ComputeObservation,
        rearm_operation_id: str,
    ) -> AutoHealingRecoveryRecord | None:
        # A promotion-committed authorization captures the fresh two-member
        # agreement before the former owner is deliberately stopped.  It is
        # the only path that may bypass the now-impossible live-heartbeat age
        # check, and it remains bound to this exact receipt and generation.
        try:
            if self._matching_restoration_authority(receipt=receipt) is not None:
                return None
        except StandbyRestorationError as error:
            raise AutoHealingPolicyError(error.reason_code) from error
        recovery = self._matching_policy_recovery(
            receipt=receipt,
            target=target,
            rearm_operation_id=rearm_operation_id,
        )
        if recovery is not None:
            return recovery
        inhibition = self._auto_healing_inhibition_reason()
        if inhibition is not None:
            raise AutoHealingPolicyError(inhibition)
        return None

    def _stable_owner_observation(
        self, receipt: PromotionReceipt
    ) -> tuple[ComputeObservation, ComputeObservation]:
        allocation = self.cloud.observe_allocation(receipt.allocation_id)
        owner = self.cloud.observe_instance(self.local.compute_id)
        target = self.cloud.observe_instance(self.peer.compute_id)
        exact_owner = AllocationOwner(self.local.compute_id, self.local.network_interface_name)
        if not (
            allocation.owner == exact_owner
            and owner.state is InstanceCloudState.RUNNING
            and owner.resource_version == receipt.ownership_epoch
            and owner.has_alias_allocation(self.local.network_interface_name, receipt.allocation_id)
            and not target.has_alias_allocation(
                self.peer.network_interface_name, receipt.allocation_id
            )
        ):
            raise RuntimeError("VM-HA rearm ownership or alias evidence drifted")
        return owner, target

    def step(self) -> dict[str, object]:
        lock = _acquire_rearm_lock(
            self.state_dir / REARM_LOCK_PATH.name,
            timeout_seconds=0.0,
        )
        if lock is None:
            return self._status(
                "inhibited",
                "rearm-writer-busy",
                persist=False,
            )
        try:
            inhibition = self._inhibition_reason()
            if inhibition is not None:
                return self._status("inhibited", inhibition)
            try:
                receipt = _promotion_receipt(
                    self.state_dir / PROMOTION_RECEIPT_PATH.name,
                    binding=self.binding,
                    local=self.local,
                    peer=self.peer,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                return self._status("blocked", "promotion-receipt-invalid")
            if receipt is None:
                return self._status("idle", "promotion-receipt-unavailable")
            try:
                _owner, target = self._stable_owner_observation(receipt)
            except RuntimeError:
                return self._status("blocked", "ownership-or-alias-drift", receipt=receipt)
            checkpoint_path = self.state_dir / REARM_CHECKPOINT_PATH.name
            try:
                checkpoint = _read_checkpoint(checkpoint_path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                return self._status("blocked", "rearm-checkpoint-invalid", receipt=receipt)
            try:
                retry_request = _request_matches(
                    self.state_dir / REARM_REQUEST_PATH.name,
                    receipt=receipt,
                    target_node_id=self.peer.node_id,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                return self._status(
                    "blocked",
                    "rearm-request-invalid",
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            try:
                accepted_operation = self.cloud.accepted_operation()
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                return self._status(
                    "blocked",
                    "cloud-operation-journal-invalid",
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            if accepted_operation is not None:
                if not (
                    checkpoint is not None
                    and checkpoint.target_node_id == self.peer.node_id
                    and checkpoint.operation_id == accepted_operation.action_operation_id
                    and checkpoint.phase in {"start-requested", "starting", "running", "blocked"}
                    and accepted_operation.kind == "start-instance"
                ):
                    return self._status(
                        "blocked",
                        "cloud-operation-journal-unbound",
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
                try:
                    self.cloud.finalize_accepted_start(accepted_operation)
                except Exception:
                    return self._status(
                        "blocked",
                        "cloud-operation-finalization-failed",
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
                try:
                    _owner, target = self._stable_owner_observation(receipt)
                except RuntimeError:
                    return self._status(
                        "blocked",
                        "ownership-or-alias-drift",
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
            if target.state is InstanceCloudState.RUNNING:
                if (
                    checkpoint is not None
                    and checkpoint.receipt_id == receipt.receipt_id
                    and (
                        checkpoint.target_node_id != self.peer.node_id
                        or checkpoint.operation_id
                        != _operation_id(
                            receipt.receipt_id,
                            self.peer.node_id,
                            checkpoint.stopped_revision,
                        )
                    )
                ):
                    return self._status(
                        "blocked",
                        "rearm-checkpoint-invalid",
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
                if checkpoint is None or checkpoint.receipt_id != receipt.receipt_id:
                    checkpoint = RearmCheckpoint(
                        receipt_id=receipt.receipt_id,
                        target_node_id=self.peer.node_id,
                        stopped_revision=target.resource_version,
                        operation_id=_operation_id(
                            receipt.receipt_id, self.peer.node_id, target.resource_version
                        ),
                        phase="running",
                        attempted_at=self.clock(),
                        completed_at=self.clock(),
                    )
                elif checkpoint.phase != "running":
                    checkpoint = RearmCheckpoint(
                        **{
                            **checkpoint.__dict__,
                            "phase": "running",
                            "completed_at": self.clock(),
                            "failure_reason": None,
                        }
                    )
                _write_checkpoint(checkpoint_path, checkpoint)
                try:
                    restoration = self._matching_restoration_authority(receipt=receipt)
                    if restoration is not None:
                        restoration_store = StandbyRestorationStore(self.state_dir)
                        if restoration.phase is RestorationPhase.COMMITTED:
                            restoration = restoration_store.accept_start(
                                receipt_id=receipt.receipt_id,
                                stopped_revision=checkpoint.stopped_revision,
                                rearm_operation_id=checkpoint.operation_id,
                                updated_at=self.clock(),
                            )
                        if restoration.phase is RestorationPhase.START_ACCEPTED:
                            restoration_store.mark_running(
                                receipt_id=receipt.receipt_id,
                                updated_at=self.clock(),
                            )
                            restoration = restoration_store.require_receipt(receipt.receipt_id)
                        if restoration.phase is RestorationPhase.RUNNING:
                            restoration_store.await_standby(
                                receipt_id=receipt.receipt_id,
                                updated_at=self.clock(),
                            )
                except StandbyRestorationError as error:
                    return self._status(
                        "blocked",
                        error.reason_code,
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
                try:
                    recovery = self._matching_policy_recovery(
                        receipt=receipt,
                        target=target,
                        rearm_operation_id=checkpoint.operation_id,
                    )
                    if recovery is not None:
                        recovery_store = AutoHealingRecoveryStore(self.state_dir)
                        recovery_store.consume(
                            operation_id=recovery.operation_id,
                            rearm_operation_id=checkpoint.operation_id,
                            updated_at=self.clock(),
                        )
                        recovery_store.complete(
                            operation_id=recovery.operation_id,
                            rearm_operation_id=checkpoint.operation_id,
                            updated_at=self.clock(),
                        )
                except AutoHealingPolicyError:
                    return self._status(
                        "blocked",
                        AutoHealingRecoveryReason.INVALID.value,
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
                if retry_request is not None:
                    try:
                        consumed = _consume_request(
                            self.state_dir / REARM_REQUEST_PATH.name,
                            retry_request,
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError):
                        consumed = False
                    if not consumed:
                        return self._status(
                            "blocked",
                            "rearm-request-changed",
                            receipt=receipt,
                            checkpoint=checkpoint,
                        )
                return self._status("running", None, receipt=receipt, checkpoint=checkpoint)
            if target.state in {
                InstanceCloudState.ERROR,
                InstanceCloudState.STOPPING,
                InstanceCloudState.UNKNOWN,
            }:
                return self._status(
                    "blocked", f"target-compute-{target.state.value}", receipt=receipt
                )
            if target.state is InstanceCloudState.TRANSITIONAL:
                if (
                    checkpoint is not None
                    and checkpoint.receipt_id == receipt.receipt_id
                    and checkpoint.target_node_id == self.peer.node_id
                    and checkpoint.phase in {"start-requested", "starting"}
                ):
                    try:
                        self._policy_start_admission(
                            receipt=receipt,
                            target=target,
                            rearm_operation_id=checkpoint.operation_id,
                        )
                    except AutoHealingPolicyError as error:
                        return self._status(
                            "inhibited",
                            str(error),
                            receipt=receipt,
                            checkpoint=checkpoint,
                        )
                    inhibition = self._inhibition_reason()
                    if inhibition is not None:
                        return self._status(
                            "inhibited",
                            inhibition,
                            receipt=receipt,
                            checkpoint=checkpoint,
                        )
                return self._status("starting", None, receipt=receipt, checkpoint=checkpoint)

            assert target.state is InstanceCloudState.STOPPED
            operation_id = _operation_id(
                receipt.receipt_id, self.peer.node_id, target.resource_version
            )
            try:
                recovery = self._policy_start_admission(
                    receipt=receipt,
                    target=target,
                    rearm_operation_id=operation_id,
                )
            except AutoHealingPolicyError as error:
                return self._status(
                    "inhibited",
                    str(error),
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            matching = bool(
                checkpoint is not None
                and checkpoint.receipt_id == receipt.receipt_id
                and checkpoint.target_node_id == self.peer.node_id
                and checkpoint.stopped_revision == target.resource_version
                and checkpoint.operation_id == operation_id
            )
            if (
                matching
                and checkpoint is not None
                and checkpoint.phase == "blocked"
                and retry_request is None
            ):
                return self._status(
                    "blocked", "explicit-retry-required", receipt=receipt, checkpoint=checkpoint
                )
            if not matching or checkpoint is None or retry_request is not None:
                checkpoint = RearmCheckpoint(
                    receipt_id=receipt.receipt_id,
                    target_node_id=self.peer.node_id,
                    stopped_revision=target.resource_version,
                    operation_id=operation_id,
                    phase="start-requested",
                    attempted_at=self.clock(),
                )
                _write_checkpoint(checkpoint_path, checkpoint)
            if retry_request is not None:
                try:
                    consumed = _consume_request(
                        self.state_dir / REARM_REQUEST_PATH.name,
                        retry_request,
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError):
                    consumed = False
                if not consumed:
                    blocked = RearmCheckpoint(
                        **{
                            **checkpoint.__dict__,
                            "phase": "blocked",
                            "failure_reason": "rearm-request-changed",
                        }
                    )
                    _write_checkpoint(checkpoint_path, blocked)
                    return self._status(
                        "blocked",
                        "rearm-request-changed",
                        receipt=receipt,
                        checkpoint=blocked,
                    )
            inhibition = self._inhibition_reason()
            if inhibition is not None:
                return self._status(
                    "inhibited",
                    inhibition,
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            try:
                recovery = self._policy_start_admission(
                    receipt=receipt,
                    target=target,
                    rearm_operation_id=operation_id,
                )
            except AutoHealingPolicyError as error:
                return self._status(
                    "inhibited",
                    str(error),
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            if recovery is not None:
                try:
                    AutoHealingRecoveryStore(self.state_dir).consume(
                        operation_id=recovery.operation_id,
                        rearm_operation_id=operation_id,
                        updated_at=self.clock(),
                    )
                except AutoHealingPolicyError:
                    return self._status(
                        "blocked",
                        AutoHealingRecoveryReason.CONSUME_FAILED.value,
                        receipt=receipt,
                        checkpoint=checkpoint,
                    )
            restoration_store = StandbyRestorationStore(self.state_dir)
            try:
                restoration = self._matching_restoration_authority(receipt=receipt)
                if restoration is not None:
                    if restoration.phase is RestorationPhase.COMMITTED:
                        restoration = restoration_store.accept_start(
                            receipt_id=receipt.receipt_id,
                            stopped_revision=target.resource_version,
                            rearm_operation_id=operation_id,
                            updated_at=self.clock(),
                        )
                    elif not (
                        restoration.phase is RestorationPhase.START_ACCEPTED
                        and restoration.stopped_revision == target.resource_version
                        and restoration.rearm_operation_id == operation_id
                    ):
                        raise StandbyRestorationError(
                            "standby restoration start identity changed",
                            reason=StandbyRestorationReason.START_IDENTITY_CHANGED,
                        )
                    if (
                        restoration.next_attempt_at is not None
                        and self.clock() < restoration.next_attempt_at
                    ):
                        return self._status(
                            "starting",
                            "compute-start-retry-scheduled",
                            receipt=receipt,
                            checkpoint=checkpoint,
                        )
            except StandbyRestorationError as error:
                return self._status(
                    "blocked",
                    error.reason_code,
                    receipt=receipt,
                    checkpoint=checkpoint,
                )
            starting = RearmCheckpoint(**{**checkpoint.__dict__, "phase": "starting"})
            _write_checkpoint(checkpoint_path, starting)
            self._status("starting", None, receipt=receipt, checkpoint=starting)
            try:
                if restoration is not None:
                    restoration_store.record_submission(
                        receipt_id=receipt.receipt_id,
                        updated_at=self.clock(),
                    )
                self.cloud.start_instance(self.peer.compute_id, starting.operation_id)
            except PermanentHACloudError as error:
                if restoration is not None:
                    restoration_store.block(
                        receipt_id=receipt.receipt_id,
                        reason="compute-start-permanent-failure",
                        updated_at=self.clock(),
                    )
                blocked = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": "blocked",
                        "failure_reason": type(error).__name__,
                    }
                )
                _write_checkpoint(checkpoint_path, blocked)
                return self._status(
                    "blocked",
                    "compute-start-permanent-failure",
                    receipt=receipt,
                    checkpoint=blocked,
                )
            except (RetryableHACloudError, AmbiguousHACloudError) as error:
                if restoration is None:
                    raise
                scheduled = restoration_store.schedule_retry(
                    receipt_id=receipt.receipt_id,
                    reason=type(error).__name__,
                    updated_at=self.clock(),
                )
                retrying = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": (
                            "blocked"
                            if scheduled.phase is RestorationPhase.BLOCKED
                            else "start-requested"
                        ),
                        "failure_reason": type(error).__name__,
                    }
                )
                _write_checkpoint(checkpoint_path, retrying)
                return self._status(
                    ("blocked" if scheduled.phase is RestorationPhase.BLOCKED else "starting"),
                    (
                        scheduled.blocked_reason
                        if scheduled.phase is RestorationPhase.BLOCKED
                        else "compute-start-retry-scheduled"
                    ),
                    receipt=receipt,
                    checkpoint=retrying,
                )
            except Exception as error:
                if restoration is not None:
                    restoration_store.block(
                        receipt_id=receipt.receipt_id,
                        reason="compute-start-failed",
                        updated_at=self.clock(),
                    )
                blocked = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": "blocked",
                        "failure_reason": type(error).__name__,
                    }
                )
                _write_checkpoint(checkpoint_path, blocked)
                return self._status(
                    "blocked",
                    "compute-start-failed",
                    receipt=receipt,
                    checkpoint=blocked,
                )
            _owner, target = self._stable_owner_observation(receipt)
            if target.state is InstanceCloudState.RUNNING:
                if restoration is not None:
                    restoration_store.mark_running(
                        receipt_id=receipt.receipt_id,
                        updated_at=self.clock(),
                    )
                    restoration_store.await_standby(
                        receipt_id=receipt.receipt_id,
                        updated_at=self.clock(),
                    )
                running = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": "running",
                        "completed_at": self.clock(),
                    }
                )
                _write_checkpoint(checkpoint_path, running)
                if recovery is not None:
                    try:
                        AutoHealingRecoveryStore(self.state_dir).complete(
                            operation_id=recovery.operation_id,
                            rearm_operation_id=operation_id,
                            updated_at=self.clock(),
                        )
                    except AutoHealingPolicyError:
                        return self._status(
                            "blocked",
                            AutoHealingRecoveryReason.COMPLETION_FAILED.value,
                            receipt=receipt,
                            checkpoint=running,
                        )
                return self._status("running", None, receipt=receipt, checkpoint=running)
            if target.state is not InstanceCloudState.TRANSITIONAL:
                blocked = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": "blocked",
                        "failure_reason": f"target-compute-{target.state.value}",
                    }
                )
                _write_checkpoint(checkpoint_path, blocked)
                return self._status(
                    "blocked", blocked.failure_reason, receipt=receipt, checkpoint=blocked
                )
            return self._status("starting", None, receipt=receipt, checkpoint=starting)
        finally:
            os.close(lock)


@dataclass
class DefaultRearmRuntime:
    runtime: RearmRuntime
    sdk: RenewableNebiusSDK

    def step(self) -> dict[str, object]:
        return self.runtime.step()

    def close(self) -> None:
        self.sdk.close()


def _read_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("vm_ha"), dict):
        raise RuntimeError("VM HA is not enabled on this node")
    config = dict(payload["vm_ha"])
    if config.get("enabled") is False:
        raise RuntimeError("VM HA is not enabled on this node")
    return config


def build_default_rearm_runtime(
    *,
    config_path: Path = CONFIG_PATH,
    state_dir: Path = STATE_DIR,
    sdk_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.time,
) -> DefaultRearmRuntime:
    current_boot_id = _boot_id()
    begin_runtime_identity_preflight(state_dir=state_dir, boot_id=current_boot_id)
    config = _read_config(config_path)
    binding = VMHARuntimeBinding.model_validate(config.get("runtime_binding"))
    node = config.get("node") or {}
    local = next(item for item in binding.nodes if item.node_id == node.get("node_id"))
    peer = next(item for item in binding.nodes if item.node_id != local.node_id)
    if not runtime_identity_binding_is_explicit(binding, local):
        record_runtime_identity_migration_required(
            state_dir=state_dir,
            boot_id=current_boot_id,
        )
        raise RuntimeError("VM-HA runtime credential identity is migration-required")
    credential_bundle = validate_installed_credential_bundle(binding, local)
    if sdk_factory is None:
        sdk = build_identity_bound_sdk(
            binding=binding,
            local=local,
            credential_bundle=credential_bundle,
            state_dir=state_dir,
            boot_id=current_boot_id,
        )
    else:
        sdk = build_identity_bound_sdk(
            binding=binding,
            local=local,
            credential_bundle=credential_bundle,
            state_dir=state_dir,
            boot_id=current_boot_id,
            factory=sdk_factory,
        )
    try:
        journal = VMHACloudOperationJournal(state_dir / REARM_OPERATION_PATH.name)
        cloud = SDKRearmCloudPort(sdk.client, journal=journal)
        runtime = RearmRuntime(
            binding=binding,
            local=local,
            peer=peer,
            cloud=cloud,
            state_dir=state_dir,
            clock=clock,
        )
        return DefaultRearmRuntime(runtime, sdk)
    except Exception:
        sdk.close()
        raise


def request_rearm_retry(
    *, config_path: Path = CONFIG_PATH, state_dir: Path = STATE_DIR
) -> dict[str, object]:
    """Submit role-neutral retry intent on the exact current owner."""

    config = _read_config(config_path)
    binding = VMHARuntimeBinding.model_validate(config.get("runtime_binding"))
    node = config.get("node") or {}
    local = next(item for item in binding.nodes if item.node_id == node.get("node_id"))
    peer = next(item for item in binding.nodes if item.node_id != local.node_id)
    lock = _acquire_rearm_lock(
        state_dir / REARM_LOCK_PATH.name,
        timeout_seconds=5.0,
    )
    if lock is None:
        raise RuntimeError("VM-HA rearm retry writer is busy")
    try:
        if (state_dir / APPLY_LOCK_PATH.name).exists():
            raise RuntimeError("VM-HA rearm retry is inhibited by an apply lock")
        if ManagedMTLSStore(state_dir / "mtls", create=False).inhibition_operation_id() is not None:
            raise RuntimeError("VM-HA rearm retry is inhibited by mTLS rotation")
        if (
            standby_replacement_inhibition_operation_id(
                state_dir,
                cluster_id=binding.cluster_id,
                node_id=local.node_id,
                generation_id=binding.generation_id,
            )
            is not None
        ):
            raise RuntimeError("VM-HA rearm retry is inhibited by standby replacement")
        receipt = _promotion_receipt(
            state_dir / PROMOTION_RECEIPT_PATH.name,
            binding=binding,
            local=local,
            peer=peer,
        )
        if receipt is None:
            raise RuntimeError("VM-HA rearm retry requires a committed promotion receipt")
        restoration_store = StandbyRestorationStore(state_dir)
        restoration = restoration_store.load()
        if restoration is not None and restoration.phase is RestorationPhase.BLOCKED:
            try:
                policy = AutoHealingPolicyStore(state_dir).require_bound(
                    cluster_id=binding.cluster_id,
                    node_id=local.node_id,
                    peer_node_id=peer.node_id,
                    generation_id=binding.generation_id,
                )
            except AutoHealingPolicyError as error:
                raise RuntimeError(
                    "blocked standby restoration requires committed enabled policy"
                ) from error
            if not (
                policy.phase is AutoHealingPolicyPhase.COMMITTED
                and policy.desired is StandbyAutoHealing.ENABLED
                and policy_authorizes_restoration(policy, restoration)
            ):
                raise RuntimeError("blocked standby restoration requires committed enabled policy")
            restoration_store.restart_blocked(
                receipt_id=receipt.receipt_id,
                updated_at=time.time(),
            )
        request_path = state_dir / REARM_REQUEST_PATH.name
        existing = _request_matches(
            request_path,
            receipt=receipt,
            target_node_id=peer.node_id,
        )
        if existing is not None:
            return _retry_request_payload(existing)
        requested_at = time.time()
        request = RearmRetryRequest(
            cluster_id=receipt.cluster_id,
            owner_node_id=receipt.owner_node_id,
            node_id=receipt.owner_node_id,
            generation_id=receipt.generation_id,
            target_node_id=peer.node_id,
            promotion_receipt_id=receipt.receipt_id,
            request_id=hashlib.sha256(
                (
                    f"vm-ha-rearm-retry-v1\0{receipt.receipt_id}\0"
                    f"{requested_at}\0{uuid.uuid4().hex}"
                ).encode()
            ).hexdigest(),
            requested_at=requested_at,
        )
        payload = _retry_request_payload(request)
        _atomic_write_json(request_path, payload)
        return payload
    finally:
        os.close(lock)


def run_rearm_service(
    runtime: DefaultRearmRuntime | RearmRuntime | None = None,
    *,
    once: bool = False,
    interval_seconds: float = 1.0,
) -> None:
    active = runtime or build_default_rearm_runtime()
    last_transition: tuple[object, object, object, object] | None = None
    try:
        while True:
            status = active.step()
            attempt_count: int | None = None
            runtime_state_dir = getattr(getattr(active, "runtime", active), "state_dir", None)
            if isinstance(runtime_state_dir, Path):
                try:
                    restoration = StandbyRestorationStore(runtime_state_dir).load()
                except StandbyRestorationError:
                    restoration = None
                if restoration is not None:
                    attempt_count = restoration.attempt_count
            transition = (
                status.get("phase"),
                status.get("reason"),
                status.get("operation_id"),
                attempt_count,
            )
            if transition != last_transition:
                print(
                    "[VM-HA-REARM] "
                    + json.dumps(
                        {
                            "attempt": transition[3],
                            "phase": transition[0],
                            "reason": transition[1],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                last_transition = transition
            if once:
                return
            time.sleep(interval_seconds)
    finally:
        close = getattr(active, "close", None)
        if callable(close):
            close()
