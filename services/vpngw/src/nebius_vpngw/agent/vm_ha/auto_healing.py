"""Strict durable policy for automatic VM-HA standby restoration."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .models import PeerHeartbeat, canonical_json
from .store import AtomicGenerationStore, CorruptStateError, atomic_write_json

AUTO_HEALING_POLICY_SCHEMA = "nebius-vpngw/vm-ha-standby-auto-healing-policy-v2"
AUTO_HEALING_STATUS_SCHEMA = "nebius-vpngw/vm-ha-auto-healing-policy-status-v2"
AUTO_HEALING_POLICY_FILENAME = "standby-auto-healing-policy.json"
AUTO_HEALING_RECOVERY_SCHEMA = "nebius-vpngw/vm-ha-auto-healing-recovery-v1"
AUTO_HEALING_RECOVERY_FILENAME = "standby-auto-healing-recovery.json"
AUTO_HEALING_CAPABILITY = "vm-ha-standby-auto-healing-policy-v4"
AUTO_HEALING_REQUEST_SCHEMA = "nebius-vpngw/vm-ha-auto-healing-policy-request-v3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_BLOCKED_DIGEST = "0" * 64


class StandbyAutoHealing(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AutoHealingPolicyPhase(str, Enum):
    PREPARED = "prepared"
    COMMITTED = "committed"


class AutoHealingRecoveryPhase(str, Enum):
    ARMED = "armed"
    CONSUMED = "consumed"
    COMPLETED = "completed"


class AutoHealingRecoveryReason(str, Enum):
    """Closed, identity-free recovery reasons safe for VM-HA status."""

    INVALID = "standby-auto-healing-recovery-invalid"
    AUTHORITY_STALE_OR_FOREIGN = "standby-auto-healing-recovery-authority-stale-or-foreign"
    POLICY_UNAVAILABLE = "standby-auto-healing-recovery-policy-unavailable"
    POLICY_CHANGED = "standby-auto-healing-recovery-policy-changed"
    CONSUME_FAILED = "standby-auto-healing-recovery-consume-failed"
    COMPLETION_FAILED = "standby-auto-healing-recovery-completion-failed"


class AutoHealingPolicyError(RuntimeError):
    """A policy record or transaction is unsafe to use."""


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise AutoHealingPolicyError(f"{name} is invalid")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise AutoHealingPolicyError(f"{name} is invalid")
    return value


def _require_timestamp(value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise AutoHealingPolicyError("updated_at is invalid")
    return float(value)


def policy_decision_digest(
    *,
    cluster_id: str,
    member_node_ids: tuple[str, str],
    generation_id: str,
    desired: StandbyAutoHealing,
    operation_id: str,
    coordinator_node_id: str,
    predecessor_digest: str,
) -> str:
    """Bind one policy decision to the exact cluster generation and members."""

    payload = {
        "cluster_id": _require_identifier("cluster_id", cluster_id),
        "desired": desired.value,
        "generation_id": _require_sha256("generation_id", generation_id),
        "member_node_ids": sorted(
            _require_identifier("member_node_id", node_id) for node_id in member_node_ids
        ),
        "operation_id": _require_sha256("operation_id", operation_id),
        "coordinator_node_id": _require_identifier("coordinator_node_id", coordinator_node_id),
        "predecessor_digest": _require_sha256("predecessor_digest", predecessor_digest),
        "schema": "nebius-vpngw/vm-ha-standby-auto-healing-decision-v2",
    }
    if len(set(payload["member_node_ids"])) != 2:
        raise AutoHealingPolicyError("policy requires exactly two distinct members")
    if payload["coordinator_node_id"] != min(payload["member_node_ids"]):
        raise AutoHealingPolicyError("policy coordinator is not deterministic")
    return hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class AutoHealingPolicyRecord:
    cluster_id: str
    node_id: str
    peer_node_id: str
    generation_id: str
    desired: StandbyAutoHealing
    operation_id: str
    coordinator_node_id: str
    predecessor_digest: str
    phase: AutoHealingPolicyPhase
    decision_digest: str
    peer_ack_digest: str | None
    updated_at: float

    def __post_init__(self) -> None:
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("node_id", self.node_id)
        _require_identifier("peer_node_id", self.peer_node_id)
        if self.node_id == self.peer_node_id:
            raise AutoHealingPolicyError("policy member identities must be distinct")
        _require_sha256("generation_id", self.generation_id)
        _require_sha256("operation_id", self.operation_id)
        _require_identifier("coordinator_node_id", self.coordinator_node_id)
        if self.coordinator_node_id != min(self.node_id, self.peer_node_id):
            raise AutoHealingPolicyError("policy coordinator is invalid")
        _require_sha256("predecessor_digest", self.predecessor_digest)
        _require_sha256("decision_digest", self.decision_digest)
        if self.peer_ack_digest is not None:
            _require_sha256("peer_ack_digest", self.peer_ack_digest)
            if self.peer_ack_digest != self.decision_digest:
                raise AutoHealingPolicyError("peer acknowledgement digest is invalid")
        _require_timestamp(self.updated_at)
        expected = policy_decision_digest(
            cluster_id=self.cluster_id,
            member_node_ids=(self.node_id, self.peer_node_id),
            generation_id=self.generation_id,
            desired=self.desired,
            operation_id=self.operation_id,
            coordinator_node_id=self.coordinator_node_id,
            predecessor_digest=self.predecessor_digest,
        )
        if self.decision_digest != expected:
            raise AutoHealingPolicyError("policy decision digest is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "cluster_id": self.cluster_id,
            "coordinator_node_id": self.coordinator_node_id,
            "decision_digest": self.decision_digest,
            "desired": self.desired.value,
            "generation_id": self.generation_id,
            "node_id": self.node_id,
            "operation_id": self.operation_id,
            "peer_ack_digest": self.peer_ack_digest,
            "peer_node_id": self.peer_node_id,
            "phase": self.phase.value,
            "predecessor_digest": self.predecessor_digest,
            "schema": AUTO_HEALING_POLICY_SCHEMA,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: object) -> AutoHealingPolicyRecord:
        if not isinstance(value, Mapping):
            raise AutoHealingPolicyError("policy record must be an object")
        expected = {
            "cluster_id",
            "coordinator_node_id",
            "decision_digest",
            "desired",
            "generation_id",
            "node_id",
            "operation_id",
            "peer_ack_digest",
            "peer_node_id",
            "phase",
            "predecessor_digest",
            "schema",
            "updated_at",
        }
        if set(value) != expected or value.get("schema") != AUTO_HEALING_POLICY_SCHEMA:
            raise AutoHealingPolicyError("policy record has an invalid shape")
        try:
            desired = StandbyAutoHealing(str(value["desired"]))
            phase = AutoHealingPolicyPhase(str(value["phase"]))
        except ValueError as error:
            raise AutoHealingPolicyError("policy record has an invalid state") from error
        return cls(
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            node_id=_require_identifier("node_id", value["node_id"]),
            peer_node_id=_require_identifier("peer_node_id", value["peer_node_id"]),
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            desired=desired,
            operation_id=_require_sha256("operation_id", value["operation_id"]),
            coordinator_node_id=_require_identifier(
                "coordinator_node_id", value["coordinator_node_id"]
            ),
            predecessor_digest=_require_sha256("predecessor_digest", value["predecessor_digest"]),
            phase=phase,
            decision_digest=_require_sha256("decision_digest", value["decision_digest"]),
            peer_ack_digest=(
                None
                if value["peer_ack_digest"] is None
                else _require_sha256("peer_ack_digest", value["peer_ack_digest"])
            ),
            updated_at=_require_timestamp(value["updated_at"]),
        )


class AutoHealingPolicyStore:
    """Atomic local participant in the two-member policy transaction."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / AUTO_HEALING_POLICY_FILENAME

    def load(self) -> AutoHealingPolicyRecord | None:
        if not self.path.exists():
            return None
        try:
            return AutoHealingPolicyRecord.from_mapping(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AutoHealingPolicyError("policy record is unreadable") from error

    def require_bound(
        self,
        *,
        cluster_id: str,
        node_id: str,
        peer_node_id: str,
        generation_id: str,
    ) -> AutoHealingPolicyRecord:
        record = self.load()
        if record is None:
            raise AutoHealingPolicyError("policy record is missing")
        if not (
            record.cluster_id == cluster_id
            and record.node_id == node_id
            and record.peer_node_id == peer_node_id
            and record.generation_id == generation_id
        ):
            raise AutoHealingPolicyError("policy record is stale or foreign")
        return record

    @staticmethod
    def _operation_id(*, cluster_id: str, generation_id: str, desired: StandbyAutoHealing) -> str:
        return hashlib.sha256(
            canonical_json(
                {
                    "cluster_id": cluster_id,
                    "desired": desired.value,
                    "generation_id": generation_id,
                    "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-v2",
                }
            ).encode("ascii")
        ).hexdigest()

    def initialize(
        self,
        *,
        cluster_id: str,
        node_id: str,
        peer_node_id: str,
        generation_id: str,
        updated_at: float,
    ) -> AutoHealingPolicyRecord:
        """Create enabled by default or rebind a committed choice to a generation."""

        existing = self.load()
        desired = StandbyAutoHealing.ENABLED
        if existing is not None:
            if not (
                existing.cluster_id == cluster_id
                and existing.node_id == node_id
                and existing.peer_node_id == peer_node_id
                and existing.phase is AutoHealingPolicyPhase.COMMITTED
            ):
                raise AutoHealingPolicyError("policy cannot be rebound from unsafe state")
            desired = existing.desired
            if existing.generation_id == generation_id:
                return existing
        operation_id = self._operation_id(
            cluster_id=cluster_id,
            generation_id=generation_id,
            desired=desired,
        )
        coordinator_node_id = min(node_id, peer_node_id)
        predecessor_digest = _BLOCKED_DIGEST if existing is None else existing.decision_digest
        decision_digest = policy_decision_digest(
            cluster_id=cluster_id,
            member_node_ids=(node_id, peer_node_id),
            generation_id=generation_id,
            desired=desired,
            operation_id=operation_id,
            coordinator_node_id=coordinator_node_id,
            predecessor_digest=predecessor_digest,
        )
        preserve_disabled_ack = bool(
            existing is not None
            and desired is StandbyAutoHealing.DISABLED
            and existing.peer_ack_digest == existing.decision_digest
        )
        record = AutoHealingPolicyRecord(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
            desired=desired,
            operation_id=operation_id,
            coordinator_node_id=coordinator_node_id,
            predecessor_digest=predecessor_digest,
            phase=AutoHealingPolicyPhase.COMMITTED,
            decision_digest=decision_digest,
            peer_ack_digest=decision_digest if preserve_disabled_ack else None,
            updated_at=updated_at,
        )
        atomic_write_json(self.path, record.to_dict())
        return record

    def adopt_replacement_peer(
        self,
        *,
        cluster_id: str,
        node_id: str,
        peer_node_id: str,
        generation_id: str,
        peer: AutoHealingPolicyRecord,
        updated_at: float,
    ) -> AutoHealingPolicyRecord:
        """Adopt one retained peer decision from an exact fresh replacement state."""

        current = self.require_bound(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
        )
        peer_bound = bool(
            peer.cluster_id == cluster_id
            and peer.node_id == peer_node_id
            and peer.peer_node_id == node_id
            and peer.generation_id == generation_id
            and peer.desired is StandbyAutoHealing.ENABLED
            and peer.phase is AutoHealingPolicyPhase.COMMITTED
            and (
                peer.peer_ack_digest == peer.decision_digest
                or (
                    peer.peer_ack_digest is None
                    and peer.operation_id
                    == self._operation_id(
                        cluster_id=cluster_id,
                        generation_id=generation_id,
                        desired=StandbyAutoHealing.ENABLED,
                    )
                    and peer.predecessor_digest == _BLOCKED_DIGEST
                )
            )
        )
        if not peer_bound:
            raise AutoHealingPolicyError("replacement peer policy is not terminal and exact")
        already_adopted = bool(
            current.desired is peer.desired
            and current.operation_id == peer.operation_id
            and current.coordinator_node_id == peer.coordinator_node_id
            and current.predecessor_digest == peer.predecessor_digest
            and current.phase is AutoHealingPolicyPhase.COMMITTED
            and current.decision_digest == peer.decision_digest
            and current.peer_ack_digest == current.decision_digest
        )
        if already_adopted:
            return current
        default_operation_id = self._operation_id(
            cluster_id=cluster_id,
            generation_id=generation_id,
            desired=StandbyAutoHealing.ENABLED,
        )
        if not (
            current.desired is StandbyAutoHealing.ENABLED
            and current.operation_id == default_operation_id
            and current.predecessor_digest == _BLOCKED_DIGEST
            and current.phase is AutoHealingPolicyPhase.COMMITTED
            and current.peer_ack_digest is None
        ):
            raise AutoHealingPolicyError(
                "only an exact default-initialized replacement policy may be adopted"
            )
        adopted = AutoHealingPolicyRecord(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
            desired=peer.desired,
            operation_id=peer.operation_id,
            coordinator_node_id=peer.coordinator_node_id,
            predecessor_digest=peer.predecessor_digest,
            phase=AutoHealingPolicyPhase.COMMITTED,
            decision_digest=peer.decision_digest,
            peer_ack_digest=peer.decision_digest,
            updated_at=updated_at,
        )
        atomic_write_json(self.path, adopted.to_dict())
        return adopted

    def prepare(
        self,
        *,
        cluster_id: str,
        node_id: str,
        peer_node_id: str,
        generation_id: str,
        desired: StandbyAutoHealing,
        operation_id: str,
        coordinator_node_id: str,
        predecessor_digest: str,
        peer: AutoHealingPolicyRecord,
        updated_at: float,
    ) -> AutoHealingPolicyRecord:
        current = self.require_bound(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
        )
        decision_digest = policy_decision_digest(
            cluster_id=cluster_id,
            member_node_ids=(node_id, peer_node_id),
            generation_id=generation_id,
            desired=desired,
            operation_id=operation_id,
            coordinator_node_id=coordinator_node_id,
            predecessor_digest=predecessor_digest,
        )
        if (
            current.operation_id == operation_id
            and current.desired is desired
            and current.coordinator_node_id == coordinator_node_id
            and current.predecessor_digest == predecessor_digest
            and current.decision_digest == decision_digest
            and current.phase in {AutoHealingPolicyPhase.PREPARED, AutoHealingPolicyPhase.COMMITTED}
        ):
            return current
        if current.phase is not AutoHealingPolicyPhase.COMMITTED:
            raise AutoHealingPolicyError("another policy transaction is prepared")
        if current.decision_digest != predecessor_digest:
            raise AutoHealingPolicyError("policy predecessor changed")
        if not (
            peer.cluster_id == cluster_id
            and peer.node_id == peer_node_id
            and peer.peer_node_id == node_id
            and peer.generation_id == generation_id
        ):
            raise AutoHealingPolicyError("peer policy predecessor is foreign")
        if node_id == coordinator_node_id:
            if not (
                peer.phase is AutoHealingPolicyPhase.COMMITTED
                and peer.decision_digest == predecessor_digest
                and peer.operation_id == current.operation_id
                and peer.desired is current.desired
                and peer.coordinator_node_id == current.coordinator_node_id
                and peer.predecessor_digest == current.predecessor_digest
            ):
                raise AutoHealingPolicyError("peer policy predecessor does not match")
        elif not (
            peer.node_id == coordinator_node_id
            and peer.phase is AutoHealingPolicyPhase.PREPARED
            and peer.operation_id == operation_id
            and peer.desired is desired
            and peer.coordinator_node_id == coordinator_node_id
            and peer.predecessor_digest == predecessor_digest
            and peer.decision_digest == decision_digest
        ):
            raise AutoHealingPolicyError("policy coordinator is not prepared")
        record = AutoHealingPolicyRecord(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
            desired=desired,
            operation_id=operation_id,
            coordinator_node_id=coordinator_node_id,
            predecessor_digest=predecessor_digest,
            phase=AutoHealingPolicyPhase.PREPARED,
            decision_digest=decision_digest,
            peer_ack_digest=None,
            updated_at=updated_at,
        )
        atomic_write_json(self.path, record.to_dict())
        return record

    def commit(
        self,
        *,
        cluster_id: str,
        node_id: str,
        peer_node_id: str,
        generation_id: str,
        desired: StandbyAutoHealing,
        operation_id: str,
        coordinator_node_id: str,
        predecessor_digest: str,
        peer: AutoHealingPolicyRecord,
        updated_at: float,
    ) -> AutoHealingPolicyRecord:
        current = self.require_bound(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
        )
        if (
            current.phase is AutoHealingPolicyPhase.COMMITTED
            and current.desired is desired
            and current.operation_id == operation_id
            and current.coordinator_node_id == coordinator_node_id
            and current.predecessor_digest == predecessor_digest
        ):
            return current
        expected_peer_phase = (
            AutoHealingPolicyPhase.COMMITTED
            if node_id == coordinator_node_id
            else AutoHealingPolicyPhase.PREPARED
        )
        if not (
            current.desired is desired
            and current.operation_id == operation_id
            and current.coordinator_node_id == coordinator_node_id
            and current.predecessor_digest == predecessor_digest
            and current.phase is AutoHealingPolicyPhase.PREPARED
            and peer.cluster_id == cluster_id
            and peer.node_id == peer_node_id
            and peer.peer_node_id == node_id
            and peer.generation_id == generation_id
            and peer.desired is desired
            and peer.operation_id == operation_id
            and peer.coordinator_node_id == coordinator_node_id
            and peer.predecessor_digest == predecessor_digest
            and peer.decision_digest == current.decision_digest
            and peer.phase is expected_peer_phase
        ):
            raise AutoHealingPolicyError("peer policy acknowledgement does not match")
        record = AutoHealingPolicyRecord(
            **{
                **current.__dict__,
                "phase": AutoHealingPolicyPhase.COMMITTED,
                "peer_ack_digest": current.decision_digest,
                "updated_at": updated_at,
            }
        )
        atomic_write_json(self.path, record.to_dict())
        return record


@dataclass(frozen=True)
class AutoHealingRecoveryRecord:
    cluster_id: str
    node_id: str
    target_node_id: str
    generation_id: str
    desired: StandbyAutoHealing
    operation_id: str
    approval_digest: str
    policy_digest: str
    predecessor_digest: str
    promotion_receipt_id: str
    allocation_id: str
    ownership_epoch: str
    stopped_revision: str
    phase: AutoHealingRecoveryPhase
    rearm_operation_id: str | None
    updated_at: float

    def __post_init__(self) -> None:
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("node_id", self.node_id)
        _require_identifier("target_node_id", self.target_node_id)
        if self.node_id == self.target_node_id:
            raise AutoHealingPolicyError("recovery target must be the peer")
        _require_sha256("generation_id", self.generation_id)
        _require_sha256("operation_id", self.operation_id)
        _require_sha256("approval_digest", self.approval_digest)
        _require_sha256("policy_digest", self.policy_digest)
        _require_sha256("predecessor_digest", self.predecessor_digest)
        _require_identifier("promotion_receipt_id", self.promotion_receipt_id)
        _require_identifier("allocation_id", self.allocation_id)
        _require_identifier("ownership_epoch", self.ownership_epoch)
        _require_identifier("stopped_revision", self.stopped_revision)
        if self.rearm_operation_id is not None:
            _require_identifier("rearm_operation_id", self.rearm_operation_id)
        if self.phase is AutoHealingRecoveryPhase.ARMED and self.rearm_operation_id is not None:
            raise AutoHealingPolicyError("armed recovery cannot bind a rearm operation")
        if self.phase is not AutoHealingRecoveryPhase.ARMED and self.rearm_operation_id is None:
            raise AutoHealingPolicyError("consumed recovery must bind a rearm operation")
        _require_timestamp(self.updated_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "allocation_id": self.allocation_id,
            "approval_digest": self.approval_digest,
            "cluster_id": self.cluster_id,
            "desired": self.desired.value,
            "generation_id": self.generation_id,
            "node_id": self.node_id,
            "operation_id": self.operation_id,
            "ownership_epoch": self.ownership_epoch,
            "phase": self.phase.value,
            "policy_digest": self.policy_digest,
            "predecessor_digest": self.predecessor_digest,
            "promotion_receipt_id": self.promotion_receipt_id,
            "rearm_operation_id": self.rearm_operation_id,
            "schema": AUTO_HEALING_RECOVERY_SCHEMA,
            "stopped_revision": self.stopped_revision,
            "target_node_id": self.target_node_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: object) -> AutoHealingRecoveryRecord:
        if not isinstance(value, Mapping):
            raise AutoHealingPolicyError("recovery record must be an object")
        expected = {
            "allocation_id",
            "approval_digest",
            "cluster_id",
            "desired",
            "generation_id",
            "node_id",
            "operation_id",
            "ownership_epoch",
            "phase",
            "policy_digest",
            "predecessor_digest",
            "promotion_receipt_id",
            "rearm_operation_id",
            "schema",
            "stopped_revision",
            "target_node_id",
            "updated_at",
        }
        if set(value) != expected or value.get("schema") != AUTO_HEALING_RECOVERY_SCHEMA:
            raise AutoHealingPolicyError("recovery record has an invalid shape")
        try:
            desired = StandbyAutoHealing(str(value["desired"]))
            phase = AutoHealingRecoveryPhase(str(value["phase"]))
        except ValueError as error:
            raise AutoHealingPolicyError("recovery record has an invalid state") from error
        return cls(
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            node_id=_require_identifier("node_id", value["node_id"]),
            target_node_id=_require_identifier("target_node_id", value["target_node_id"]),
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            desired=desired,
            operation_id=_require_sha256("operation_id", value["operation_id"]),
            approval_digest=_require_sha256("approval_digest", value["approval_digest"]),
            policy_digest=_require_sha256("policy_digest", value["policy_digest"]),
            predecessor_digest=_require_sha256("predecessor_digest", value["predecessor_digest"]),
            promotion_receipt_id=_require_identifier(
                "promotion_receipt_id", value["promotion_receipt_id"]
            ),
            allocation_id=_require_identifier("allocation_id", value["allocation_id"]),
            ownership_epoch=_require_identifier("ownership_epoch", value["ownership_epoch"]),
            stopped_revision=_require_identifier("stopped_revision", value["stopped_revision"]),
            phase=phase,
            rearm_operation_id=(
                None
                if value["rearm_operation_id"] is None
                else _require_identifier("rearm_operation_id", value["rearm_operation_id"])
            ),
            updated_at=_require_timestamp(value["updated_at"]),
        )


def auto_healing_recovery_digest(record: AutoHealingRecoveryRecord) -> str:
    """Return the immutable identity used for exact completed-recovery CAS."""

    return hashlib.sha256(canonical_json(record.to_dict()).encode("ascii")).hexdigest()


class AutoHealingRecoveryStore:
    """Atomic single-use authorization for policy-owned standby recovery."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / AUTO_HEALING_RECOVERY_FILENAME

    def load(self) -> AutoHealingRecoveryRecord | None:
        if not self.path.exists():
            return None
        try:
            return AutoHealingRecoveryRecord.from_mapping(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AutoHealingPolicyError("recovery record is unreadable") from error

    def arm(self, record: AutoHealingRecoveryRecord) -> AutoHealingRecoveryRecord:
        if record.phase is not AutoHealingRecoveryPhase.ARMED:
            raise AutoHealingPolicyError("new recovery intent must be armed")
        existing = self.load()
        if existing is not None:
            comparable = {
                **existing.__dict__,
                "phase": record.phase,
                "rearm_operation_id": record.rearm_operation_id,
                "updated_at": record.updated_at,
            }
            if comparable == record.__dict__:
                return existing
            if existing.phase is not AutoHealingRecoveryPhase.COMPLETED:
                raise AutoHealingPolicyError("another recovery intent is active")
        atomic_write_json(self.path, record.to_dict())
        return record

    def consume(
        self,
        *,
        operation_id: str,
        rearm_operation_id: str,
        updated_at: float,
    ) -> AutoHealingRecoveryRecord:
        record = self.load()
        if record is None or record.operation_id != operation_id:
            raise AutoHealingPolicyError("recovery intent is unavailable")
        if record.phase is AutoHealingRecoveryPhase.COMPLETED:
            raise AutoHealingPolicyError("recovery intent is already completed")
        if record.phase is AutoHealingRecoveryPhase.CONSUMED:
            if record.rearm_operation_id != rearm_operation_id:
                raise AutoHealingPolicyError("recovery intent was consumed by another start")
            return record
        consumed = AutoHealingRecoveryRecord(
            **{
                **record.__dict__,
                "phase": AutoHealingRecoveryPhase.CONSUMED,
                "rearm_operation_id": rearm_operation_id,
                "updated_at": updated_at,
            }
        )
        atomic_write_json(self.path, consumed.to_dict())
        return consumed

    def complete(
        self,
        *,
        operation_id: str,
        rearm_operation_id: str,
        updated_at: float,
    ) -> AutoHealingRecoveryRecord:
        record = self.load()
        if record is None or not (
            record.operation_id == operation_id
            and record.rearm_operation_id == rearm_operation_id
            and record.phase
            in {AutoHealingRecoveryPhase.CONSUMED, AutoHealingRecoveryPhase.COMPLETED}
        ):
            raise AutoHealingPolicyError("recovery completion does not match")
        if record.phase is AutoHealingRecoveryPhase.COMPLETED:
            return record
        completed = AutoHealingRecoveryRecord(
            **{
                **record.__dict__,
                "phase": AutoHealingRecoveryPhase.COMPLETED,
                "updated_at": updated_at,
            }
        )
        atomic_write_json(self.path, completed.to_dict())
        return completed

    def clear_completed(self, *, operation_id: str, recovery_digest: str) -> None:
        expected_digest = _require_sha256("recovery_digest", recovery_digest)
        record = self.load()
        if record is None:
            return
        if not (
            record.operation_id == operation_id
            and record.phase is AutoHealingRecoveryPhase.COMPLETED
            and auto_healing_recovery_digest(record) == expected_digest
        ):
            raise AutoHealingPolicyError("recovery state changed before cleanup")
        self.path.unlink()
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def cancel_armed(self, *, operation_id: str, approval_digest: str) -> None:
        """Remove only an unconsumed intent from the exact approved operation."""

        record = self.load()
        if record is None:
            return
        if not (
            record.operation_id == operation_id
            and record.approval_digest == approval_digest
            and record.phase is AutoHealingRecoveryPhase.ARMED
        ):
            raise AutoHealingPolicyError("recovery state is not safely cancellable")
        self.path.unlink()
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def require_auto_healing_writer_quiescent(state_dir: Path) -> None:
    """Fail closed when another policy or recovery writer owns the node."""

    # Imported lazily because restoration consumes the policy model.  The
    # shared rearm lock serializes the writers; this check prevents policy,
    # apply, removal, and mTLS workflows from changing identity while an exact
    # post-promotion restoration remains active.
    from .restoration import require_standby_restoration_writer_quiescent

    require_standby_restoration_writer_quiescent(state_dir)

    policy = AutoHealingPolicyStore(state_dir).load()
    if policy is not None and policy.phase is AutoHealingPolicyPhase.PREPARED:
        raise AutoHealingPolicyError("a standby auto-healing policy transaction is prepared")
    recovery = AutoHealingRecoveryStore(state_dir).load()
    if recovery is not None and recovery.phase in {
        AutoHealingRecoveryPhase.ARMED,
        AutoHealingRecoveryPhase.CONSUMED,
    }:
        raise AutoHealingPolicyError("a standby auto-healing recovery is active")


def encode_policy_request(value: Mapping[str, object]) -> str:
    raw = canonical_json(value).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_policy_request(value: str) -> dict[str, Any]:
    if not value or len(value) > 16384 or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise AutoHealingPolicyError("policy request is invalid")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise AutoHealingPolicyError("policy request is invalid") from error
    if not isinstance(payload, dict):
        raise AutoHealingPolicyError("policy request must be an object")
    return payload


def project_local_policy(
    store: AutoHealingPolicyStore,
    *,
    cluster_id: str,
    node_id: str,
    peer_node_id: str,
    generation_id: str,
) -> tuple[str, str]:
    """Return the strict local heartbeat projection; invalid state is blocked."""

    try:
        record = store.require_bound(
            cluster_id=cluster_id,
            node_id=node_id,
            peer_node_id=peer_node_id,
            generation_id=generation_id,
        )
    except AutoHealingPolicyError:
        return "blocked", _BLOCKED_DIGEST
    if record.phase is AutoHealingPolicyPhase.PREPARED:
        return "transitioning", record.decision_digest
    if (
        record.desired is StandbyAutoHealing.DISABLED
        and record.peer_ack_digest != record.decision_digest
    ):
        return "blocked", record.decision_digest
    return record.desired.value, record.decision_digest


def peer_policy_agrees(
    record: AutoHealingPolicyRecord,
    heartbeat: PeerHeartbeat | None,
    *,
    now: float,
    max_age_seconds: float = 30.0,
) -> bool:
    """Require fresh authenticated peer evidence for one committed decision."""

    if heartbeat is None or record.phase is not AutoHealingPolicyPhase.COMMITTED:
        return False
    try:
        from datetime import datetime

        sent_at = datetime.fromisoformat(heartbeat.sent_at.removesuffix("Z") + "+00:00").timestamp()
    except ValueError:
        return False
    age = now - sent_at
    return bool(
        0 <= age <= max_age_seconds
        and heartbeat.cluster_id == record.cluster_id
        and heartbeat.node_id == record.peer_node_id
        and heartbeat.generation_id == record.generation_id
        and heartbeat.auto_healing_policy_state == record.desired.value
        and heartbeat.auto_healing_policy_digest == record.decision_digest
    )


def load_peer_policy_heartbeat(
    state_dir: Path,
    *,
    peer_node_id: str,
) -> PeerHeartbeat | None:
    try:
        return AtomicGenerationStore(state_dir / "generation-store").load_accepted_peer_heartbeat(
            peer_node_id
        )
    except CorruptStateError as error:
        raise AutoHealingPolicyError("peer policy evidence is invalid") from error
