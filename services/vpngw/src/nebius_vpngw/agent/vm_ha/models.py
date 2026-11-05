"""Canonical models for local VM-HA evidence.

These records deliberately describe observations and local readiness only. They
never establish cloud allocation ownership or authorize promotion.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_ROLES = frozenset({"active", "passive"})


class StateValidationError(ValueError):
    """Raised when persisted or received state violates its canonical contract."""


class StalePeerStateError(StateValidationError):
    """Raised when an authenticated peer message is replayed or out of order."""


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise StateValidationError(f"{name} must be a non-empty stable identifier")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise StateValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise StateValidationError(f"{name} must be a boolean")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise StateValidationError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(actual)}"
        )


def canonical_json(value: Mapping[str, Any]) -> str:
    """Return the only accepted JSON representation for VM-HA records."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class DigestSet:
    """Digests that must match on both nodes before promotion is considered."""

    configuration: str
    static_routes: str
    bgp_policy: str

    def __post_init__(self) -> None:
        _require_sha256("configuration", self.configuration)
        _require_sha256("static_routes", self.static_routes)
        _require_sha256("bgp_policy", self.bgp_policy)

    def to_dict(self) -> dict[str, str]:
        return {
            "bgp_policy": self.bgp_policy,
            "configuration": self.configuration,
            "static_routes": self.static_routes,
        }

    @classmethod
    def from_mapping(cls, value: object) -> DigestSet:
        if not isinstance(value, Mapping):
            raise StateValidationError("digests must be an object")
        _require_exact_keys(value, {"configuration", "static_routes", "bgp_policy"}, "digests")
        return cls(
            configuration=_require_sha256("configuration", value["configuration"]),
            static_routes=_require_sha256("static_routes", value["static_routes"]),
            bgp_policy=_require_sha256("bgp_policy", value["bgp_policy"]),
        )


@dataclass(frozen=True)
class GenerationRevision:
    """Immutable node-local acknowledgement of one canonical generation."""

    cluster_id: str
    node_id: str
    generation_id: str
    digests: DigestSet
    committed_at: str

    def __post_init__(self) -> None:
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("node_id", self.node_id)
        _require_sha256("generation_id", self.generation_id)
        if self.generation_id != self.digests.configuration:
            raise StateValidationError(
                "generation_id must equal the canonical configuration digest"
            )
        if not isinstance(self.committed_at, str) or not self.committed_at.endswith("Z"):
            raise StateValidationError("committed_at must be a UTC timestamp ending in Z")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "committed_at": self.committed_at,
            "digests": self.digests.to_dict(),
            "generation_id": self.generation_id,
            "node_id": self.node_id,
            "schema": "nebius-vpngw/vm-ha-generation-v1",
        }

    @classmethod
    def from_mapping(cls, value: object) -> GenerationRevision:
        if not isinstance(value, Mapping):
            raise StateValidationError("generation revision must be an object")
        _require_exact_keys(
            value,
            {"schema", "cluster_id", "node_id", "generation_id", "digests", "committed_at"},
            "generation revision",
        )
        if value["schema"] != "nebius-vpngw/vm-ha-generation-v1":
            raise StateValidationError("unsupported generation revision schema")
        return cls(
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            node_id=_require_identifier("node_id", value["node_id"]),
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            digests=DigestSet.from_mapping(value["digests"]),
            committed_at=str(value["committed_at"]),
        )


