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
    validate_installed_credential_bundle,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AcceptedCloudOperation,
    AllocationObservation,
    AllocationOwner,
    ComputeObservation,
    InstanceCloudState,
    NebiusSDKCloudClient,
    VMHACloudOperationJournal,
    allocation_observation,
    compute_observation,
)
from nebius_vpngw.schema import VMHARuntimeBinding, VMHARuntimeNodeBinding

from .vm_ha.mtls import ManagedMTLSStore

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
    ) -> None:
        self.binding = binding
        self.local = local
        self.peer = peer
        self.cloud = cloud
        self.state_dir = state_dir
        self.enabled_path = enabled_path
        self.clock = clock

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
                    and checkpoint.phase
                    in {"start-requested", "starting", "running", "blocked"}
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
                    inhibition = self._inhibition_reason()
                    if inhibition is not None:
                        return self._status(
                            "inhibited",
                            inhibition,
                            receipt=receipt,
                            checkpoint=checkpoint,
                        )
                    try:
                        self.cloud.start_instance(self.peer.compute_id, checkpoint.operation_id)
                    except Exception as error:
                        blocked = RearmCheckpoint(
                            **{
                                **checkpoint.__dict__,
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
                return self._status("starting", None, receipt=receipt, checkpoint=checkpoint)

            assert target.state is InstanceCloudState.STOPPED
            operation_id = _operation_id(
                receipt.receipt_id, self.peer.node_id, target.resource_version
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
                self.cloud.start_instance(self.peer.compute_id, checkpoint.operation_id)
            except Exception as error:
                blocked = RearmCheckpoint(
                    **{
                        **checkpoint.__dict__,
                        "phase": "blocked",
                        "failure_reason": type(error).__name__,
                    }
                )
                _write_checkpoint(checkpoint_path, blocked)
                self._status("blocked", "compute-start-failed", receipt=receipt, checkpoint=blocked)
                raise
            starting = RearmCheckpoint(**{**checkpoint.__dict__, "phase": "starting"})
            _write_checkpoint(checkpoint_path, starting)
            _owner, target = self._stable_owner_observation(receipt)
            if target.state is InstanceCloudState.RUNNING:
                running = RearmCheckpoint(
                    **{
                        **starting.__dict__,
                        "phase": "running",
                        "completed_at": self.clock(),
                    }
                )
                _write_checkpoint(checkpoint_path, running)
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
    config = _read_config(config_path)
    binding = VMHARuntimeBinding.model_validate(config.get("runtime_binding"))
    node = config.get("node") or {}
    local = next(item for item in binding.nodes if item.node_id == node.get("node_id"))
    peer = next(item for item in binding.nodes if item.node_id != local.node_id)
    credential_bundle = validate_installed_credential_bundle(binding, local)
    sdk = (
        RenewableNebiusSDK(
            local.nebius_credentials_path,
            credential_check=credential_bundle.revalidate,
        )
        if sdk_factory is None
        else RenewableNebiusSDK(
            local.nebius_credentials_path,
            factory=sdk_factory,
            credential_check=credential_bundle.revalidate,
        )
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
        if (
            ManagedMTLSStore(state_dir / "mtls", create=False).inhibition_operation_id()
            is not None
        ):
            raise RuntimeError("VM-HA rearm retry is inhibited by mTLS rotation")
        receipt = _promotion_receipt(
            state_dir / PROMOTION_RECEIPT_PATH.name,
            binding=binding,
            local=local,
            peer=peer,
        )
        if receipt is None:
            raise RuntimeError("VM-HA rearm retry requires a committed promotion receipt")
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
    try:
        while True:
            active.step()
            if once:
                return
            time.sleep(interval_seconds)
    finally:
        close = getattr(active, "close", None)
        if callable(close):
            close()
