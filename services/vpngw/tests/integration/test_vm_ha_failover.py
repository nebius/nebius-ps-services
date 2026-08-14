from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

import pytest

from nebius_vpngw.agent.vm_ha import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    ControllerSnapshot,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    RecoverableController,
    RouteReconciliationContext,
    VMHAController,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    BGPRouteReadiness,
    LogicalStaticRouteManifest,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    RouteMutationKind,
    RouteTransitionState,
    VerifiedAllocationOwnership,
    VMHARouteReconciler,
)
from nebius_vpngw.schema import VMHARouteTarget

GENERATION = "a" * 64
DIGESTS = DigestSet(GENERATION, "b" * 64, "c" * 64)
TAKEOVER_EFFECTS = (
    ActionKind.STOP_FORMER_OWNER,
    ActionKind.DETACH_FORMER_ATTACHMENT,
    ActionKind.ATTACH_CANDIDATE,
    ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
    ActionKind.RECONCILE_ROUTES,
    ActionKind.ENABLE_ACTIVE,
)


def _target() -> VMHARouteTarget:
    return VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )


def _static_manifest(*prefixes: str) -> LogicalStaticRouteManifest:
    payload = json.dumps(
        [{"connection": "site-a", "remote_prefixes": list(prefixes)}],
        sort_keys=True,
        separators=(",", ":"),
    )
    return LogicalStaticRouteManifest.from_committed_json(
        payload,
        expected_digest=hashlib.sha256(payload.encode()).hexdigest(),
    )


def _bgp(
    *,
    learned: tuple[str, ...] = ("10.20.0.0/16",),
    usable: tuple[str, ...] = ("10.20.0.0/16",),
    required: tuple[str, ...] = ("10.20.0.0/16",),
) -> BGPRouteReadiness:
    return BGPRouteReadiness.normalize(
        configured_sessions=("169.254.10.2",),
        established_sessions=("169.254.10.2",),
        required_prefixes=required,
        optional_prefixes=(),
        learned_prefixes=learned,
        usable_xfrm_prefixes=usable,
        observed_import_policy_digest="policy-a",
        committed_import_policy_digest="policy-a",
    )


def _owner() -> VerifiedAllocationOwnership:
    return VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-b",
        observed_owner_node_id="node-b",
        allocation_id="shared-allocation",
        ownership_epoch="7",
    )


def _route(prefix: str, *, kind: ManagedRouteKind) -> ManagedRouteSnapshot:
    return ManagedRouteSnapshot(
        route_id=f"route-{prefix}",
        prefix=prefix,
        allocation_id="shared-allocation",
        ownership=ManagedRouteOwnership(cluster_id="cluster-a", kind=kind, route_target=_target()),
    )


def _reconciler(*, hold_down: float, observations: int) -> VMHARouteReconciler:
    return VMHARouteReconciler(
        cluster_id="cluster-a",
        node_id="node-b",
        takeover_hold_down_seconds=hold_down,
        withdrawal_stability_observations=observations,
        route_targets=(_target(),),
    )


@dataclass
class FakeClock:
    now: float = 100.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeNode:
    node_id: str
    role: ConfiguredRole
    mode: DataPlaneMode
    checkpoint: ControllerCheckpoint = ControllerCheckpoint()
    guard_boot_id: str | None = None
    peer_received_at: float | None = 99.0
    peer_generation: str = GENERATION
    routes: RouteReconciliationContext | None = None
    manual_failback: bool = False

    @property
    def boot_id(self) -> str:
        return f"boot-{self.node_id}"


@dataclass
class FakeCloud:
    authoritative: bool = True
    attachment: str | None = "node-a"
    confirmed_owner: str | None = "node-a"
    ownership_revision: int = 1

    def __post_init__(self) -> None:
        self.compute = {
            "node-a": ComputeState.RUNNING,
            "node-b": ComputeState.RUNNING,
        }

    def observe(self, *, local: str, peer: str) -> CloudObservation:
        return CloudObservation(
            authoritative=self.authoritative,
            allocation_id="allocation-a",
            observed_owner_node_id=self.attachment,
            former_owner_node_id=peer,
            former_owner_compute_state=self.compute[peer],
            former_attachment_absent=self.attachment != peer,
            candidate_attachment_exact=self.attachment == local,
            ownership_re_read_exact=(self.attachment == local and self.confirmed_owner == local),
            ownership_epoch=str(self.ownership_revision),
        )


