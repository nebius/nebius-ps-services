"""Pure owner-gated route policy for VM-level HA transitions.

This module never discovers cloud ownership, queries FRR, or mutates VPC
resources.  Callers must supply an authoritative ownership observation, the
committed logical static-route manifest, normalized local FRR observations,
and all observed VPC prefix occupancy.  Only explicit ledger ownership grants
mutation authority.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from ..schema import VMHARouteTarget


def _normalized_prefixes(prefixes: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for prefix in prefixes:
        try:
            network = ipaddress.ip_network(str(prefix), strict=True)
        except ValueError as error:
            raise ValueError(f"Invalid route prefix: {prefix}") from error
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError(f"Expected IPv4 route prefix: {prefix}")
        normalized.add(str(network))
    return frozenset(normalized)


class ManagedRouteKind(str, Enum):
    STATIC = "static"
    BGP = "bgp"


class RouteMutationKind(str, Enum):
    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"


class RouteMutationPhase(str, Enum):
    """Durable progress for one route mutation across process restarts."""

    INTENT = "intent"
    DELETE_ACCEPTED = "delete-accepted"
    ORIGINAL_ABSENT = "original-absent"
    CREATE_ACCEPTED = "create-accepted"
    DESIRED_PRESENT = "desired-present"
    RESTORE_ACCEPTED = "restore-accepted"
    RESTORED = "restored"


@dataclass(frozen=True)
class VerifiedAllocationOwnership:
    """One authoritative allocation-owner observation from the cloud adapter."""

    cluster_id: str
    candidate_node_id: str
    observed_owner_node_id: str
    allocation_id: str
    ownership_epoch: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.cluster_id,
                self.candidate_node_id,
                self.observed_owner_node_id,
                self.allocation_id,
            )
        ):
            raise ValueError("Verified allocation ownership identities must be non-empty")
        if not isinstance(self.ownership_epoch, str) or not self.ownership_epoch.isdecimal():
            raise ValueError(
                "Verified allocation ownership epoch must be a Compute resource revision"
            )
        ownership_revision = int(self.ownership_epoch)
        if ownership_revision <= 0:
            raise ValueError(
                "Verified allocation ownership epoch must be a positive Compute resource revision"
            )

    def authorizes(self, *, cluster_id: str, node_id: str) -> bool:
        return bool(
            self.cluster_id == cluster_id
            and self.candidate_node_id == node_id
            and self.observed_owner_node_id == node_id
            and self.allocation_id
            and self.ownership_epoch
        )


@dataclass(frozen=True)
class LogicalStaticRouteManifest:
    """Validated committed logical static-route intent shared by both nodes."""

    digest: str
    prefixes: frozenset[str]

    @classmethod
    def from_committed_json(
        cls,
        manifest_json: str,
        *,
        expected_digest: str,
    ) -> LogicalStaticRouteManifest:
        actual_digest = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("Committed static-route manifest digest mismatch")

        try:
            records = json.loads(manifest_json)
        except json.JSONDecodeError as error:
            raise ValueError("Committed static-route manifest is not valid JSON") from error
        if not isinstance(records, list):
            raise ValueError("Committed static-route manifest must be a list")

        prefixes: list[str] = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("remote_prefixes"), list):
                raise ValueError("Committed static-route manifest record is invalid")
            prefixes.extend(str(prefix) for prefix in record["remote_prefixes"])
        return cls(digest=actual_digest, prefixes=_normalized_prefixes(prefixes))

    @classmethod
    def from_generation(cls, generation: object) -> LogicalStaticRouteManifest:
        """Consume task-1's immutable generation record without importing it."""

        logical_manifests = getattr(generation, "logical_manifests", None)
        digests = getattr(generation, "digests", None)
        manifest_json = getattr(logical_manifests, "static_routes_json", None)
        digest = getattr(digests, "static_routes", None)
        if not isinstance(manifest_json, str) or not isinstance(digest, str):
            raise ValueError("Generation does not contain committed static-route intent")
        return cls.from_committed_json(manifest_json, expected_digest=digest)