@dataclass(frozen=True)
class TransitionRecord:
    """Recovery/audit evidence for a local transition, never ownership authority."""

    boot_id: str
    sequence: int
    transition: str
    generation_id: str | None
    recorded_at: str
    outcome: str

    def __post_init__(self) -> None:
        _require_identifier("boot_id", self.boot_id)
        _require_identifier("transition", self.transition)
        _require_identifier("outcome", self.outcome)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise StateValidationError("sequence must be a non-negative integer")
        if self.generation_id is not None:
            _require_sha256("generation_id", self.generation_id)
        if not isinstance(self.recorded_at, str) or not self.recorded_at.endswith("Z"):
            raise StateValidationError("recorded_at must be a UTC timestamp ending in Z")

    def to_dict(self) -> dict[str, Any]:
        return {
            "boot_id": self.boot_id,
            "generation_id": self.generation_id,
            "outcome": self.outcome,
            "recorded_at": self.recorded_at,
            "schema": "nebius-vpngw/vm-ha-transition-v1",
            "sequence": self.sequence,
            "transition": self.transition,
        }

    @classmethod
    def from_mapping(cls, value: object) -> TransitionRecord:
        if not isinstance(value, Mapping):
            raise StateValidationError("transition record must be an object")
        _require_exact_keys(
            value,
            {
                "schema",
                "boot_id",
                "sequence",
                "transition",
                "generation_id",
                "recorded_at",
                "outcome",
            },
            "transition record",
        )
        if value["schema"] != "nebius-vpngw/vm-ha-transition-v1":
            raise StateValidationError("unsupported transition record schema")
        generation_id = value["generation_id"]
        if generation_id is not None:
            generation_id = _require_sha256("generation_id", generation_id)
        sequence = value["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise StateValidationError("sequence must be a non-negative integer")
        return cls(
            boot_id=_require_identifier("boot_id", value["boot_id"]),
            sequence=sequence,
            transition=_require_identifier("transition", value["transition"]),
            generation_id=generation_id,
            recorded_at=str(value["recorded_at"]),
            outcome=_require_identifier("outcome", value["outcome"]),
        )


@dataclass(frozen=True)
class HeartbeatEvidence:
    """Wire evidence; receipt proof is deliberately not part of this record."""

    state: str = "unknown"
    serving: bool = False
    process_id: str = "0" * 32
    sample_id: int = 0
    age_seconds: float = 0.0
    duration_seconds: float = 0.0
    cloud_status: str = "unknown"
    cloud_age_seconds: float | None = None
    controller_phase: str = "starting"
    challenge: str = "0" * 32
    echo: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or self.state not in {"healthy", "unhealthy", "unknown"}:
            raise StateValidationError("invalid local health evidence")
        if type(self.serving) is not bool or (self.serving and self.state != "healthy"):
            raise StateValidationError("invalid serving path evidence")
        if not isinstance(self.cloud_status, str) or self.cloud_status not in {
            "complete",
            "in-progress",
            "unavailable",
            "unknown",
        }:
            raise StateValidationError("invalid cloud observation status")
        if self.process_id is None or self.challenge is None:
            raise StateValidationError("missing heartbeat evidence identity")
        for value in (self.process_id, self.challenge, self.echo):
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 32
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise StateValidationError("invalid heartbeat evidence identity")
        if type(self.sample_id) is not int or self.sample_id < 0:
            raise StateValidationError("invalid health sample counter")
        if self.age_seconds is None or self.duration_seconds is None:
            raise StateValidationError("missing evidence age")
        for age in (self.age_seconds, self.duration_seconds, self.cloud_age_seconds):
            if age is not None and (
                type(age) not in {int, float} or not math.isfinite(age) or age < 0
            ):
                raise StateValidationError("invalid evidence age")
        if self.duration_seconds > self.age_seconds:
            raise StateValidationError("sample duration exceeds sample age")
        if not isinstance(self.controller_phase, str) or self.controller_phase not in {
            "starting",
            "observing",
            "idle",
            "effect",
            "closing",
            "failed",
        }:
            raise StateValidationError("invalid controller progress")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_mapping(cls, value: object) -> HeartbeatEvidence:
        if not isinstance(value, Mapping):
            raise StateValidationError("heartbeat evidence must be an object")
        _require_exact_keys(value, set(cls.__dataclass_fields__), "heartbeat evidence")
        return cls(**value)


@dataclass(frozen=True)
class PeerHeartbeat:
    """Authenticated advisory state reported by one peer."""

    cluster_id: str
    node_id: str
    boot_id: str
    sequence: int
    sent_at: str
    configured_role: str
    observed_owner_id: str | None
    generation_id: str
    mtls_epoch: int
    certificate_fingerprint: str
    digests: DigestSet
    service_healthy: bool
    route_ready: bool
    promotion_ready: bool
    auto_healing_policy_state: str
    auto_healing_policy_digest: str
    tunnel_capability: bool = True
    tunnel_recovery: dict[str, Any] | None = None
    evidence: HeartbeatEvidence = field(default_factory=HeartbeatEvidence)
    # Set only by the current receiving process after authenticated round trip.
    # Excluded from all wire/persistence serialization.
    health_valid_until: float | None = field(default=None, compare=False, repr=False)
    service_valid_until: float | None = field(default=None, compare=False, repr=False)
    echo_valid_until: float | None = field(default=None, compare=False, repr=False)

    def local_health_fresh(self, now: float) -> bool:
        return bool(
            self.evidence.state == "healthy"
            and self.health_valid_until is not None
            and math.isfinite(now)
            and now < self.health_valid_until
        )

    def local_service_fresh(self, now: float) -> bool:
        return bool(
            self.service_healthy
            and self.service_valid_until is not None
            and math.isfinite(now)
            and now < self.service_valid_until
        )

    def __post_init__(self) -> None:
        _require_identifier("cluster_id", self.cluster_id)
        _require_identifier("node_id", self.node_id)
        _require_identifier("boot_id", self.boot_id)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise StateValidationError("sequence must be a non-negative integer")
        if not isinstance(self.sent_at, str) or not self.sent_at.endswith("Z"):
            raise StateValidationError("sent_at must be a UTC timestamp ending in Z")
        if self.configured_role not in _ROLES:
            raise StateValidationError("configured_role must be active or passive")
        if self.observed_owner_id is not None:
            _require_identifier("observed_owner_id", self.observed_owner_id)
        _require_sha256("generation_id", self.generation_id)
        if (
            not isinstance(self.mtls_epoch, int)
            or isinstance(self.mtls_epoch, bool)
            or self.mtls_epoch < 1
        ):
            raise StateValidationError("mtls_epoch must be a positive integer")
        _require_sha256("certificate_fingerprint", self.certificate_fingerprint)
        if self.generation_id != self.digests.configuration:
            raise StateValidationError(
                "generation_id must equal the canonical configuration digest"
            )
        _require_bool("service_healthy", self.service_healthy)
        _require_bool("route_ready", self.route_ready)
        _require_bool("promotion_ready", self.promotion_ready)
        if self.auto_healing_policy_state not in {
            "enabled",
            "disabled",
            "transitioning",
            "blocked",
        }:
            raise StateValidationError("auto_healing_policy_state is invalid")
        _require_sha256("auto_healing_policy_digest", self.auto_healing_policy_digest)
        _require_bool("tunnel_capability", self.tunnel_capability)
        if self.tunnel_recovery is not None:
            claim = self.tunnel_recovery
            if not isinstance(claim, dict) or set(claim) != {"owner", "started", "deadline"}:
                raise StateValidationError("invalid tunnel recovery claim")
            owner = claim["owner"]
            if not isinstance(owner, dict) or set(owner) != {
                "cluster",
                "owner",
                "allocation",
                "epoch",
                "incarnation",
            }:
                raise StateValidationError("invalid tunnel recovery owner")
            for key in ("cluster", "owner", "allocation", "epoch"):
                _require_identifier(key, owner[key])
            if type(owner["incarnation"]) is not int or owner["incarnation"] < 0:
                raise StateValidationError("invalid tunnel recovery incarnation")
            if (
                any(
                    type(claim[key]) not in {int, float} or not math.isfinite(claim[key])
                    for key in ("started", "deadline")
                )
                or not 0 < claim["deadline"] - claim["started"] <= 30
            ):
                raise StateValidationError("invalid tunnel recovery deadline")

    def to_dict(self) -> dict[str, Any]:
        return {
            "boot_id": self.boot_id,
            "auto_healing_policy_digest": self.auto_healing_policy_digest,
            "auto_healing_policy_state": self.auto_healing_policy_state,
            "cluster_id": self.cluster_id,
            "configured_role": self.configured_role,
            "digests": self.digests.to_dict(),
            "generation_id": self.generation_id,
            "mtls_epoch": self.mtls_epoch,
            "certificate_fingerprint": self.certificate_fingerprint,
            "node_id": self.node_id,
            "observed_owner_id": self.observed_owner_id,
            "promotion_ready": self.promotion_ready,
            "route_ready": self.route_ready,
            "schema": "nebius-vpngw/vm-ha-heartbeat-v5",
            "evidence": self.evidence.to_dict(),
            "tunnel_capability": self.tunnel_capability,
            "tunnel_recovery": self.tunnel_recovery,
            "sent_at": self.sent_at,
            "sequence": self.sequence,
            "service_healthy": self.service_healthy,
        }

    @classmethod
    def from_mapping(cls, value: object) -> PeerHeartbeat:
        if not isinstance(value, Mapping):
            raise StateValidationError("peer heartbeat must be an object")
        _require_exact_keys(
            value,
            {
                "schema",
                "cluster_id",
                "node_id",
                "boot_id",
                "auto_healing_policy_digest",
                "auto_healing_policy_state",
                "sequence",
                "sent_at",
                "configured_role",
                "observed_owner_id",
                "generation_id",
                "mtls_epoch",
                "certificate_fingerprint",
                "digests",
                "service_healthy",
                "route_ready",
                "promotion_ready",
                "tunnel_capability",
                "tunnel_recovery",
                "evidence",
            },
            "peer heartbeat",
        )
        if value["schema"] != "nebius-vpngw/vm-ha-heartbeat-v5":
            raise StateValidationError("unsupported peer heartbeat schema")
        sequence = value["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise StateValidationError("sequence must be a non-negative integer")
        observed_owner_id = value["observed_owner_id"]
        if observed_owner_id is not None:
            observed_owner_id = _require_identifier("observed_owner_id", observed_owner_id)
        return cls(
            cluster_id=_require_identifier("cluster_id", value["cluster_id"]),
            node_id=_require_identifier("node_id", value["node_id"]),
            boot_id=_require_identifier("boot_id", value["boot_id"]),
            sequence=sequence,
            sent_at=str(value["sent_at"]),
            configured_role=str(value["configured_role"]),
            observed_owner_id=observed_owner_id,
            generation_id=_require_sha256("generation_id", value["generation_id"]),
            mtls_epoch=value["mtls_epoch"],
            certificate_fingerprint=_require_sha256(
                "certificate_fingerprint", value["certificate_fingerprint"]
            ),
            digests=DigestSet.from_mapping(value["digests"]),
            service_healthy=_require_bool("service_healthy", value["service_healthy"]),
            route_ready=_require_bool("route_ready", value["route_ready"]),
            promotion_ready=_require_bool("promotion_ready", value["promotion_ready"]),
            auto_healing_policy_state=str(value["auto_healing_policy_state"]),
            auto_healing_policy_digest=_require_sha256(
                "auto_healing_policy_digest", value["auto_healing_policy_digest"]
            ),
            tunnel_capability=_require_bool("tunnel_capability", value["tunnel_capability"]),
            tunnel_recovery=value["tunnel_recovery"],
            evidence=HeartbeatEvidence.from_mapping(value["evidence"]),
        )


@dataclass(frozen=True)
class ReplayState:
    """Persistable replay boundary for one peer identity."""

    current_boot_id: str
    highest_sequence: int
    retired_boot_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_identifier("current_boot_id", self.current_boot_id)
        if (
            not isinstance(self.highest_sequence, int)
            or isinstance(self.highest_sequence, bool)
            or self.highest_sequence < 0
        ):
            raise StateValidationError("highest_sequence must be non-negative")
        for boot_id in self.retired_boot_ids:
            _require_identifier("retired_boot_id", boot_id)
        if self.current_boot_id in self.retired_boot_ids:
            raise StateValidationError("current boot identity cannot be retired")

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_boot_id": self.current_boot_id,
            "highest_sequence": self.highest_sequence,
            "retired_boot_ids": list(self.retired_boot_ids),
        }

    @classmethod
    def from_mapping(cls, value: object) -> ReplayState:
        if not isinstance(value, Mapping):
            raise StateValidationError("replay state must be an object")
        _require_exact_keys(
            value, {"current_boot_id", "highest_sequence", "retired_boot_ids"}, "replay state"
        )
        highest = value["highest_sequence"]
        retired = value["retired_boot_ids"]
        if not isinstance(highest, int) or isinstance(highest, bool):
            raise StateValidationError("highest_sequence must be non-negative")
        if not isinstance(retired, list) or not all(isinstance(item, str) for item in retired):
            raise StateValidationError("retired_boot_ids must be a string list")
        return cls(
            current_boot_id=_require_identifier("current_boot_id", value["current_boot_id"]),
            highest_sequence=highest,
            retired_boot_ids=tuple(retired),
        )


class PeerReplayGuard:
    """Reject replayed sequences and retired boot identities after authentication."""

    def __init__(self, state: ReplayState | None = None) -> None:
        self._state = state

    @property
    def state(self) -> ReplayState | None:
        return self._state

    def accept(
        self,
        heartbeat: PeerHeartbeat,
        *,
        authenticated_node_id: str,
        authenticated_certificate_fingerprint: str,
        authenticated_mtls_epoch: int,
        expected_cluster_id: str,
        expected_node_id: str,
    ) -> ReplayState:
        if heartbeat.node_id != authenticated_node_id:
            raise StateValidationError(
                "heartbeat node identity does not match the authenticated peer"
            )
        if heartbeat.cluster_id != expected_cluster_id or heartbeat.node_id != expected_node_id:
            raise StateValidationError("heartbeat is outside the expected cluster or peer identity")
        if (
            heartbeat.certificate_fingerprint != authenticated_certificate_fingerprint
            or heartbeat.mtls_epoch != authenticated_mtls_epoch
        ):
            raise StateValidationError(
                "heartbeat mTLS identity does not match the authenticated peer certificate"
            )

        current = self._state
        if current is None:
            self._state = ReplayState(heartbeat.boot_id, heartbeat.sequence)
            return self._state

        if heartbeat.boot_id == current.current_boot_id:
            if heartbeat.sequence <= current.highest_sequence:
                raise StalePeerStateError("heartbeat sequence is stale or replayed")
            self._state = ReplayState(
                current.current_boot_id, heartbeat.sequence, current.retired_boot_ids
            )
            return self._state

        if heartbeat.boot_id in current.retired_boot_ids:
            raise StalePeerStateError("heartbeat uses a retired boot identity")

        retired = (*current.retired_boot_ids, current.current_boot_id)
        self._state = ReplayState(heartbeat.boot_id, heartbeat.sequence, retired)
        return self._state