class MemoryCheckpoints:
    def __init__(self, node: FakeNode) -> None:
        self.node = node
        self.fail_next_save = False

    def load(self) -> ControllerCheckpoint:
        return self.node.checkpoint

    def save(self, checkpoint: ControllerCheckpoint) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("injected checkpoint failure")
        self.node.checkpoint = checkpoint


class ScenarioEffects:
    def __init__(self, scenario: TwoNodeScenario, local: str) -> None:
        self.scenario = scenario
        self.local = local
        self.applied_operation_ids: set[str] = set()
        self.crashed_operation_ids: set[str] = set()
        self.crash_after: ActionKind | None = None
        self.fail_before: ActionKind | None = None

    def apply(self, action: ControllerAction) -> None:
        self.scenario.attempts.append(action.kind)
        if action.kind is self.fail_before:
            raise RuntimeError(f"injected-{action.kind.value}-failure")
        if action.operation_id not in self.applied_operation_ids:
            self._apply_once(action)
            self.applied_operation_ids.add(action.operation_id)
            self.scenario.trace.append(action.kind)
        if (
            action.kind is self.crash_after
            and action.operation_id not in self.crashed_operation_ids
        ):
            self.crashed_operation_ids.add(action.operation_id)
            raise RuntimeError(f"crash-after-{action.kind.value}")

    def _apply_once(self, action: ControllerAction) -> None:
        cloud = self.scenario.cloud
        node = self.scenario.nodes[self.local]
        if action.kind is ActionKind.INSTALL_COLD_START_GUARD:
            node.guard_boot_id = node.boot_id
            node.mode = DataPlaneMode.BLOCKED
        elif action.kind is ActionKind.ENTER_PASSIVE:
            node.mode = DataPlaneMode.PASSIVE
        elif action.kind is ActionKind.DISABLE_ACTIVE:
            node.mode = DataPlaneMode.BLOCKED
        elif action.kind is ActionKind.STOP_FORMER_OWNER:
            cloud.compute[action.target_node_id] = ComputeState.STOPPED
            self.scenario.nodes[action.target_node_id].mode = DataPlaneMode.BLOCKED
        elif action.kind is ActionKind.DETACH_FORMER_ATTACHMENT:
            cloud.attachment = None
            cloud.confirmed_owner = None
        elif action.kind is ActionKind.ATTACH_CANDIDATE:
            cloud.attachment = self.local
            cloud.confirmed_owner = None
            cloud.ownership_revision += 1
        elif action.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP:
            cloud.confirmed_owner = self.local
        elif action.kind is ActionKind.RECONCILE_ROUTES:
            snapshot = self.scenario.snapshot(self.local)
            node.routes = replace(
                snapshot.route_reconciliation_context,
                ownership_incarnation=action.ownership_incarnation,
                operation_id=action.operation_id,
            )
        elif action.kind is ActionKind.ENABLE_ACTIVE:
            node.mode = DataPlaneMode.ACTIVE
        else:  # pragma: no cover - every enum member is handled above
            raise AssertionError(f"unhandled action: {action.kind}")


class ScenarioSnapshots:
    def __init__(self, scenario: TwoNodeScenario, local: str) -> None:
        self.scenario = scenario
        self.local = local

    def observe(self) -> ControllerSnapshot:
        return self.scenario.snapshot(self.local)