@dataclass(frozen=True)
class BGPRouteReadiness:
    """Normalized local FRR, policy, and XFRM readiness for one candidate."""

    configured_sessions: frozenset[str]
    established_sessions: frozenset[str]
    required_prefixes: frozenset[str]
    optional_prefixes: frozenset[str]
    learned_prefixes: frozenset[str]
    usable_xfrm_prefixes: frozenset[str]
    observed_import_policy_digest: str
    committed_import_policy_digest: str

    @classmethod
    def normalize(
        cls,
        *,
        configured_sessions: Iterable[str],
        established_sessions: Iterable[str],
        required_prefixes: Iterable[str],
        optional_prefixes: Iterable[str] = (),
        learned_prefixes: Iterable[str],
        usable_xfrm_prefixes: Iterable[str],
        observed_import_policy_digest: str,
        committed_import_policy_digest: str,
    ) -> BGPRouteReadiness:
        return cls(
            configured_sessions=frozenset(str(item) for item in configured_sessions),
            established_sessions=frozenset(str(item) for item in established_sessions),
            required_prefixes=_normalized_prefixes(required_prefixes),
            optional_prefixes=_normalized_prefixes(optional_prefixes),
            learned_prefixes=_normalized_prefixes(learned_prefixes),
            usable_xfrm_prefixes=_normalized_prefixes(usable_xfrm_prefixes),
            observed_import_policy_digest=str(observed_import_policy_digest),
            committed_import_policy_digest=str(committed_import_policy_digest),
        )

    @property
    def eligible_prefixes(self) -> frozenset[str]:
        if (
            not self.configured_sessions
            or not (self.configured_sessions & self.established_sessions)
            or not self.observed_import_policy_digest
            or not self.committed_import_policy_digest
            or self.observed_import_policy_digest != self.committed_import_policy_digest
        ):
            return frozenset()
        return self.learned_prefixes & self.usable_xfrm_prefixes

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.configured_sessions:
            reasons.append("no-configured-bgp-sessions")
        if not (self.configured_sessions & self.established_sessions):
            reasons.append("configured-bgp-sessions-not-established")
        if (
            not self.observed_import_policy_digest
            or not self.committed_import_policy_digest
            or self.observed_import_policy_digest != self.committed_import_policy_digest
        ):
            reasons.append("bgp-import-policy-mismatch")
        if self.required_prefixes - self.learned_prefixes:
            reasons.append("required-bgp-prefixes-not-learned")
        if self.required_prefixes - self.usable_xfrm_prefixes:
            reasons.append("required-bgp-prefixes-lack-usable-xfrm-next-hop")
        return tuple(reasons)

    @property
    def promotion_ready(self) -> bool:
        return not self.blocked_reasons

    @property
    def missing_optional_prefixes(self) -> frozenset[str]:
        """Informational only; optional parity never blocks promotion."""

        return self.optional_prefixes - self.eligible_prefixes


@dataclass(frozen=True)
class ManagedRouteOwnership:
    """Explicit management ledger entry; route names are never authority."""

    cluster_id: str
    kind: ManagedRouteKind
    route_target: VMHARouteTarget


@dataclass(frozen=True)
class ManagedRouteSnapshot:
    route_id: str
    prefix: str
    allocation_id: str
    ownership: ManagedRouteOwnership
    rollback: RouteRollbackSnapshot | None = None

    def __post_init__(self) -> None:
        normalized = _normalized_prefixes((self.prefix,))
        object.__setattr__(self, "prefix", next(iter(normalized)))

    @property
    def route_target(self) -> VMHARouteTarget:
        return self.ownership.route_target


@dataclass(frozen=True)
class RouteRollbackSnapshot:
    """Normalized restorable content captured before replacing an owned route."""

    route_id: str
    resource_version: str
    name: str
    labels: tuple[tuple[str, str], ...]
    description: str
    prefix: str
    allocation_id: str
    route_target: VMHARouteTarget

    def __post_init__(self) -> None:
        normalized = _normalized_prefixes((self.prefix,))
        object.__setattr__(self, "prefix", next(iter(normalized)))
        if not all((self.route_id, self.resource_version, self.name, self.allocation_id)):
            raise ValueError("VM-HA route rollback identity is incomplete")
        if not self.resource_version.isdecimal() or int(self.resource_version) <= 0:
            raise ValueError("VM-HA route rollback revision must be positive")
        if self.labels != tuple(sorted(set(self.labels))) or any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            for key, value in self.labels
        ):
            raise ValueError("VM-HA route rollback labels are not canonical")

    def to_dict(self) -> dict[str, object]:
        return {
            "allocation_id": self.allocation_id,
            "description": self.description,
            "labels": {key: value for key, value in self.labels},
            "name": self.name,
            "prefix": self.prefix,
            "resource_version": self.resource_version,
            "route_id": self.route_id,
            "route_target": self.route_target.model_dump(mode="json"),
        }

    @classmethod
    def from_mapping(cls, value: object) -> RouteRollbackSnapshot:
        if not isinstance(value, Mapping) or set(value) != {
            "allocation_id",
            "description",
            "labels",
            "name",
            "prefix",
            "resource_version",
            "route_id",
            "route_target",
        }:
            raise ValueError("VM-HA route rollback snapshot is malformed")
        labels = value.get("labels")
        if not isinstance(labels, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in labels.items()
        ):
            raise ValueError("VM-HA route rollback labels are malformed")
        string_fields = (
            "allocation_id",
            "description",
            "name",
            "prefix",
            "resource_version",
            "route_id",
        )
        if any(not isinstance(value.get(field), str) for field in string_fields):
            raise ValueError("VM-HA route rollback snapshot fields are malformed")
        return cls(
            route_id=value["route_id"],
            resource_version=value["resource_version"],
            name=value["name"],
            labels=tuple(sorted(labels.items())),
            description=value["description"],
            prefix=value["prefix"],
            allocation_id=value["allocation_id"],
            route_target=VMHARouteTarget.model_validate(value["route_target"]),
        )


