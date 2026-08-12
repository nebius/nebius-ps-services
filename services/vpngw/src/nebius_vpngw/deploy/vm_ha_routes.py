"""Pure owner-gated route policy for VM-level HA transitions.

This module never discovers cloud ownership, queries FRR, or mutates VPC
resources.  Callers must supply an authoritative ownership observation, the
committed logical static-route manifest, normalized local FRR observations,
and an explicitly owned VPC route snapshot.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum


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


@dataclass(frozen=True)
class VerifiedAllocationOwnership:
    """One authoritative allocation-owner observation from the cloud adapter."""

    cluster_id: str
    candidate_node_id: str
    observed_owner_node_id: str
    allocation_id: str

    def authorizes(self, *, cluster_id: str, node_id: str) -> bool:
        return bool(
            self.cluster_id == cluster_id
            and self.candidate_node_id == node_id
            and self.observed_owner_node_id == node_id
            and self.allocation_id
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
            or not self.configured_sessions.issubset(self.established_sessions)
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
        missing_sessions = self.configured_sessions - self.established_sessions
        if missing_sessions:
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


@dataclass(frozen=True)
class ManagedRouteSnapshot:
    route_id: str
    prefix: str
    allocation_id: str
    ownership: ManagedRouteOwnership

    def __post_init__(self) -> None:
        normalized = _normalized_prefixes((self.prefix,))
        object.__setattr__(self, "prefix", next(iter(normalized)))


@dataclass(frozen=True)
class RouteMutation:
    kind: RouteMutationKind
    prefix: str
    route_kind: ManagedRouteKind
    allocation_id: str
    cluster_id: str
    route_id: str | None = None

    @property
    def operation_id(self) -> str:
        target = self.route_id or self.prefix
        return f"{self.kind.value}:{target}:{self.route_kind.value}:{self.allocation_id}"


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


@dataclass(frozen=True)
class RouteApplyResult:
    applied: tuple[RouteMutation, ...]
    failed: RouteMutation | None
    remaining: tuple[RouteMutation, ...]
    committed_state: RouteTransitionState | None


class VMHARouteReconciler:
    """Plan deterministic owner-only route mutations for one HA cluster."""

    def __init__(
        self,
        *,
        cluster_id: str,
        node_id: str,
        takeover_hold_down_seconds: float,
        withdrawal_stability_observations: int,
    ) -> None:
        if not cluster_id or not node_id:
            raise ValueError("cluster_id and node_id are required")
        if not math.isfinite(takeover_hold_down_seconds) or takeover_hold_down_seconds < 0:
            raise ValueError("takeover_hold_down_seconds must be finite and non-negative")
        if withdrawal_stability_observations < 1:
            raise ValueError("withdrawal_stability_observations must be positive")
        self.cluster_id = cluster_id
        self.node_id = node_id
        self.takeover_hold_down_seconds = takeover_hold_down_seconds
        self.withdrawal_stability_observations = withdrawal_stability_observations

    def plan(
        self,
        *,
        ownership: VerifiedAllocationOwnership,
        static_manifest: LogicalStaticRouteManifest,
        bgp: BGPRouteReadiness,
        existing_routes: Iterable[ManagedRouteSnapshot],
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
            )

        routes = tuple(existing_routes)
        owned_routes = tuple(
            route for route in routes if route.ownership.cluster_id == self.cluster_id
        )
        foreign_prefixes = {
            route.prefix for route in routes if route.ownership.cluster_id != self.cluster_id
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

        owned_by_prefix: dict[str, list[ManagedRouteSnapshot]] = {}
        for route in owned_routes:
            owned_by_prefix.setdefault(route.prefix, []).append(route)
        retained_route_ids: set[str] = set()

        for prefix, route_kind in sorted(
            desired_kinds.items(), key=lambda item: (item[1].value, item[0])
        ):
            existing_candidates = owned_by_prefix.get(prefix, [])
            if not existing_candidates and prefix in foreign_prefixes:
                blocked_reasons.append(f"foreign-route-conflict:{prefix}")
                continue
            if not existing_candidates:
                mutations.append(
                    RouteMutation(
                        kind=RouteMutationKind.CREATE,
                        prefix=prefix,
                        route_kind=route_kind,
                        allocation_id=ownership.allocation_id,
                        cluster_id=self.cluster_id,
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
                        route_id=existing.route_id,
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
        )


def execute_route_plan(
    plan: RouteReconciliationPlan,
    apply_mutation: Callable[[RouteMutation], None],
) -> RouteApplyResult:
    """Apply in order and stop at the first failure for safe deterministic retry."""

    if not plan.authorized:
        return RouteApplyResult((), None, (), None)
    applied: list[RouteMutation] = []
    for index, mutation in enumerate(plan.mutations):
        try:
            apply_mutation(mutation)
        except Exception:
            return RouteApplyResult(
                applied=tuple(applied),
                failed=mutation,
                remaining=plan.mutations[index:],
                committed_state=None,
            )
        applied.append(mutation)
    return RouteApplyResult(
        applied=tuple(applied),
        failed=None,
        remaining=(),
        committed_state=plan.next_state,
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