class TwoNodeScenario:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.cloud = FakeCloud()
        self.nodes = {
            "node-a": FakeNode(
                "node-a",
                ConfiguredRole.ACTIVE,
                DataPlaneMode.ACTIVE,
                guard_boot_id="boot-node-a",
            ),
            "node-b": FakeNode(
                "node-b",
                ConfiguredRole.PASSIVE,
                DataPlaneMode.PASSIVE,
                guard_boot_id="boot-node-b",
            ),
        }
        self.trace: list[ActionKind] = []
        self.attempts: list[ActionKind] = []
        self.effects = {node_id: ScenarioEffects(self, node_id) for node_id in self.nodes}
        self.checkpoints = {
            node_id: MemoryCheckpoints(node) for node_id, node in self.nodes.items()
        }
        self.runtimes = {
            node_id: RecoverableController(
                policy=VMHAController(peer_timeout_seconds=10, suspicion_seconds=5),
                snapshots=ScenarioSnapshots(self, node_id),
                checkpoints=self.checkpoints[node_id],
                effects=self.effects[node_id],
            )
            for node_id in self.nodes
        }
        initial = self.snapshot("node-a")
        self.nodes["node-a"].routes = replace(
            initial.route_reconciliation_context,
            operation_id="initial-route-reconciliation",
        )

    def peer(self, local: str) -> str:
        return "node-b" if local == "node-a" else "node-a"

    def heartbeat(self, local: str) -> PeerHeartbeat:
        peer_id = self.peer(local)
        peer = self.nodes[peer_id]
        generation = self.nodes[local].peer_generation
        digests = (
            DIGESTS if generation == GENERATION else replace(DIGESTS, configuration=generation)
        )
        return PeerHeartbeat(
            cluster_id="cluster-a",
            node_id=peer_id,
            boot_id=peer.boot_id,
            sequence=1,
            sent_at="2026-08-13T00:00:00Z",
            configured_role=peer.role.value,
            observed_owner_id=self.cloud.attachment,
            generation_id=generation,
            digests=digests,
            service_healthy=True,
            route_ready=True,
            promotion_ready=True,
        )

    def snapshot(self, local: str) -> ControllerSnapshot:
        node = self.nodes[local]
        peer = self.peer(local)
        return ControllerSnapshot(
            now=self.clock.now,
            boot_id=node.boot_id,
            cluster_id="cluster-a",
            local_node_id=local,
            peer_node_id=peer,
            configured_role=node.role,
            local_generation_id=GENERATION,
            local_digests=DIGESTS,
            peer_heartbeat=self.heartbeat(local),
            peer_received_at=node.peer_received_at,
            apply_locked=False,
            emergency_active_only=False,
            manual_failback_requested=node.manual_failback,
            readiness=LocalReadiness(True, True, True, True),
            cloud=self.cloud.observe(local=local, peer=peer),
            guard_boot_id=node.guard_boot_id,
            data_plane_mode=node.mode,
            routes_reconciled_context=node.routes,
            route_runtime_id="route-runtime-a",
        )

    def run_until_active(self, local: str) -> None:
        for _ in range(40):
            try:
                decision = self.runtimes[local].step()
            except RuntimeError as exc:
                if not str(exc).startswith("crash-after-"):
                    raise
                continue
            if decision.action is None and decision.state is HAState.SUSPECT:
                self.clock.advance(5)
            if decision.action is None and decision.state is HAState.ACTIVE:
                return
        raise AssertionError("controller did not reach active state")


def test_healthy_nodes_keep_one_exact_owner_and_one_passive() -> None:
    scenario = TwoNodeScenario()

    active = scenario.runtimes["node-a"].step()
    passive = scenario.runtimes["node-b"].step()

    assert active.state is HAState.ACTIVE
    assert active.forwarding_enabled
    assert active.action is None
    assert passive.state is HAState.NORMAL
    assert not passive.forwarding_enabled
    assert passive.action is None
    assert passive.reasons == ("authoritative-owner-peer-is-healthy",)
    assert scenario.trace == []


def test_stale_passive_generation_blocks_before_fencing() -> None:
    scenario = TwoNodeScenario()
    passive = scenario.nodes["node-b"]
    passive.peer_received_at = 0
    passive.peer_generation = "d" * 64

    decision = scenario.runtimes["node-b"].step()

    assert decision.state is HAState.BLOCKED
    assert decision.action is None
    assert "generation-mismatch" in decision.reasons
    assert scenario.cloud.compute["node-a"] is ComputeState.RUNNING
    assert scenario.trace == []


def test_cloud_api_outage_fails_closed_and_active_owner_disables() -> None:
    scenario = TwoNodeScenario()
    scenario.cloud.authoritative = False

    passive = scenario.runtimes["node-b"].step()
    active = scenario.runtimes["node-a"].step()

    assert passive.state is HAState.BLOCKED
    assert passive.action is None
    assert passive.reasons == ("cloud-ownership-unavailable",)
    assert active.action is not None
    assert active.action.kind is ActionKind.DISABLE_ACTIVE
    assert scenario.trace == [ActionKind.DISABLE_ACTIVE]