@dataclass(frozen=True)
class RouteOccupancySnapshot:
    """Observed prefix occupancy without authority to mutate the route."""

    route_id: str
    prefix: str
    next_hop: str
    route_target: VMHARouteTarget

    def __post_init__(self) -> None:
        if not self.route_id:
            raise ValueError("Observed route ID is required")
        if not self.next_hop:
            raise ValueError("Observed route next hop is required")
        normalized = _normalized_prefixes((self.prefix,))
        object.__setattr__(self, "prefix", next(iter(normalized)))


@dataclass(frozen=True)
class RouteMutation:
    kind: RouteMutationKind
    prefix: str
    route_kind: ManagedRouteKind
    allocation_id: str
    cluster_id: str
    route_target: VMHARouteTarget
    route_id: str | None = None
    rollback: RouteRollbackSnapshot | None = None

    def __post_init__(self) -> None:
        normalized = _normalized_prefixes((self.prefix,))
        object.__setattr__(self, "prefix", next(iter(normalized)))
        if self.rollback is not None and (
            self.kind is not RouteMutationKind.REPLACE
            or self.route_id != self.rollback.route_id
            or self.prefix != self.rollback.prefix
            or self.route_target != self.rollback.route_target
        ):
            raise ValueError("VM-HA route rollback snapshot does not match its replacement")

    @property
    def operation_id(self) -> str:
        target = self.route_id or self.prefix
        return (
            f"{self.kind.value}:{self.route_target.route_table_id}:{target}:"
            f"{self.route_kind.value}:{self.allocation_id}"
        )


@dataclass(frozen=True)
class AcceptedRouteOperation:
    action_operation_id: str
    action: str
    cloud_operation_id: str

    def __post_init__(self) -> None:
        if self.action not in {"create", "delete", "restore"} or not all(
            (self.action_operation_id, self.cloud_operation_id)
        ):
            raise ValueError("VM-HA accepted route operation identity is invalid")


@dataclass(frozen=True)
class PendingRouteMutation:
    mutation: RouteMutation
    context: RouteReconciliationContext
    phase: RouteMutationPhase = RouteMutationPhase.INTENT
    accepted_operation: AcceptedRouteOperation | None = None
    record_version: int = 2

    def __post_init__(self) -> None:
        if self.record_version not in {1, 2}:
            raise ValueError("VM-HA route mutation intent version is invalid")
        if self.record_version == 1 and (
            self.mutation.rollback is not None
            or self.phase is not RouteMutationPhase.INTENT
            or self.accepted_operation is not None
        ):
            raise ValueError("VM-HA legacy route mutation intent is not byte-compatible")
        if (
            self.record_version == 2
            and self.mutation.kind is RouteMutationKind.REPLACE
            and self.mutation.rollback is None
        ):
            raise ValueError("VM-HA replacement intent requires an exact rollback snapshot")
        allowed_phases = {
            RouteMutationKind.CREATE: {
                RouteMutationPhase.INTENT,
                RouteMutationPhase.CREATE_ACCEPTED,
                RouteMutationPhase.DESIRED_PRESENT,
            },
            RouteMutationKind.DELETE: {
                RouteMutationPhase.INTENT,
                RouteMutationPhase.DELETE_ACCEPTED,
                RouteMutationPhase.ORIGINAL_ABSENT,
            },
            RouteMutationKind.REPLACE: set(RouteMutationPhase),
        }[self.mutation.kind]
        if self.phase not in allowed_phases:
            raise ValueError("VM-HA route mutation phase does not match its action")
        expected_action = {
            RouteMutationPhase.DELETE_ACCEPTED: "delete",
            RouteMutationPhase.CREATE_ACCEPTED: "create",
            RouteMutationPhase.RESTORE_ACCEPTED: "restore",
        }.get(self.phase)
        if (self.accepted_operation is None) != (expected_action is None) or (
            self.accepted_operation is not None
            and self.accepted_operation.action != expected_action
        ):
            raise ValueError("VM-HA accepted route operation does not match its phase")


