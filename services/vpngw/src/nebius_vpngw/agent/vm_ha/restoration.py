"""Durable transfer-bound authority for automatic standby restoration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .auto_healing import (
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    StandbyAutoHealing,
    peer_policy_agrees,
)
from .models import PeerHeartbeat, canonical_json
from .promotion_receipt import (
    PROMOTION_RECEIPT_FILENAME,
    PROMOTION_RECEIPT_SCHEMA,
    promotion_receipt_id_v1,
)
from .store import atomic_write_json

RESTORATION_AGREEMENT_SCHEMA = "nebius-vpngw/vm-ha-restoration-agreement-v1"
RESTORATION_AGREEMENT_FILENAME = "standby-restoration-agreement.json"
RESTORATION_AUTHORIZATION_SCHEMA = "nebius-vpngw/vm-ha-standby-restoration-authorization-v1"
RESTORATION_AUTHORIZATION_FILENAME = "standby-restoration-authorization.json"
STANDBY_RESTORATION_CAPABILITY = "vm-ha-standby-restoration-v2"
RESTORATION_MAX_ATTEMPTS = 5
RESTORATION_RETRY_DELAYS_SECONDS = (5.0, 15.0, 30.0, 60.0)
RESTORATION_STANDBY_TIMEOUT_SECONDS = 300.0

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$")
_DIGEST_KEYS = {"configuration", "static_routes", "bgp_policy"}


class StandbyRestorationReason(str, Enum):
    """Closed, identity-free reasons safe to expose through VM-HA status."""

    AUTHORIZATION_INVALID = "standby-restoration-authorization-invalid"
    AUTHORITY_STALE_OR_FOREIGN = "standby-restoration-authority-stale-or-foreign"
    POLICY_UNAVAILABLE = "standby-restoration-policy-unavailable"
    POLICY_CHANGED = "standby-restoration-policy-changed"
    NOT_COMMITTED = "standby-restoration-not-committed"
    START_IDENTITY_CHANGED = "standby-restoration-start-identity-changed"
    BLOCKED = "standby-restoration-blocked"
    AUTOMATIC_RETRY_EXHAUSTED = "automatic-retry-exhausted"
    COMPUTE_START_FAILED = "compute-start-failed"
    COMPUTE_START_PERMANENT_FAILURE = "compute-start-permanent-failure"
    STANDBY_READINESS_TIMEOUT = "standby-readiness-timeout"


_PERSISTED_BLOCKED_REASON_CODES = frozenset(
    {
        StandbyRestorationReason.AUTOMATIC_RETRY_EXHAUSTED.value,
        StandbyRestorationReason.COMPUTE_START_FAILED.value,
        StandbyRestorationReason.COMPUTE_START_PERMANENT_FAILURE.value,
        StandbyRestorationReason.STANDBY_READINESS_TIMEOUT.value,
    }
)


class StandbyRestorationError(RuntimeError):
    """Restoration failure with a closed public reason and private detail."""

    def __init__(
        self,
        detail: str,
        *,
        reason: StandbyRestorationReason = StandbyRestorationReason.AUTHORIZATION_INVALID,
    ) -> None:
        super().__init__(detail)
        self.reason = reason

    @property
    def reason_code(self) -> str:
        return self.reason.value


class RestorationSource(str, Enum):
    PLANNED_FAILOVER = "planned-failover"
    PLANNED_FAILBACK = "planned-failback"
    AUTOMATIC_FAILOVER = "automatic-failover"
    OPERATOR_RESTORATION = "operator-restoration"


class RestorationPhase(str, Enum):
    ARMED = "armed"
    COMMITTED = "committed"
    START_ACCEPTED = "start-accepted"
    RUNNING = "running"
    AWAITING_STANDBY = "awaiting-standby"
    COMPLETED = "completed"
    BLOCKED = "blocked"


_ACTIVE_PHASES = frozenset(
    {
        RestorationPhase.ARMED,
        RestorationPhase.COMMITTED,
        RestorationPhase.START_ACCEPTED,
        RestorationPhase.RUNNING,
        RestorationPhase.AWAITING_STANDBY,
    }
)


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise StandbyRestorationError(f"{name} is invalid")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise StandbyRestorationError(f"{name} is invalid")
    return value


def _require_timestamp(name: str, value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise StandbyRestorationError(f"{name} is invalid")
    return float(value)


def _optional_timestamp(name: str, value: object) -> float | None:
    return None if value is None else _require_timestamp(name, value)


def _require_digests(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _DIGEST_KEYS:
        raise StandbyRestorationError("restoration digests are invalid")
    return {key: _require_sha256(key, value[key]) for key in sorted(_DIGEST_KEYS)}


def _heartbeat_digest(heartbeat: PeerHeartbeat) -> str:
    return hashlib.sha256(canonical_json(heartbeat.to_dict()).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class PolicyAgreementCertificate:
    certificate_id: str
    cluster_id: str
    node_id: str
    peer_node_id: str
    generation_id: str
    digests: Mapping[str, str]
    policy_operation_id: str
    policy_digest: str
    peer_boot_id: str
    peer_sequence: int
    peer_sent_at: str
    peer_mtls_epoch: int
    peer_certificate_fingerprint: str
    heartbeat_digest: str
    captured_at: float

    def __post_init__(self) -> None:
        _require_sha256("certificate_id", self.certificate_id)
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("node_id", self.node_id)
        _require_identifier("peer_node_id", self.peer_node_id)
        if self.node_id == self.peer_node_id:
            raise StandbyRestorationError("agreement members must be distinct")
        _require_sha256("generation_id", self.generation_id)
        _require_digests(self.digests)
        _require_sha256("policy_operation_id", self.policy_operation_id)
        _require_sha256("policy_digest", self.policy_digest)
        _require_identifier("peer_boot_id", self.peer_boot_id)
        if (
            not isinstance(self.peer_sequence, int)
            or isinstance(self.peer_sequence, bool)
            or self.peer_sequence < 0
        ):
            raise StandbyRestorationError("peer_sequence is invalid")
        if not isinstance(self.peer_sent_at, str) or not self.peer_sent_at.endswith("Z"):
            raise StandbyRestorationError("peer_sent_at is invalid")
        if (
            not isinstance(self.peer_mtls_epoch, int)
            or isinstance(self.peer_mtls_epoch, bool)
            or self.peer_mtls_epoch < 1
        ):
            raise StandbyRestorationError("peer_mtls_epoch is invalid")
        _require_sha256("peer_certificate_fingerprint", self.peer_certificate_fingerprint)
        _require_sha256("heartbeat_digest", self.heartbeat_digest)
        _require_timestamp("captured_at", self.captured_at)
        if self.certificate_id != self.identity_digest():
            raise StandbyRestorationError("agreement certificate digest is invalid")

    def identity_payload(self) -> dict[str, object]:
        return {
            "cluster_id": self.cluster_id,
            "digests": dict(self.digests),
            "generation_id": self.generation_id,
            "heartbeat_digest": self.heartbeat_digest,
            "node_id": self.node_id,
            "peer_boot_id": self.peer_boot_id,
            "peer_certificate_fingerprint": self.peer_certificate_fingerprint,
            "peer_mtls_epoch": self.peer_mtls_epoch,
            "peer_node_id": self.peer_node_id,
            "peer_sent_at": self.peer_sent_at,
            "peer_sequence": self.peer_sequence,
            "policy_digest": self.policy_digest,
            "policy_operation_id": self.policy_operation_id,
            "schema": RESTORATION_AGREEMENT_SCHEMA,
        }

    def identity_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_payload()).encode("ascii")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity_payload(),
            "captured_at": self.captured_at,
            "certificate_id": self.certificate_id,
        }

    @classmethod
    def create(
        cls,
        *,
        policy: AutoHealingPolicyRecord,
        heartbeat: PeerHeartbeat,
        captured_at: float,
    ) -> PolicyAgreementCertificate:
        if not (
            policy.phase is AutoHealingPolicyPhase.COMMITTED
            and policy.desired is StandbyAutoHealing.ENABLED
            and peer_policy_agrees(policy, heartbeat, now=captured_at)
        ):
            raise StandbyRestorationError("fresh enabled peer policy agreement is unavailable")
        if heartbeat.digests.configuration != policy.generation_id:
            raise StandbyRestorationError("peer agreement generation digest is invalid")
        identity = {
            "cluster_id": policy.cluster_id,
            "digests": heartbeat.digests.to_dict(),
            "generation_id": policy.generation_id,
            "heartbeat_digest": _heartbeat_digest(heartbeat),
            "node_id": policy.node_id,
            "peer_boot_id": heartbeat.boot_id,
            "peer_certificate_fingerprint": heartbeat.certificate_fingerprint,
            "peer_mtls_epoch": heartbeat.mtls_epoch,
            "peer_node_id": policy.peer_node_id,
            "peer_sent_at": heartbeat.sent_at,
            "peer_sequence": heartbeat.sequence,
            "policy_digest": policy.decision_digest,
            "policy_operation_id": policy.operation_id,
            "schema": RESTORATION_AGREEMENT_SCHEMA,
        }
        return cls(
            certificate_id=hashlib.sha256(canonical_json(identity).encode("ascii")).hexdigest(),
            cluster_id=policy.cluster_id,
            node_id=policy.node_id,
            peer_node_id=policy.peer_node_id,
            generation_id=policy.generation_id,
            digests=heartbeat.digests.to_dict(),
            policy_operation_id=policy.operation_id,
            policy_digest=policy.decision_digest,
            peer_boot_id=heartbeat.boot_id,
            peer_sequence=heartbeat.sequence,
            peer_sent_at=heartbeat.sent_at,
            peer_mtls_epoch=heartbeat.mtls_epoch,
            peer_certificate_fingerprint=heartbeat.certificate_fingerprint,
            heartbeat_digest=str(identity["heartbeat_digest"]),
            captured_at=captured_at,
        )

    @classmethod
    def from_mapping(cls, value: object) -> PolicyAgreementCertificate:
        if not isinstance(value, Mapping):
            raise StandbyRestorationError("agreement certificate must be an object")
        expected = {
            "captured_at",
            "certificate_id",
            "cluster_id",
            "digests",
            "generation_id",
            "heartbeat_digest",
            "node_id",
            "peer_boot_id",
            "peer_certificate_fingerprint",
            "peer_mtls_epoch",
            "peer_node_id",
            "peer_sent_at",
            "peer_sequence",
            "policy_digest",
            "policy_operation_id",
            "schema",
        }
        if set(value) != expected or value.get("schema") != RESTORATION_AGREEMENT_SCHEMA:
            raise StandbyRestorationError("agreement certificate has an invalid shape")
        if (
            not isinstance(value["peer_sequence"], int)
            or isinstance(value["peer_sequence"], bool)
            or not isinstance(value["peer_mtls_epoch"], int)
            or isinstance(value["peer_mtls_epoch"], bool)
        ):
            raise StandbyRestorationError("agreement certificate counters are invalid")
        return cls(
            certificate_id=_require_sha256("certificate_id", value["certificate_id"]),
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            node_id=_require_identifier("node_id", value["node_id"]),
            peer_node_id=_require_identifier("peer_node_id", value["peer_node_id"]),
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            digests=_require_digests(value["digests"]),
            policy_operation_id=_require_sha256(
                "policy_operation_id", value["policy_operation_id"]
            ),
            policy_digest=_require_sha256("policy_digest", value["policy_digest"]),
            peer_boot_id=_require_identifier("peer_boot_id", value["peer_boot_id"]),
            peer_sequence=value["peer_sequence"],
            peer_sent_at=str(value["peer_sent_at"]),
            peer_mtls_epoch=value["peer_mtls_epoch"],
            peer_certificate_fingerprint=_require_sha256(
                "peer_certificate_fingerprint", value["peer_certificate_fingerprint"]
            ),
            heartbeat_digest=_require_sha256("heartbeat_digest", value["heartbeat_digest"]),
            captured_at=_require_timestamp("captured_at", value["captured_at"]),
        )


class PolicyAgreementStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / RESTORATION_AGREEMENT_FILENAME

    def load(self) -> PolicyAgreementCertificate | None:
        if not self.path.exists():
            return None
        try:
            return PolicyAgreementCertificate.from_mapping(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StandbyRestorationError("agreement certificate is unreadable") from error

    def capture(
        self,
        *,
        policy: AutoHealingPolicyRecord,
        heartbeat: PeerHeartbeat,
        captured_at: float,
    ) -> PolicyAgreementCertificate:
        certificate = PolicyAgreementCertificate.create(
            policy=policy,
            heartbeat=heartbeat,
            captured_at=captured_at,
        )
        current = self.load()
        if current is not None and current.certificate_id == certificate.certificate_id:
            return current
        atomic_write_json(self.path, certificate.to_dict())
        return certificate


def policy_authorizes_certificate(
    policy: AutoHealingPolicyRecord,
    certificate: PolicyAgreementCertificate,
    *,
    allow_prepared_disable: bool,
) -> bool:
    if not (
        policy.cluster_id == certificate.cluster_id
        and policy.node_id == certificate.node_id
        and policy.peer_node_id == certificate.peer_node_id
        and policy.generation_id == certificate.generation_id
    ):
        return False
    committed_enabled = bool(
        policy.phase is AutoHealingPolicyPhase.COMMITTED
        and policy.desired is StandbyAutoHealing.ENABLED
        and policy.decision_digest == certificate.policy_digest
        and policy.operation_id == certificate.policy_operation_id
    )
    prepared_disable = bool(
        allow_prepared_disable
        and policy.phase is AutoHealingPolicyPhase.PREPARED
        and policy.desired is StandbyAutoHealing.DISABLED
        and policy.predecessor_digest == certificate.policy_digest
    )
    return committed_enabled or prepared_disable


@dataclass(frozen=True)
class StandbyRestorationAuthorization:
    authorization_id: str
    source: RestorationSource
    cluster_id: str
    owner_node_id: str
    former_owner_node_id: str
    allocation_id: str
    generation_id: str
    digests: Mapping[str, str]
    policy_operation_id: str
    policy_digest: str
    certificate_id: str
    request_fingerprint: str | None
    first_operation_id: str | None
    promotion_receipt_id: str | None
    ownership_epoch: str | None
    route_operation_id: str | None
    stopped_revision: str | None
    rearm_operation_id: str | None
    phase: RestorationPhase
    attempt_count: int
    next_attempt_at: float | None
    standby_deadline_at: float | None
    blocked_reason: str | None
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        _require_sha256("authorization_id", self.authorization_id)
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("owner_node_id", self.owner_node_id)
        _require_identifier("former_owner_node_id", self.former_owner_node_id)
        if self.owner_node_id == self.former_owner_node_id:
            raise StandbyRestorationError("restoration members must be distinct")
        _require_identifier("allocation_id", self.allocation_id)
        _require_sha256("generation_id", self.generation_id)
        _require_digests(self.digests)
        _require_sha256("policy_operation_id", self.policy_operation_id)
        _require_sha256("policy_digest", self.policy_digest)
        _require_sha256("certificate_id", self.certificate_id)
        if self.request_fingerprint is not None:
            _require_sha256("request_fingerprint", self.request_fingerprint)
        for name, value in (
            ("first_operation_id", self.first_operation_id),
            ("promotion_receipt_id", self.promotion_receipt_id),
            ("ownership_epoch", self.ownership_epoch),
            ("route_operation_id", self.route_operation_id),
            ("stopped_revision", self.stopped_revision),
            ("rearm_operation_id", self.rearm_operation_id),
        ):
            if value is not None:
                _require_identifier(name, value)
        if (
            not isinstance(self.attempt_count, int)
            or isinstance(self.attempt_count, bool)
            or not 0 <= self.attempt_count <= RESTORATION_MAX_ATTEMPTS
        ):
            raise StandbyRestorationError("attempt_count is invalid")
        _optional_timestamp("next_attempt_at", self.next_attempt_at)
        _optional_timestamp("standby_deadline_at", self.standby_deadline_at)
        if self.blocked_reason is not None:
            _require_identifier("blocked_reason", self.blocked_reason)
        _require_timestamp("created_at", self.created_at)
        _require_timestamp("updated_at", self.updated_at)
        committed_fields = (
            self.promotion_receipt_id,
            self.ownership_epoch,
            self.route_operation_id,
        )
        if self.phase is RestorationPhase.ARMED:
            if any(value is not None for value in committed_fields):
                raise StandbyRestorationError("armed restoration cannot bind promotion")
        elif self.first_operation_id is None or any(value is None for value in committed_fields):
            raise StandbyRestorationError("committed restoration is incomplete")
        if self.phase in {
            RestorationPhase.START_ACCEPTED,
            RestorationPhase.RUNNING,
            RestorationPhase.AWAITING_STANDBY,
            RestorationPhase.COMPLETED,
        } and any(value is None for value in (self.stopped_revision, self.rearm_operation_id)):
            raise StandbyRestorationError("accepted restoration start is incomplete")
        if (
            self.phase
            in {
                RestorationPhase.AWAITING_STANDBY,
                RestorationPhase.COMPLETED,
            }
            and self.standby_deadline_at is None
        ):
            raise StandbyRestorationError("standby readiness deadline is missing")
        if self.phase is RestorationPhase.BLOCKED:
            if self.blocked_reason is None:
                raise StandbyRestorationError("blocked restoration reason is missing")
        elif self.blocked_reason is not None:
            raise StandbyRestorationError("non-blocked restoration has a blocked reason")
        if self.authorization_id != self.identity_digest():
            raise StandbyRestorationError("restoration authorization digest is invalid")

    def identity_payload(self) -> dict[str, object]:
        return {
            "allocation_id": self.allocation_id,
            "certificate_id": self.certificate_id,
            "cluster_id": self.cluster_id,
            "digests": dict(self.digests),
            "former_owner_node_id": self.former_owner_node_id,
            "generation_id": self.generation_id,
            "owner_node_id": self.owner_node_id,
            "policy_digest": self.policy_digest,
            "policy_operation_id": self.policy_operation_id,
            "request_fingerprint": self.request_fingerprint,
            "schema": RESTORATION_AUTHORIZATION_SCHEMA,
            "source": self.source.value,
        }

    def identity_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_payload()).encode("ascii")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity_payload(),
            "attempt_count": self.attempt_count,
            "authorization_id": self.authorization_id,
            "blocked_reason": self.blocked_reason,
            "created_at": self.created_at,
            "first_operation_id": self.first_operation_id,
            "next_attempt_at": self.next_attempt_at,
            "ownership_epoch": self.ownership_epoch,
            "phase": self.phase.value,
            "promotion_receipt_id": self.promotion_receipt_id,
            "rearm_operation_id": self.rearm_operation_id,
            "route_operation_id": self.route_operation_id,
            "standby_deadline_at": self.standby_deadline_at,
            "stopped_revision": self.stopped_revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: object) -> StandbyRestorationAuthorization:
        if not isinstance(value, Mapping):
            raise StandbyRestorationError("restoration authorization must be an object")
        expected = {
            "allocation_id",
            "attempt_count",
            "authorization_id",
            "blocked_reason",
            "certificate_id",
            "cluster_id",
            "created_at",
            "digests",
            "first_operation_id",
            "former_owner_node_id",
            "generation_id",
            "next_attempt_at",
            "owner_node_id",
            "ownership_epoch",
            "phase",
            "policy_digest",
            "policy_operation_id",
            "promotion_receipt_id",
            "rearm_operation_id",
            "request_fingerprint",
            "route_operation_id",
            "schema",
            "source",
            "standby_deadline_at",
            "stopped_revision",
            "updated_at",
        }
        if set(value) != expected or value.get("schema") != RESTORATION_AUTHORIZATION_SCHEMA:
            raise StandbyRestorationError("restoration authorization has an invalid shape")
        try:
            source = RestorationSource(str(value["source"]))
            phase = RestorationPhase(str(value["phase"]))
        except ValueError as error:
            raise StandbyRestorationError(
                "restoration authorization has an invalid state"
            ) from error

        def optional_identifier(name: str) -> str | None:
            return None if value[name] is None else _require_identifier(name, value[name])

        request_fingerprint = (
            None
            if value["request_fingerprint"] is None
            else _require_sha256("request_fingerprint", value["request_fingerprint"])
        )
        if not isinstance(value["attempt_count"], int) or isinstance(value["attempt_count"], bool):
            raise StandbyRestorationError("attempt_count is invalid")
        return cls(
            authorization_id=_require_sha256("authorization_id", value["authorization_id"]),
            source=source,
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            owner_node_id=_require_identifier("owner_node_id", value["owner_node_id"]),
            former_owner_node_id=_require_identifier(
                "former_owner_node_id", value["former_owner_node_id"]
            ),
            allocation_id=_require_identifier("allocation_id", value["allocation_id"]),
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            digests=_require_digests(value["digests"]),
            policy_operation_id=_require_sha256(
                "policy_operation_id", value["policy_operation_id"]
            ),
            policy_digest=_require_sha256("policy_digest", value["policy_digest"]),
            certificate_id=_require_sha256("certificate_id", value["certificate_id"]),
            request_fingerprint=request_fingerprint,
            first_operation_id=optional_identifier("first_operation_id"),
            promotion_receipt_id=optional_identifier("promotion_receipt_id"),
            ownership_epoch=optional_identifier("ownership_epoch"),
            route_operation_id=optional_identifier("route_operation_id"),
            stopped_revision=optional_identifier("stopped_revision"),
            rearm_operation_id=optional_identifier("rearm_operation_id"),
            phase=phase,
            attempt_count=value["attempt_count"],
            next_attempt_at=_optional_timestamp("next_attempt_at", value["next_attempt_at"]),
            standby_deadline_at=_optional_timestamp(
                "standby_deadline_at", value["standby_deadline_at"]
            ),
            blocked_reason=(
                None
                if value["blocked_reason"] is None
                else _require_identifier("blocked_reason", value["blocked_reason"])
            ),
            created_at=_require_timestamp("created_at", value["created_at"]),
            updated_at=_require_timestamp("updated_at", value["updated_at"]),
        )


def policy_authorizes_restoration(
    policy: AutoHealingPolicyRecord,
    authorization: StandbyRestorationAuthorization,
) -> bool:
    """Match durable restoration authority to the current policy transaction.

    The mutable latest agreement certificate is admission evidence used before
    arming.  Once armed, the authorization preserves the exact policy decision
    that admitted the transfer.  Planned transfers require that enabled
    decision to remain current.  Only automatic failover may finish a start
    under the exact prepared-disable successor of its admitted decision.
    """

    if not (
        policy.cluster_id == authorization.cluster_id
        and {policy.node_id, policy.peer_node_id}
        == {authorization.owner_node_id, authorization.former_owner_node_id}
        and policy.generation_id == authorization.generation_id
    ):
        return False
    committed_enabled = bool(
        policy.phase is AutoHealingPolicyPhase.COMMITTED
        and policy.desired is StandbyAutoHealing.ENABLED
        and policy.operation_id == authorization.policy_operation_id
        and policy.decision_digest == authorization.policy_digest
    )
    automatic_prepared_disable = bool(
        authorization.source is RestorationSource.AUTOMATIC_FAILOVER
        and authorization.phase
        in {
            RestorationPhase.COMMITTED,
            RestorationPhase.START_ACCEPTED,
            RestorationPhase.RUNNING,
            RestorationPhase.AWAITING_STANDBY,
            RestorationPhase.BLOCKED,
        }
        and policy.phase is AutoHealingPolicyPhase.PREPARED
        and policy.desired is StandbyAutoHealing.DISABLED
        and policy.predecessor_digest == authorization.policy_digest
    )
    return committed_enabled or automatic_prepared_disable


def blocked_restoration_reason_code(reason: str | None) -> str:
    """Project only persisted closed block reasons into public status."""

    if reason in _PERSISTED_BLOCKED_REASON_CODES:
        return str(reason)
    return StandbyRestorationReason.BLOCKED.value


class StandbyRestorationStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / RESTORATION_AUTHORIZATION_FILENAME
        self.receipt_path = state_dir / PROMOTION_RECEIPT_FILENAME

    def load(self) -> StandbyRestorationAuthorization | None:
        record = self._load_authorization()
        if record is not None and record.phase is not RestorationPhase.ARMED:
            self._prove_receipt_binding(
                record,
                self._load_durable_receipt(),
                compare_source=record.source is not RestorationSource.OPERATOR_RESTORATION,
            )
        return record

    def _load_authorization(self) -> StandbyRestorationAuthorization | None:
        if not self.path.exists():
            return None
        try:
            return StandbyRestorationAuthorization.from_mapping(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StandbyRestorationError("restoration authorization is unreadable") from error

    def _load_durable_receipt(self) -> Mapping[str, object]:
        try:
            payload = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise StandbyRestorationError("promotion receipt is unavailable") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StandbyRestorationError("promotion receipt is unreadable") from error
        if not isinstance(payload, Mapping):
            raise StandbyRestorationError("promotion receipt is malformed")
        return payload

    @staticmethod
    def _validate_receipt_shape(receipt: Mapping[str, object]) -> None:
        expected = {
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
        if set(receipt) != expected or receipt.get("schema") != PROMOTION_RECEIPT_SCHEMA:
            raise StandbyRestorationError("promotion receipt is malformed")
        for name in (
            "allocation_id",
            "cluster_id",
            "former_owner_node_id",
            "intent",
            "owner_node_id",
            "ownership_epoch",
            "route_operation_id",
        ):
            _require_identifier(name, receipt[name])
        _require_sha256("generation_id", receipt["generation_id"])
        _require_sha256("receipt_id", receipt["receipt_id"])
        _require_digests(receipt["digests"])
        _require_timestamp("committed_at", receipt["committed_at"])
        _require_timestamp("common_cutover_seconds", receipt["common_cutover_seconds"])

    @classmethod
    def _prove_receipt_binding(
        cls,
        record: StandbyRestorationAuthorization,
        receipt: Mapping[str, object],
        *,
        compare_source: bool,
    ) -> None:
        cls._validate_receipt_shape(receipt)
        intent = str(receipt["intent"])
        if intent == "apply-owner-adoption" or (compare_source and intent != record.source.value):
            raise StandbyRestorationError("promotion receipt does not match restoration authority")
        if record.first_operation_id is None:
            raise StandbyRestorationError("restoration first effect is unavailable")
        if not (
            receipt["cluster_id"] == record.cluster_id
            and receipt["owner_node_id"] == record.owner_node_id
            and receipt["former_owner_node_id"] == record.former_owner_node_id
            and receipt["allocation_id"] == record.allocation_id
            and receipt["generation_id"] == record.generation_id
            and receipt["digests"] == dict(record.digests)
        ):
            raise StandbyRestorationError("promotion receipt does not match restoration authority")
        expected_receipt_id = promotion_receipt_id_v1(
            allocation_id=record.allocation_id,
            first_operation_id=record.first_operation_id,
            generation_id=record.generation_id,
            intent=intent,
            owner_node_id=record.owner_node_id,
            ownership_epoch=str(receipt["ownership_epoch"]),
            route_operation_id=str(receipt["route_operation_id"]),
        )
        if receipt["receipt_id"] != expected_receipt_id:
            raise StandbyRestorationError("promotion receipt digest is invalid")
        if record.promotion_receipt_id is not None and not (
            record.promotion_receipt_id == receipt["receipt_id"]
            and record.ownership_epoch == receipt["ownership_epoch"]
            and record.route_operation_id == receipt["route_operation_id"]
        ):
            raise StandbyRestorationError("promotion receipt does not match restoration authority")

    def _write(self, record: StandbyRestorationAuthorization) -> StandbyRestorationAuthorization:
        atomic_write_json(self.path, record.to_dict())
        return record

    def arm(
        self,
        *,
        source: RestorationSource,
        certificate: PolicyAgreementCertificate,
        owner_node_id: str,
        former_owner_node_id: str,
        allocation_id: str,
        generation_id: str,
        digests: Mapping[str, str],
        request_fingerprint: str | None,
        first_operation_id: str | None,
        updated_at: float,
    ) -> StandbyRestorationAuthorization:
        if not (
            certificate.node_id == owner_node_id
            and certificate.peer_node_id == former_owner_node_id
            and certificate.cluster_id
            and certificate.generation_id == generation_id
            and dict(certificate.digests) == _require_digests(digests)
        ):
            raise StandbyRestorationError("agreement certificate does not match transfer")
        if source is RestorationSource.AUTOMATIC_FAILOVER and request_fingerprint is not None:
            raise StandbyRestorationError("automatic restoration cannot bind a manual request")
        if (
            source
            in {
                RestorationSource.PLANNED_FAILOVER,
                RestorationSource.PLANNED_FAILBACK,
            }
            and request_fingerprint is None
        ):
            raise StandbyRestorationError("planned restoration requires a request fingerprint")
        identity = {
            "allocation_id": allocation_id,
            "certificate_id": certificate.certificate_id,
            "cluster_id": certificate.cluster_id,
            "digests": dict(digests),
            "former_owner_node_id": former_owner_node_id,
            "generation_id": generation_id,
            "owner_node_id": owner_node_id,
            "policy_digest": certificate.policy_digest,
            "policy_operation_id": certificate.policy_operation_id,
            "request_fingerprint": request_fingerprint,
            "schema": RESTORATION_AUTHORIZATION_SCHEMA,
            "source": source.value,
        }
        record = StandbyRestorationAuthorization(
            authorization_id=hashlib.sha256(canonical_json(identity).encode("ascii")).hexdigest(),
            source=source,
            cluster_id=certificate.cluster_id,
            owner_node_id=owner_node_id,
            former_owner_node_id=former_owner_node_id,
            allocation_id=allocation_id,
            generation_id=generation_id,
            digests=dict(digests),
            policy_operation_id=certificate.policy_operation_id,
            policy_digest=certificate.policy_digest,
            certificate_id=certificate.certificate_id,
            request_fingerprint=request_fingerprint,
            first_operation_id=first_operation_id,
            promotion_receipt_id=None,
            ownership_epoch=None,
            route_operation_id=None,
            stopped_revision=None,
            rearm_operation_id=None,
            phase=RestorationPhase.ARMED,
            attempt_count=0,
            next_attempt_at=None,
            standby_deadline_at=None,
            blocked_reason=None,
            created_at=updated_at,
            updated_at=updated_at,
        )
        current = self.load()
        if current is not None:
            if current.authorization_id == record.authorization_id:
                if current.phase is RestorationPhase.ARMED and (
                    current.first_operation_id is None
                    or current.first_operation_id == first_operation_id
                    or first_operation_id is None
                ):
                    return current
                if current.phase is not RestorationPhase.ARMED:
                    return current
            if current.phase in _ACTIVE_PHASES or current.phase is RestorationPhase.BLOCKED:
                raise StandbyRestorationError("another standby restoration is active")
        return self._write(record)

    def bind_first_effect(
        self,
        *,
        authorization_id: str,
        operation_id: str,
        updated_at: float,
    ) -> StandbyRestorationAuthorization:
        record = self._require(authorization_id)
        if record.phase is not RestorationPhase.ARMED:
            if record.first_operation_id == operation_id:
                return record
            raise StandbyRestorationError("restoration first effect is already bound")
        if record.first_operation_id is not None:
            if record.first_operation_id != operation_id:
                raise StandbyRestorationError("restoration first effect changed")
            return record
        return self._write(replace(record, first_operation_id=operation_id, updated_at=updated_at))

    def commit(
        self,
        *,
        receipt: Mapping[str, object],
        updated_at: float,
    ) -> StandbyRestorationAuthorization:
        record = self.load()
        if record is None:
            raise StandbyRestorationError("restoration authorization is unavailable")
        if receipt.get("intent") == "apply-owner-adoption":
            return record
        durable_receipt = self._load_durable_receipt()
        if dict(durable_receipt) != dict(receipt):
            raise StandbyRestorationError("promotion receipt changed before restoration commit")
        self._prove_receipt_binding(
            record,
            durable_receipt,
            compare_source=record.phase is RestorationPhase.ARMED,
        )
        if record.phase is not RestorationPhase.ARMED:
            if record.promotion_receipt_id == receipt["receipt_id"]:
                return record
            raise StandbyRestorationError("restoration promotion receipt changed")
        committed = replace(
            record,
            promotion_receipt_id=str(receipt["receipt_id"]),
            ownership_epoch=str(receipt["ownership_epoch"]),
            route_operation_id=str(receipt["route_operation_id"]),
            phase=RestorationPhase.COMMITTED,
            updated_at=updated_at,
        )
        self._prove_receipt_binding(committed, durable_receipt, compare_source=True)
        return self._write(committed)

    def accept_start(
        self,
        *,
        receipt_id: str,
        stopped_revision: str,
        rearm_operation_id: str,
        updated_at: float,
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase is RestorationPhase.START_ACCEPTED:
            if (
                record.stopped_revision == stopped_revision
                and record.rearm_operation_id == rearm_operation_id
            ):
                return record
            raise StandbyRestorationError("restoration start identity changed")
        if record.phase is not RestorationPhase.COMMITTED:
            raise StandbyRestorationError("restoration is not ready to accept a start")
        return self._write(
            replace(
                record,
                stopped_revision=stopped_revision,
                rearm_operation_id=rearm_operation_id,
                phase=RestorationPhase.START_ACCEPTED,
                updated_at=updated_at,
            )
        )

    def record_submission(
        self, *, receipt_id: str, updated_at: float
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase is not RestorationPhase.START_ACCEPTED:
            raise StandbyRestorationError("restoration start is not accepted")
        if record.next_attempt_at is not None and updated_at < record.next_attempt_at:
            raise StandbyRestorationError("restoration retry is not due")
        if record.attempt_count >= RESTORATION_MAX_ATTEMPTS:
            raise StandbyRestorationError("automatic-retry-exhausted")
        return self._write(
            replace(
                record,
                attempt_count=record.attempt_count + 1,
                next_attempt_at=None,
                updated_at=updated_at,
            )
        )

    def schedule_retry(
        self, *, receipt_id: str, reason: str, updated_at: float
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase is not RestorationPhase.START_ACCEPTED or record.attempt_count < 1:
            raise StandbyRestorationError("restoration retry cannot be scheduled")
        if record.attempt_count >= RESTORATION_MAX_ATTEMPTS:
            return self.block(
                receipt_id=receipt_id,
                reason="automatic-retry-exhausted",
                updated_at=updated_at,
            )
        delay = RESTORATION_RETRY_DELAYS_SECONDS[record.attempt_count - 1]
        _require_identifier("retry_reason", reason)
        return self._write(
            replace(record, next_attempt_at=updated_at + delay, updated_at=updated_at)
        )

    def mark_running(
        self, *, receipt_id: str, updated_at: float
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase in {
            RestorationPhase.RUNNING,
            RestorationPhase.AWAITING_STANDBY,
            RestorationPhase.COMPLETED,
        }:
            return record
        if record.phase is not RestorationPhase.START_ACCEPTED:
            raise StandbyRestorationError("restoration start was not accepted")
        return self._write(
            replace(
                record, phase=RestorationPhase.RUNNING, next_attempt_at=None, updated_at=updated_at
            )
        )

    def await_standby(
        self,
        *,
        receipt_id: str,
        updated_at: float,
        timeout_seconds: float = RESTORATION_STANDBY_TIMEOUT_SECONDS,
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase in {RestorationPhase.AWAITING_STANDBY, RestorationPhase.COMPLETED}:
            return record
        if record.phase is not RestorationPhase.RUNNING:
            raise StandbyRestorationError("restoration Compute is not running")
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise StandbyRestorationError("standby timeout is invalid")
        return self._write(
            replace(
                record,
                phase=RestorationPhase.AWAITING_STANDBY,
                standby_deadline_at=updated_at + timeout_seconds,
                updated_at=updated_at,
            )
        )

    def complete(self, *, receipt_id: str, updated_at: float) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        if record.phase is RestorationPhase.COMPLETED:
            return record
        if record.phase is not RestorationPhase.AWAITING_STANDBY:
            raise StandbyRestorationError("restoration is not awaiting standby readiness")
        return self._write(replace(record, phase=RestorationPhase.COMPLETED, updated_at=updated_at))

    def block(
        self, *, receipt_id: str, reason: str, updated_at: float
    ) -> StandbyRestorationAuthorization:
        record = self.require_receipt(receipt_id)
        _require_identifier("blocked_reason", reason)
        if record.phase is RestorationPhase.COMPLETED:
            raise StandbyRestorationError("completed restoration cannot be blocked")
        if record.phase is RestorationPhase.BLOCKED:
            if record.blocked_reason == reason:
                return record
            raise StandbyRestorationError("restoration blocked reason changed")
        return self._write(
            replace(
                record,
                phase=RestorationPhase.BLOCKED,
                blocked_reason=reason,
                next_attempt_at=None,
                updated_at=updated_at,
            )
        )

    def restart_blocked(
        self, *, receipt_id: str, updated_at: float
    ) -> StandbyRestorationAuthorization:
        """Create fresh operator-owned retry authority for the same receipt."""

        record = self.require_receipt(receipt_id)
        if record.phase is not RestorationPhase.BLOCKED:
            return record
        if record.stopped_revision is None or record.rearm_operation_id is None:
            raise StandbyRestorationError("blocked restoration has no stable start identity")
        identity = {
            **record.identity_payload(),
            "source": RestorationSource.OPERATOR_RESTORATION.value,
        }
        restarted = replace(
            record,
            authorization_id=hashlib.sha256(canonical_json(identity).encode("ascii")).hexdigest(),
            source=RestorationSource.OPERATOR_RESTORATION,
            phase=RestorationPhase.START_ACCEPTED,
            attempt_count=0,
            next_attempt_at=None,
            standby_deadline_at=None,
            blocked_reason=None,
            created_at=updated_at,
            updated_at=updated_at,
        )
        return self._write(restarted)

    def require_receipt(self, receipt_id: str) -> StandbyRestorationAuthorization:
        record = self.load()
        if record is None or record.promotion_receipt_id != receipt_id:
            raise StandbyRestorationError("restoration authorization is unavailable")
        return record

    def _require(self, authorization_id: str) -> StandbyRestorationAuthorization:
        record = self.load()
        if record is None or record.authorization_id != authorization_id:
            raise StandbyRestorationError("restoration authorization is unavailable")
        return record

    def retire_terminal(self, *, authorization_id: str) -> None:
        record = self._require(authorization_id)
        if record.phase not in {RestorationPhase.COMPLETED, RestorationPhase.BLOCKED}:
            raise StandbyRestorationError("active restoration cannot be retired")
        self._retire_exact_terminal(record)

    def retire_terminal_for_apply_owner_adoption(self) -> bool:
        """Retire one terminal authorization superseded by exact owner adoption.

        Apply-owner adoption is allowed to replace the promotion receipt only
        after terminal restoration state is retired. Older runtimes could
        persist those writes in the opposite order, which makes ordinary
        receipt-bound loading fail closed. Standby replacement may repair only
        that exact historical terminal state: the authorization must still be
        structurally valid and its immutable authority must match the current
        apply-owner-adoption receipt.
        """

        record = self._load_authorization()
        if record is None or record.phase not in {
            RestorationPhase.COMPLETED,
            RestorationPhase.BLOCKED,
        }:
            return False
        receipt = self._load_durable_receipt()
        self._validate_receipt_shape(receipt)
        if not (
            receipt["intent"] == "apply-owner-adoption"
            and receipt["cluster_id"] == record.cluster_id
            and receipt["owner_node_id"] == record.owner_node_id
            and receipt["former_owner_node_id"] == record.former_owner_node_id
            and receipt["allocation_id"] == record.allocation_id
            and receipt["generation_id"] == record.generation_id
            and receipt["digests"] == dict(record.digests)
        ):
            return False
        self._retire_exact_terminal(record)
        return True

    def _retire_exact_terminal(self, record: StandbyRestorationAuthorization) -> None:
        current = self._load_authorization()
        if current != record:
            raise StandbyRestorationError("restoration authorization changed before retirement")
        if current.phase not in {RestorationPhase.COMPLETED, RestorationPhase.BLOCKED}:
            raise StandbyRestorationError("active restoration cannot be retired")
        self.path.unlink()
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def discard_unusable_authorization(self) -> None:
        """Durably disable restoration without trusting an unreadable record."""

        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def require_standby_restoration_writer_quiescent(state_dir: Path) -> None:
    record = StandbyRestorationStore(state_dir).load()
    if record is not None and record.phase in _ACTIVE_PHASES:
        raise StandbyRestorationError("a standby restoration is active")


def restoration_is_active(record: StandbyRestorationAuthorization | None) -> bool:
    return record is not None and record.phase in _ACTIVE_PHASES