def test_fencing_failure_never_reaches_allocation_routes_or_forwarding() -> None:
    scenario = TwoNodeScenario()
    scenario.nodes["node-b"].peer_received_at = 0
    scenario.effects["node-b"].fail_before = ActionKind.STOP_FORMER_OWNER

    suspect = scenario.runtimes["node-b"].step()
    assert suspect.state is HAState.SUSPECT
    scenario.clock.advance(5)

    with pytest.raises(RuntimeError, match="injected-stop-former-owner-failure"):
        scenario.runtimes["node-b"].step()

    assert scenario.cloud.compute["node-a"] is ComputeState.RUNNING
    assert scenario.cloud.attachment == "node-a"
    assert scenario.trace == []
    assert scenario.attempts == [ActionKind.STOP_FORMER_OWNER]


@pytest.mark.parametrize("crash_after", TAKEOVER_EFFECTS)
def test_takeover_crash_after_every_side_effect_replays_exactly_once(
    crash_after: ActionKind,
) -> None:
    scenario = TwoNodeScenario()
    scenario.nodes["node-b"].peer_received_at = 0
    scenario.effects["node-b"].crash_after = crash_after

    scenario.run_until_active("node-b")

    assert scenario.trace == list(TAKEOVER_EFFECTS)
    assert scenario.trace.index(ActionKind.RECONCILE_ROUTES) > scenario.trace.index(
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP
    )
    assert scenario.trace[-1] is ActionKind.ENABLE_ACTIVE
    assert scenario.effects["node-b"].crashed_operation_ids
    assert scenario.cloud.compute["node-a"] is ComputeState.STOPPED
    assert scenario.cloud.attachment == "node-b"
    assert scenario.cloud.confirmed_owner == "node-b"
    assert scenario.nodes["node-b"].mode is DataPlaneMode.ACTIVE
    assert scenario.nodes["node-a"].mode is DataPlaneMode.BLOCKED


def test_checkpoint_filesystem_failure_happens_before_fencing_effect() -> None:
    scenario = TwoNodeScenario()
    scenario.nodes["node-b"].peer_received_at = 0
    suspect = scenario.runtimes["node-b"].step()
    assert suspect.state is HAState.SUSPECT
    scenario.clock.advance(5)
    scenario.checkpoints["node-b"].fail_next_save = True

    with pytest.raises(OSError, match="injected checkpoint failure"):
        scenario.runtimes["node-b"].step()

    assert scenario.trace == []
    assert scenario.cloud.compute["node-a"] is ComputeState.RUNNING

    scenario.runtimes["node-b"].step()
    assert scenario.trace == [ActionKind.STOP_FORMER_OWNER]


def test_takeover_resynchronization_and_manual_failback_restore_configured_owner() -> None:
    scenario = TwoNodeScenario()
    scenario.nodes["node-b"].peer_received_at = 0
    scenario.run_until_active("node-b")

    scenario.cloud.compute["node-a"] = ComputeState.RUNNING
    scenario.nodes["node-a"].mode = DataPlaneMode.PASSIVE
    scenario.nodes["node-a"].peer_received_at = scenario.clock.now
    scenario.trace.clear()
    waiting = scenario.runtimes["node-a"].step()
    assert waiting.action is None
    assert waiting.reasons == ("manual-failback-required",)

    scenario.nodes["node-a"].manual_failback = True
    scenario.run_until_active("node-a")

    assert scenario.trace == list(TAKEOVER_EFFECTS)
    assert scenario.cloud.compute["node-b"] is ComputeState.STOPPED
    assert scenario.cloud.attachment == "node-a"
    assert scenario.cloud.confirmed_owner == "node-a"
    assert scenario.nodes["node-a"].mode is DataPlaneMode.ACTIVE
    assert scenario.nodes["node-b"].mode is DataPlaneMode.BLOCKED


def test_takeover_hold_down_preserves_routes_until_stable_absence() -> None:
    reconciler = _reconciler(hold_down=60, observations=2)
    existing = (_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),)
    during_hold = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=RouteTransitionState(takeover_started_at=100),
        now=120,
    )
    first_stable = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=during_hold.next_state,
        now=161,
    )
    second_stable = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=first_stable.next_state,
        now=162,
    )

    assert during_hold.mutations == ()
    assert during_hold.held_bgp_prefixes == {"10.20.0.0/16"}
    assert first_stable.mutations == ()
    assert [(item.kind, item.prefix) for item in second_stable.mutations] == [
        (RouteMutationKind.DELETE, "10.20.0.0/16")
    ]