class RouteReplacementCompensated(RuntimeError):
    """The desired replacement failed terminally and the original was restored."""

    def __init__(self, restored_route_id: str) -> None:
        super().__init__("VM-HA route replacement failed and was exactly compensated")
        self.restored_route_id = restored_route_id


@dataclass(frozen=True)
class RouteTransitionState:
    takeover_started_at: float
    absent_bgp_observations: tuple[tuple[str, int], ...] = ()

    def absence_counts(self) -> dict[str, int]:
        return dict(self.absent_bgp_observations)


@dataclass(frozen=True)
class RouteReconciliationPlan:
    authorized: bool
    blocked_reasons: tuple[str, ...]
    mutations: tuple[RouteMutation, ...]
    held_bgp_prefixes: frozenset[str]
    desired_prefixes: frozenset[str]
    next_state: RouteTransitionState
    ownership: VerifiedAllocationOwnership | None = None
    route_targets: tuple[VMHARouteTarget, ...] = ()


@dataclass(frozen=True)
class RouteReconciliationContext:
    """Complete controller operation identity covered by one route receipt."""

    operation_id: str
    cluster_id: str
    owner_node_id: str
    allocation_id: str
    ownership_epoch: str
    generation_id: str
    configuration_digest: str
    static_routes_digest: str
    bgp_policy_digest: str
    ownership_incarnation: int

    def __post_init__(self) -> None:
        values = (
            self.operation_id,
            self.cluster_id,
            self.owner_node_id,
            self.allocation_id,
            self.ownership_epoch,
            self.generation_id,
            self.configuration_digest,
            self.static_routes_digest,
            self.bgp_policy_digest,
        )
        if not all(values):
            raise ValueError("Route reconciliation context identities must be non-empty")
        if not isinstance(self.ownership_epoch, str) or not self.ownership_epoch.isdecimal():
            raise ValueError(
                "Route reconciliation ownership epoch must be a Compute resource revision"
            )
        ownership_revision = int(self.ownership_epoch)
        if ownership_revision <= 0:
            raise ValueError(
                "Route reconciliation ownership epoch must be a positive Compute resource revision"
            )
        for digest in (
            self.configuration_digest,
            self.static_routes_digest,
            self.bgp_policy_digest,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("Route reconciliation policy digests must be lowercase SHA-256")
        if self.ownership_incarnation < 0:
            raise ValueError("Route reconciliation ownership incarnation must be non-negative")

    def matches(self, ownership: VerifiedAllocationOwnership) -> bool:
        return bool(
            ownership.authorizes(
                cluster_id=self.cluster_id,
                node_id=self.owner_node_id,
            )
            and ownership.allocation_id == self.allocation_id
            and ownership.ownership_epoch == self.ownership_epoch
        )

    def has_same_authority(self, other: RouteReconciliationContext) -> bool:
        """Compare reboot-stable authority while excluding the per-boot operation ID."""

        return all(
            getattr(self, field) == getattr(other, field)
            for field in (
                "cluster_id",
                "owner_node_id",
                "allocation_id",
                "ownership_epoch",
                "generation_id",
                "configuration_digest",
                "static_routes_digest",
                "bgp_policy_digest",
                "ownership_incarnation",
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "allocation_id": self.allocation_id,
            "bgp_policy_digest": self.bgp_policy_digest,
            "cluster_id": self.cluster_id,
            "configuration_digest": self.configuration_digest,
            "generation_id": self.generation_id,
            "operation_id": self.operation_id,
            "owner_node_id": self.owner_node_id,
            "ownership_epoch": self.ownership_epoch,
            "ownership_incarnation": self.ownership_incarnation,
            "schema": "nebius-vpngw/vm-ha-route-reconciliation-context-v1",
            "static_routes_digest": self.static_routes_digest,
        }

    @classmethod
    def from_mapping(cls, value: object) -> RouteReconciliationContext:
        if not isinstance(value, Mapping):
            raise ValueError("Route reconciliation context must be an object")
        expected = {
            "allocation_id",
            "bgp_policy_digest",
            "cluster_id",
            "configuration_digest",
            "generation_id",
            "operation_id",
            "owner_node_id",
            "ownership_epoch",
            "ownership_incarnation",
            "schema",
            "static_routes_digest",
        }
        if set(value) != expected or value.get("schema") != (
            "nebius-vpngw/vm-ha-route-reconciliation-context-v1"
        ):
            raise ValueError("Route reconciliation context has an invalid shape")

        def string_field(name: str) -> str:
            field = value[name]
            if not isinstance(field, str):
                raise ValueError("Route reconciliation context string fields are invalid")
            return field

        ownership_incarnation = value["ownership_incarnation"]
        if not isinstance(ownership_incarnation, int) or isinstance(ownership_incarnation, bool):
            raise ValueError("Route reconciliation ownership incarnation is invalid")
        return cls(
            operation_id=string_field("operation_id"),
            cluster_id=string_field("cluster_id"),
            owner_node_id=string_field("owner_node_id"),
            allocation_id=string_field("allocation_id"),
            ownership_epoch=string_field("ownership_epoch"),
            generation_id=string_field("generation_id"),
            configuration_digest=string_field("configuration_digest"),
            static_routes_digest=string_field("static_routes_digest"),
            bgp_policy_digest=string_field("bgp_policy_digest"),
            ownership_incarnation=ownership_incarnation,
        )


@dataclass(frozen=True)
class RouteReconciliationReceipt:
    """Secret-free durable proof that one exact route operation completed."""

    context: RouteReconciliationContext
    plan_digest: str

    def __post_init__(self) -> None:
        if len(self.plan_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.plan_digest
        ):
            raise ValueError("Route reconciliation plan digest must be lowercase SHA-256")

    def to_dict(self) -> dict[str, object]:
        return {
            "context": self.context.to_dict(),
            "plan_digest": self.plan_digest,
            "schema": "nebius-vpngw/vm-ha-route-reconciliation-receipt-v1",
        }

    @classmethod
    def from_mapping(cls, value: object) -> RouteReconciliationReceipt:
        if not isinstance(value, Mapping) or set(value) != {
            "context",
            "plan_digest",
            "schema",
        }:
            raise ValueError("Route reconciliation receipt has an invalid shape")
        if value["schema"] != "nebius-vpngw/vm-ha-route-reconciliation-receipt-v1":
            raise ValueError("Route reconciliation receipt has an unsupported schema")
        if not isinstance(value["plan_digest"], str):
            raise ValueError("Route reconciliation receipt plan digest is invalid")
        return cls(
            context=RouteReconciliationContext.from_mapping(value["context"]),
            plan_digest=value["plan_digest"],
        )


@dataclass(frozen=True)
class RouteApplyResult:
    applied: tuple[RouteMutation, ...]
    failed: RouteMutation | None
    remaining: tuple[RouteMutation, ...]
    committed_state: RouteTransitionState | None
    receipt: RouteReconciliationReceipt | None = None


class VMHARouteReconciler:
    """Plan deterministic owner-only route mutations for one HA cluster."""

    def __init__(
        self,
        *,
        cluster_id: str,
        node_id: str,
        takeover_hold_down_seconds: float,
        withdrawal_stability_observations: int,
        route_targets: tuple[VMHARouteTarget, ...],
    ) -> None:
        if not cluster_id or not node_id:
            raise ValueError("cluster_id and node_id are required")
        if not math.isfinite(takeover_hold_down_seconds) or takeover_hold_down_seconds < 0:
            raise ValueError("takeover_hold_down_seconds must be finite and non-negative")
        if withdrawal_stability_observations < 1:
            raise ValueError("withdrawal_stability_observations must be positive")
        if not route_targets:
            raise ValueError("VM-HA route reconciliation requires exact route targets")
        self.cluster_id = cluster_id
        self.node_id = node_id
        self.takeover_hold_down_seconds = takeover_hold_down_seconds
        self.withdrawal_stability_observations = withdrawal_stability_observations
        self.route_targets = route_targets

    def plan(
        self,
        *,
        ownership: VerifiedAllocationOwnership,
        static_manifest: LogicalStaticRouteManifest,
        bgp: BGPRouteReadiness,
        existing_routes: Iterable[ManagedRouteSnapshot | RouteOccupancySnapshot],
        state: RouteTransitionState,
        now: float,
    ) -> RouteReconciliationPlan:
        if not math.isfinite(now) or not math.isfinite(state.takeover_started_at):
            raise ValueError("Route observation times must be finite")
        if now < state.takeover_started_at:
            raise ValueError("Route observation time precedes takeover start")
        for prefix, observations in state.absent_bgp_observations:
            _normalized_prefixes((prefix,))
            if observations < 0:
                raise ValueError("BGP absence observations must be non-negative")
        if not ownership.authorizes(cluster_id=self.cluster_id, node_id=self.node_id):
            return RouteReconciliationPlan(
                authorized=False,
                blocked_reasons=("allocation-owner-not-verified-for-candidate",),
                mutations=(),
                held_bgp_prefixes=frozenset(),
                desired_prefixes=frozenset(),
                next_state=state,
                ownership=None,
                route_targets=self.route_targets,
            )

        routes = tuple(existing_routes)
        undeclared_targets = sorted(
            {
                route.route_target.route_table_id
                for route in routes
                if route.route_target not in self.route_targets
            }
        )
        if undeclared_targets:
            return RouteReconciliationPlan(
                authorized=True,
                blocked_reasons=tuple(
                    f"undeclared-route-target:{route_table_id}"
                    for route_table_id in undeclared_targets
                ),
                mutations=(),
                held_bgp_prefixes=frozenset(),
                desired_prefixes=frozenset(),
                next_state=state,
                ownership=ownership,
                route_targets=self.route_targets,
            )
        owned_routes = tuple(
            route
            for route in routes
            if isinstance(route, ManagedRouteSnapshot)
            and route.ownership.cluster_id == self.cluster_id
            and route.ownership.route_target in self.route_targets
        )
        foreign_prefixes = {
            (route.route_target, route.prefix)
            for route in routes
            if not isinstance(route, ManagedRouteSnapshot)
            or route.ownership.cluster_id != self.cluster_id
            or route.ownership.route_target not in self.route_targets
        }
        existing_bgp = {
            route.prefix for route in owned_routes if route.ownership.kind is ManagedRouteKind.BGP
        }
        eligible_bgp = bgp.eligible_prefixes
        hold_down_active = now - state.takeover_started_at < self.takeover_hold_down_seconds
        previous_absence = state.absence_counts()
        next_absence: dict[str, int] = {}
        held_bgp: set[str] = set()
        withdrawable_bgp: set[str] = set()

        for prefix in existing_bgp - eligible_bgp:
            if hold_down_active or not bgp.promotion_ready:
                held_bgp.add(prefix)
                continue
            observations = previous_absence.get(prefix, 0) + 1
            if observations < self.withdrawal_stability_observations:
                next_absence[prefix] = observations
                held_bgp.add(prefix)
            else:
                withdrawable_bgp.add(prefix)

        desired_kinds = {prefix: ManagedRouteKind.BGP for prefix in eligible_bgp | held_bgp}
        desired_kinds.update(
            {prefix: ManagedRouteKind.STATIC for prefix in static_manifest.prefixes}
        )
        mutations: list[RouteMutation] = []
        blocked_reasons = list(bgp.blocked_reasons)
        foreign_conflicts = sorted(
            (
                (target, prefix)
                for target in self.route_targets
                for prefix in desired_kinds
                if (target, prefix) in foreign_prefixes
            ),
            key=lambda item: (item[0].route_table_id, item[1]),
        )
        if foreign_conflicts:
            blocked_reasons.extend(
                f"foreign-route-conflict:{target.route_table_id}:{prefix}"
                for target, prefix in foreign_conflicts
            )
            return RouteReconciliationPlan(
                authorized=True,
                blocked_reasons=tuple(dict.fromkeys(blocked_reasons)),
                mutations=(),
                held_bgp_prefixes=frozenset(held_bgp),
                desired_prefixes=frozenset(desired_kinds),
                next_state=state,
                ownership=ownership,
                route_targets=self.route_targets,
            )

        owned_by_prefix: dict[tuple[VMHARouteTarget, str], list[ManagedRouteSnapshot]] = {}
        for route in owned_routes:
            owned_by_prefix.setdefault((route.ownership.route_target, route.prefix), []).append(
                route
            )
        retained_route_ids: set[str] = set()

        for route_target in self.route_targets:
            for prefix, route_kind in sorted(
                desired_kinds.items(), key=lambda item: (item[1].value, item[0])
            ):
                existing_candidates = owned_by_prefix.get((route_target, prefix), [])
                if not existing_candidates:
                    mutations.append(
                        RouteMutation(
                            kind=RouteMutationKind.CREATE,
                            prefix=prefix,
                            route_kind=route_kind,
                            allocation_id=ownership.allocation_id,
                            cluster_id=self.cluster_id,
                            route_target=route_target,
                        )
                    )
                    continue
                existing = next(
                    (
                        route
                        for route in existing_candidates
                        if route.allocation_id == ownership.allocation_id
                        and route.ownership.kind is route_kind
                    ),
                    existing_candidates[0],
                )
                retained_route_ids.add(existing.route_id)
                if (
                    existing.allocation_id != ownership.allocation_id
                    or existing.ownership.kind is not route_kind
                ):
                    mutations.append(
                        RouteMutation(
                            kind=RouteMutationKind.REPLACE,
                            prefix=prefix,
                            route_kind=route_kind,
                            allocation_id=ownership.allocation_id,
                            cluster_id=self.cluster_id,
                            route_target=route_target,
                            route_id=existing.route_id,
                            rollback=existing.rollback,
                        )
                    )

        for route in owned_routes:
            if route.route_id in retained_route_ids:
                continue
            if route.prefix in desired_kinds:
                mutations.append(
                    RouteMutation(
                        kind=RouteMutationKind.DELETE,
                        prefix=route.prefix,
                        route_kind=route.ownership.kind,
                        allocation_id=route.allocation_id,
                        cluster_id=self.cluster_id,
                        route_target=route.ownership.route_target,
                        route_id=route.route_id,
                    )
                )
                continue
            if (
                route.ownership.kind is ManagedRouteKind.BGP
                and route.prefix not in withdrawable_bgp
            ):
                continue
            mutations.append(
                RouteMutation(
                    kind=RouteMutationKind.DELETE,
                    prefix=route.prefix,
                    route_kind=route.ownership.kind,
                    allocation_id=route.allocation_id,
                    cluster_id=self.cluster_id,
                    route_target=route.ownership.route_target,
                    route_id=route.route_id,
                )
            )

        next_state = RouteTransitionState(
            takeover_started_at=state.takeover_started_at,
            absent_bgp_observations=tuple(sorted(next_absence.items())),
        )
        return RouteReconciliationPlan(
            authorized=True,
            blocked_reasons=tuple(dict.fromkeys(blocked_reasons)),
            mutations=tuple(mutations),
            held_bgp_prefixes=frozenset(held_bgp),
            desired_prefixes=frozenset(desired_kinds),
            next_state=next_state,
            ownership=ownership,
            route_targets=self.route_targets,
        )


def _route_plan_digest(plan: RouteReconciliationPlan) -> str:
    payload = {
        "desired_prefixes": sorted(plan.desired_prefixes),
        "route_targets": [target.model_dump(mode="json") for target in plan.route_targets],
        "next_state": {
            "absent_bgp_observations": list(plan.next_state.absent_bgp_observations),
            "takeover_started_at": plan.next_state.takeover_started_at,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _has_route_safety_blocker(plan: RouteReconciliationPlan) -> bool:
    return any(reason.startswith("foreign-route-conflict:") for reason in plan.blocked_reasons)


def execute_route_plan(
    plan: RouteReconciliationPlan,
    apply_mutation: Callable[[RouteMutation], None],
    *,
    context: RouteReconciliationContext,
    reobserve_ownership: Callable[[], VerifiedAllocationOwnership],
    reobserve_plan: Callable[[], RouteReconciliationPlan],
    persist_receipt: Callable[[Mapping[str, object]], None],
    observe_receipt: Callable[[], Mapping[str, object] | None],
) -> RouteApplyResult:
    """Apply only for the exact current owner and durably re-observe completion."""

    ownership = plan.ownership
    if not plan.authorized or ownership is None or not context.matches(ownership):
        return RouteApplyResult((), None, (), None, None)
    plan_digest = _route_plan_digest(plan)
    if not context.matches(reobserve_ownership()):
        return RouteApplyResult((), None, plan.mutations, None, None)
    current = reobserve_plan()
    observed = observe_receipt()
    if observed is not None:
        receipt = RouteReconciliationReceipt.from_mapping(observed)
        if receipt.context == context and receipt.plan_digest == plan_digest:
            if (
                not current.authorized
                or current.ownership is None
                or not context.matches(current.ownership)
                or _has_route_safety_blocker(current)
                or current.mutations
                or _route_plan_digest(current) != plan_digest
                or not context.matches(reobserve_ownership())
            ):
                raise RuntimeError("Current route observation does not satisfy the durable receipt")
            return RouteApplyResult((), None, (), plan.next_state, receipt)
    if current != plan:
        raise RuntimeError("Route plan changed before mutation")
    applied: list[RouteMutation] = []
    for index, mutation in enumerate(plan.mutations):
        if not context.matches(reobserve_ownership()):
            return RouteApplyResult(
                applied=tuple(applied),
                failed=mutation,
                remaining=plan.mutations[index:],
                committed_state=None,
                receipt=None,
            )
        try:
            apply_mutation(mutation)
        except Exception:
            return RouteApplyResult(
                applied=tuple(applied),
                failed=mutation,
                remaining=plan.mutations[index:],
                committed_state=None,
                receipt=None,
            )
        applied.append(mutation)
    if not context.matches(reobserve_ownership()):
        return RouteApplyResult(
            applied=tuple(applied),
            failed=None,
            remaining=(),
            committed_state=None,
            receipt=None,
        )
    current = reobserve_plan()
    if (
        not current.authorized
        or current.ownership is None
        or not context.matches(current.ownership)
        or _has_route_safety_blocker(current)
        or current.mutations
        or _route_plan_digest(current) != plan_digest
        or not context.matches(reobserve_ownership())
    ):
        raise RuntimeError("Route reconciliation postcondition was not observed")
    receipt = RouteReconciliationReceipt(context=context, plan_digest=plan_digest)
    persist_receipt(receipt.to_dict())
    observed = observe_receipt()
    if observed is None:
        raise RuntimeError("Route reconciliation receipt was not durably observed")
    durable_receipt = RouteReconciliationReceipt.from_mapping(observed)
    if durable_receipt != receipt:
        raise RuntimeError("Route reconciliation receipt does not match the current operation")
    return RouteApplyResult(
        applied=tuple(applied),
        failed=None,
        remaining=(),
        committed_state=plan.next_state,
        receipt=durable_receipt,
    )


def owned_route_snapshots(
    routes: Iterable[object],
    *,
    ownership_by_route_id: Mapping[str, ManagedRouteOwnership],
    route_id: Callable[[object], str],
    route_prefix: Callable[[object], str | None],
    route_allocation_id: Callable[[object], str | None],
) -> tuple[ManagedRouteSnapshot, ...]:
    """Normalize only routes present in the explicit management ledger."""

    snapshots: list[ManagedRouteSnapshot] = []
    for route in routes:
        identifier = route_id(route)
        ownership = ownership_by_route_id.get(identifier)
        if ownership is None:
            continue
        prefix = route_prefix(route)
        allocation_id = route_allocation_id(route)
        if not identifier or not prefix or not allocation_id:
            continue
        snapshots.append(
            ManagedRouteSnapshot(
                route_id=identifier,
                prefix=prefix,
                allocation_id=allocation_id,
                ownership=ownership,
            )
        )
    return tuple(sorted(snapshots, key=lambda item: (item.prefix, item.route_id)))


def route_observation_snapshots(
    routes: Iterable[object],
    *,
    ownership_by_route_id: Mapping[str, ManagedRouteOwnership],
    route_id: Callable[[object], str],
    route_prefix: Callable[[object], str | None],
    route_allocation_id: Callable[[object], str | None],
    route_next_hop: Callable[[object], str],
    route_rollback: Callable[[object, VMHARouteTarget], RouteRollbackSnapshot],
    route_target: VMHARouteTarget,
) -> tuple[ManagedRouteSnapshot | RouteOccupancySnapshot, ...]:
    """Normalize every route as mutable ledger ownership or read-only occupancy."""

    snapshots: list[ManagedRouteSnapshot | RouteOccupancySnapshot] = []
    for route in routes:
        identifier = route_id(route)
        prefix = route_prefix(route)
        next_hop = route_next_hop(route)
        if not identifier or not prefix:
            raise ValueError("Observed route identity and prefix are required")
        ownership = ownership_by_route_id.get(identifier)
        if ownership is None:
            snapshots.append(
                RouteOccupancySnapshot(
                    route_id=identifier,
                    prefix=prefix,
                    next_hop=next_hop,
                    route_target=route_target,
                )
            )
            continue
        allocation_id = route_allocation_id(route)
        if not allocation_id:
            raise ValueError("Ledger-owned route must have an allocation next hop")
        snapshots.append(
            ManagedRouteSnapshot(
                route_id=identifier,
                prefix=prefix,
                allocation_id=allocation_id,
                ownership=ownership,
                rollback=route_rollback(route, route_target),
            )
        )
    return tuple(sorted(snapshots, key=lambda item: (item.prefix, item.route_id)))
