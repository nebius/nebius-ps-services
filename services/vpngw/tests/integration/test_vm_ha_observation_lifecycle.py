"""Sustained production HA composition with simulated OS/cloud/TLS boundaries.

Exercise real sampling, readiness, heartbeat validation/replay, two controllers,
checkpoint/status persistence and static route repair. No packet or BGP claim.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from tests.integration.test_vm_ha_static_path_failover import connection_routes
from tests.unit.test_vm_ha_agent_runtime import _passive_runtime_config, _runtime_config
from tests.unit.test_vm_ha_static_selection import PREFIX, Kernel, action, config, runtime

from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHACloudObservationUnavailable,
    VMHASnapshotProviders,
    _typed_transfer_intent_provider,
    build_vm_ha_controller_runtime,
)
from nebius_vpngw.agent.vm_ha import AtomicGenerationStore, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.runtime import (
    BoundPeerRuntime,
    CommandResult,
    LocalObservationUnavailable,
    VMHARuntimePorts,
)
from nebius_vpngw.agent.vm_ha.transport import (
    AuthenticatedPeerMessage,
    PeerStateExchange,
    PeerTransportError,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
)
from nebius_vpngw.deploy.vm_ha_cloud import RetryableHACloudError
from nebius_vpngw.schema import VMHARuntimeBinding

pytestmark = pytest.mark.integration
OTHER_PREFIX = "192.168.0.0/24"


class MemoryTransport:
    """Replace TLS delivery, retaining actual exchange validation and replay state."""

    def __init__(self, cluster, node_id):
        self.cluster = cluster
        self.node_id = node_id
        self.inbox = queue.Queue()
        self.sent = []

    def send(self, heartbeat):
        decoded = PeerHeartbeat.from_mapping(json.loads(json.dumps(heartbeat.to_dict())))
        self.sent.append(decoded)
        peer = self.cluster.nodes[self.cluster.peer(self.node_id)]
        peer.transport.inbox.put(
            AuthenticatedPeerMessage(
                heartbeat=decoded,
                authenticated_node_id=decoded.node_id,
                authenticated_certificate_fingerprint=decoded.certificate_fingerprint,
                authenticated_mtls_epoch=decoded.mtls_epoch,
            )
        )

    def receive(self, *, timeout_seconds):
        try:
            return self.inbox.get(timeout=min(timeout_seconds, 0.02))
        except queue.Empty:
            raise PeerTransportError("simulated receive timeout") from None


class FaultNode:
    def __init__(self, cluster, node_id, state_dir):
        self.cluster = cluster
        self.node_id = node_id
        self.state_dir = state_dir
        state_dir.mkdir()
        self.boot_id = f"boot-{node_id}"
        self.cloud_unavailable = False
        self.receipt_failure = None
        self.local_unknown = False
        self.cloud_entered = threading.Event()
        self.cloud_release = threading.Event()
        self.cloud_release.set()
        self.sampler_entered = threading.Event()
        self.sampler_release = threading.Event()
        self.sampler_release.set()
        self.actions = []
        self.statuses = []
        self.route_context = None
        cfg = config(mixed=True)
        cfg["connections"][0].update(routing_mode="static", remote_prefixes=[OTHER_PREFIX])
        self.kernel = Kernel()
        self.kernel.forwarding = "1" if node_id == "node-a" else "0"
        self.kernel.usable = {"xfrm0", "xfrm1"} if node_id == "node-a" else set()
        self.kernel.routes = {OTHER_PREFIX: "xfrm0", PREFIX: "xfrm1"}
        self.data_plane = runtime(state_dir, self.kernel, cfg)
        self.data_plane.monotonic_clock = cluster.clock
        self.data_plane.runner = self.run_command
        mode = DataPlaneMode.ACTIVE if node_id == "node-a" else DataPlaneMode.PASSIVE
        (state_dir / "guard.json").write_text(
            json.dumps(
                {
                    "guard_boot_id": self.boot_id,
                    "data_plane_mode": mode.value,
                }
            )
        )
        self.data_plane._write_state(replace(action(), boot_id=self.boot_id), mode)
        self.routes = connection_routes(self.data_plane)
        self.transport = MemoryTransport(cluster, node_id)
        exchange = PeerStateExchange(
            self.transport,
            cluster_id="cluster-a",
            peer_node_id=cluster.peer(node_id),
            replay_store=AtomicGenerationStore(state_dir / "replay"),
            max_heartbeat_age_seconds=10.0,
            max_clock_skew_seconds=0.0,
            wall_clock=cluster.clock,
            monotonic_clock=cluster.clock,
        )
        peer_runtime = BoundPeerRuntime(
            exchange, clock=cluster.clock, peer_node_id=cluster.peer(node_id)
        )
        cfg = _runtime_config() if node_id == "node-a" else _passive_runtime_config()
        binding = VMHARuntimeBinding.model_validate(cfg["runtime_binding"])
        local = next(node for node in binding.nodes if node.node_id == node_id)
        peer = next(node for node in binding.nodes if node.node_id != node_id)
        self.ports = VMHARuntimePorts(
            binding=binding,
            local=local,
            peer=peer,
            sdk=SimpleNamespace(close=lambda: None),
            cloud=SimpleNamespace(observe=self.observe_cloud),
            data_plane=self.data_plane,
            routes=self.routes,
            peer_runtime=peer_runtime,
            credential_bundle=None,
            mtls_snapshot_provider=lambda: SimpleNamespace(
                epoch=1, certificate_fingerprint="d" * 64
            ),
            auto_healing_policy_provider=lambda: ("enabled", "e" * 64),
            monotonic_clock=cluster.clock,
        )
        config_path = state_dir / "config.yaml"
        config_path.write_text(yaml.safe_dump({"vm_ha": cfg}))
        handlers = {}
        for kind in ActionKind:
            handlers[kind] = self.effect
        self.controller = build_vm_ha_controller_runtime(
            config_path=config_path,
            providers=VMHASnapshotProviders(
                peer=peer_runtime.observe,
                readiness=self.routes.readiness,
                cloud=self.ports.observe_cloud,
                data_plane=self.data_plane.mode,
                routes=self.observe_receipt,
                transfer_intent=_typed_transfer_intent_provider(state_dir=state_dir, config=cfg),
            ),
            handlers=handlers,
            state_dir=state_dir,
            clock=cluster.clock,
            boot_id=lambda: self.boot_id,
        )
        self.controller.snapshots.observation_clock = cluster.clock
        self.controller.guard = self.install_guard
        self.service = DefaultVMHAControllerRuntime(
            self.controller,
            self.ports,
            boot_id=self.boot_id,
            sequence_path=state_dir / "sequence.json",
            guard_path=state_dir / "guard.json",
            config=cfg,
            transfer_lineage_path=state_dir / "transfer-lineage.json",
            promotion_receipt_path=state_dir / "promotion-receipt.json",
            clock=cluster.clock,
            sleeper=lambda _seconds: None,
            peer_retry_seconds=0.01,
        )
        # Begin with an already-running guarded pair, not deployment/bootstrap.
        self.service.started_guarded = True
        self.service.active_reconcile_requested = node_id == "node-a"
        if not cluster.real_time:
            self.service._start_peer_listener = lambda: None
            self.service._start_heartbeat_workers = lambda: None
        observed = self.controller.snapshots.observe()
        if node_id == "node-a":
            self.route_context = replace(
                observed.route_reconciliation_context, operation_id="baseline-routes"
            )
            self.controller.controller.checkpoints.save(
                ControllerCheckpoint(
                    state=HAState.ACTIVE,
                    established_ownership_context=observed.ownership_context,
                )
            )

    def install_guard(self):
        self.kernel.forwarding = "0"
        self.actions.append(ActionKind.INSTALL_COLD_START_GUARD)
        self.data_plane._write_state(action(), DataPlaneMode.BLOCKED)
        return {}

    def run_command(self, arguments, timeout):
        if threading.current_thread().name == "nebius-vpngw-vm-ha-local-health":
            self.sampler_entered.set()
            if not self.sampler_release.wait(30.0):
                raise TimeoutError("test sampler gate timed out")
        if arguments[0].endswith("vtysh") and self.local_unknown:
            raise TimeoutError("simulated local read timeout")
        if arguments[0].endswith("swanctl"):
            self.kernel.calls.append(tuple(arguments))
            return CommandResult(0, "list-sas reply {}")
        return self.kernel.run(arguments, timeout)

    def observe_cloud(self):
        self.cloud_entered.set()
        if not self.cloud_release.wait(30.0):
            raise RetryableHACloudError("test cloud gate timed out")
        if self.cloud_unavailable:
            raise RetryableHACloudError("simulated cloud read timeout")
        peer_id = self.cluster.peer(self.node_id)
        return CloudObservation(
            authoritative=True,
            allocation_id="allocation-a",
            observed_owner_node_id=self.cluster.owner,
            former_owner_node_id=peer_id,
            former_owner_compute_state=self.cluster.compute[peer_id],
            former_attachment_absent=self.cluster.owner != peer_id,
            candidate_attachment_exact=self.cluster.owner == self.node_id,
            ownership_re_read_exact=self.cluster.owner == self.node_id and self.cluster.confirmed,
            ownership_epoch=str(self.cluster.revision),
            former_attachment_exact=self.cluster.owner == peer_id,
            candidate_attachment_absent=self.cluster.owner != self.node_id,
        )

    def observe_receipt(self):
        if self.receipt_failure is not None:
            raise self.receipt_failure
        return self.route_context

    def effect(self, effect):
        self.actions.append(effect.kind)
        if effect.kind is ActionKind.REPAIR_LOCAL_DATAPLANE:
            self.data_plane.repair_local(effect)
        elif effect.kind in {ActionKind.DISABLE_ACTIVE, ActionKind.INSTALL_COLD_START_GUARD}:
            self.kernel.forwarding = "0"
            self.data_plane._write_state(effect, DataPlaneMode.BLOCKED)
        elif effect.kind is ActionKind.ENTER_PASSIVE:
            self.kernel.forwarding = "0"
            self.data_plane._write_state(effect, DataPlaneMode.PASSIVE)
        elif effect.kind is ActionKind.STOP_FORMER_OWNER:
            self.cluster.compute[effect.target_node_id] = ComputeState.STOPPED
            self.cluster.nodes[effect.target_node_id].kernel.forwarding = "0"
        elif effect.kind is ActionKind.DETACH_FORMER_ATTACHMENT:
            assert self.cluster.compute[effect.target_node_id] is ComputeState.STOPPED
            self.cluster.owner = None
            self.cluster.confirmed = False
            self.cluster.revision += 1
        elif effect.kind is ActionKind.ATTACH_CANDIDATE:
            assert self.cluster.owner is None
            self.cluster.owner = self.node_id
            self.cluster.revision += 1
        elif effect.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP:
            self.cluster.confirmed = True
        elif effect.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE:
            assert self.cluster.owner == self.node_id and self.cluster.confirmed
            assert self.cluster.compute[self.cluster.peer(self.node_id)] is ComputeState.STOPPED
            self.kernel.usable = {"xfrm0", "xfrm1"}
            self.kernel.routes = {OTHER_PREFIX: "xfrm0", PREFIX: "xfrm1"}
        elif effect.kind is ActionKind.RECONCILE_ROUTES:
            assert self.cluster.owner == self.node_id and self.cluster.confirmed
            self.route_context = replace(
                self.controller.snapshots.last_snapshot.route_reconciliation_context,
                operation_id=effect.operation_id,
                ownership_incarnation=effect.ownership_incarnation,
            )
        elif effect.kind is ActionKind.ENABLE_ACTIVE:
            assert self.cluster.owner == self.node_id and self.cluster.confirmed
            assert self.cluster.compute[self.cluster.peer(self.node_id)] is ComputeState.STOPPED
            self.kernel.forwarding = "1"
            self.data_plane._write_state(effect, DataPlaneMode.ACTIVE)
        else:
            raise AssertionError(f"Unexpected mutation: {effect.kind}")

    def step(self):
        try:
            status = self.service.step()
        except VMHACloudObservationUnavailable:
            return None
        self.statuses.append(status)
        return status


class FaultCluster:
    def __init__(self, root, *, real_time=False):
        self.now = 100.0
        self.origin = time.monotonic()
        self.real_time = real_time
        self.owner = "node-a"
        self.revision = 42
        self.confirmed = True
        self.compute = {"node-a": ComputeState.RUNNING, "node-b": ComputeState.RUNNING}
        self.nodes = {}
        for node_id in self.compute:
            self.nodes[node_id] = FaultNode(self, node_id, root / node_id)
        self.tick()
        self.assert_undisrupted()

    def clock(self):
        if self.real_time:
            return 100.0 + time.monotonic() - self.origin
        return self.now

    @staticmethod
    def peer(node_id):
        return "node-b" if node_id == "node-a" else "node-a"

    def tick(self, *, publish=True):
        if publish:
            for node in self.nodes.values():
                if self.compute[node.node_id] is not ComputeState.RUNNING:
                    continue
                node.ports.sample_local_health(cancelled=lambda: False)
                node.service._exchange_peer_state()
            for node in self.nodes.values():
                if self.compute[node.node_id] is ComputeState.RUNNING:
                    try:
                        node.ports.peer_runtime.poll(timeout_seconds=0.1)
                    except PeerTransportError:
                        pass
        for node in self.nodes.values():
            if self.compute[node.node_id] is ComputeState.RUNNING:
                node.step()
        self.now += 1.0

    def assert_undisrupted(self):
        assert self.owner == "node-a"
        assert self.compute == {"node-a": ComputeState.RUNNING, "node-b": ComputeState.RUNNING}
        assert self.nodes["node-a"].kernel.forwarding == "1"
        assert self.nodes["node-b"].kernel.forwarding == "0"
        for node in self.nodes.values():
            assert not set(node.actions) - {ActionKind.REPAIR_LOCAL_DATAPLANE}, node.actions
            assert node.service.worker_error is None
            assert node.service.listener_error is None
        for command in self.nodes["node-a"].kernel.calls:
            assert "restart" not in command
            assert "reload" not in command

    def close(self):
        for node in self.nodes.values():
            node.cloud_release.set()
            node.sampler_release.set()
        for node in self.nodes.values():
            node.service.close(restore_guard=False)


@pytest.fixture
def cluster(tmp_path):
    pair = FaultCluster(tmp_path)
    try:
        yield pair
    finally:
        pair.close()


@pytest.mark.parametrize("affected", [("node-a",), ("node-b",), ("node-a", "node-b")])
@pytest.mark.parametrize("boundary", ["cloud", "route-receipt"])
def test_sustained_cloud_timeouts_never_become_gateway_failure(cluster, affected, boundary):
    for elapsed in range(180):
        for node_id in affected:
            node = cluster.nodes[node_id]
            if boundary == "cloud":
                node.cloud_unavailable = elapsed < 120
            else:
                node.receipt_failure = (
                    RetryableHACloudError("route observation timeout") if elapsed < 120 else None
                )
        cluster.tick()
        cluster.assert_undisrupted()
    assert len(cluster.nodes["node-a"].transport.sent) >= 180
    assert cluster.nodes["node-b"].controller.controller.checkpoints.load().suspect_since is None


@pytest.mark.parametrize("boundary", ["local-command", "local-receipt"])
def test_repeated_short_unknown_observations_recover_without_takeover(cluster, boundary):
    for elapsed in range(180):
        owner = cluster.nodes["node-a"]
        unknown = elapsed % 20 < 6
        owner.local_unknown = unknown and boundary == "local-command"
        owner.receipt_failure = (
            LocalObservationUnavailable("local route query")
            if unknown and boundary == "local-receipt"
            else None
        )
        cluster.tick()
        cluster.assert_undisrupted()
    assert any(
        status["observation_health"]["local"]["state"] == "grace" for status in owner.statuses
    )
    assert owner.controller.snapshots.local_observation_grace.consecutive_failures == 0
    if boundary == "local-command":
        assert any(frame.local_forwarding_ready is None for frame in owner.transport.sent)
        standby = cluster.nodes["node-b"]
        assert any(
            status["observation_health"]["peer_local"]["state"] == "grace"
            for status in standby.statuses
        )


def test_multi_connection_loss_and_repeated_neighbor_switches_preserve_gateway(cluster):
    owner = cluster.nodes["node-a"]
    for elapsed in range(180):
        if elapsed < 60:
            owner.kernel.usable = {"xfrm0"}
        elif elapsed < 120:
            owner.kernel.usable = {"xfrm0", "xfrm2"}
        else:
            device = "xfrm1" if (elapsed // 10) % 2 else "xfrm2"
            owner.kernel.usable = {"xfrm0", device}
        cluster.tick()
        cluster.assert_undisrupted()
        assert owner.kernel.routes[OTHER_PREFIX] == "xfrm0"
    assert ActionKind.REPAIR_LOCAL_DATAPLANE in owner.actions
    assert any(status["state"] == "degraded-path" for status in owner.statuses)
    assert owner.kernel.routes[PREFIX] in owner.kernel.usable


def test_prolonged_local_unknown_eventually_fences_instead_of_hiding_failure(cluster):
    owner = cluster.nodes["node-a"]
    owner.local_unknown = True
    for elapsed in range(12):
        cluster.tick()
        if elapsed < 10:
            cluster.assert_undisrupted()
    assert owner.actions[0] is ActionKind.DISABLE_ACTIVE
    assert owner.actions.count(ActionKind.DISABLE_ACTIVE) == 1
    assert owner.kernel.forwarding == "0"


def test_lost_heartbeat_enters_suspicion_then_requests_fencing(cluster):
    standby = cluster.nodes["node-b"]
    for elapsed in range(17):
        cluster.tick(publish=False)
        if elapsed < 9:
            assert not standby.actions
        if standby.actions:
            break
    assert standby.actions == [ActionKind.STOP_FORMER_OWNER]
    assert cluster.compute["node-a"] is ComputeState.STOPPED
    assert standby.kernel.forwarding == "0"


def test_cloud_outage_then_local_unknown_gets_a_fresh_local_grace(cluster):
    owner = cluster.nodes["node-a"]
    for elapsed in range(90):
        owner.cloud_unavailable = elapsed < 60
        owner.local_unknown = 60 <= elapsed < 66
        cluster.tick()
        cluster.assert_undisrupted()
    grace_statuses = []
    for status in owner.statuses:
        if status["observation_health"]["local"]["state"] == "grace":
            grace_statuses.append(status)
    assert grace_statuses
    assert grace_statuses[0]["observation_health"]["local"]["failure_age_seconds"] == 0.0


@pytest.mark.parametrize(("stage", "duration"), [("cloud", 16.0), ("sampler", 6.0)])
def test_real_heartbeat_workers_protect_peer_during_blocked_observation(tmp_path, stage, duration):
    """Use production one-second worker cadence and exceed the peer timeout.

    A blocked cloud read must outlive timeout plus suspicion without takeover.
    A blocked local sample is shorter than health expiry and must recover.
    """
    pair = FaultCluster(tmp_path, real_time=True)
    owner = pair.nodes["node-a"]
    stop = threading.Event()
    failures = queue.Queue()
    drivers = []

    def drive(node):
        try:
            while not stop.is_set():
                try:
                    status = node.service.step()
                    node.statuses.append(status)
                except VMHACloudObservationUnavailable:
                    pass
                stop.wait(0.2)
        except BaseException as error:
            failures.put(error)
            stop.set()

    try:
        for node in pair.nodes.values():
            driver = threading.Thread(target=drive, args=(node,), name=f"controller-{node.node_id}")
            drivers.append(driver)
            driver.start()
        deadline = time.monotonic() + 5.0
        while min(len(node.transport.sent) for node in pair.nodes.values()) < 3:
            assert time.monotonic() < deadline, "Workers failed to establish steady publication"
            assert failures.empty(), list(failures.queue)
            stop.wait(0.05)

        gate = owner.cloud_release if stage == "cloud" else owner.sampler_release
        entered = owner.cloud_entered if stage == "cloud" else owner.sampler_entered
        gate.clear()
        entered.clear()
        assert entered.wait(3.0), "Fault never reached the intended production observer"
        before = len(owner.transport.sent)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            pair.assert_undisrupted()
            assert failures.empty(), list(failures.queue)
            stop.wait(0.1)
        blocked_frames = owner.transport.sent[before:]
        assert len(blocked_frames) >= int(duration) - 2
        if stage == "cloud":
            assert all(frame.local_forwarding_ready is True for frame in blocked_frames)
            assert (
                max(frame.ownership_observation_age_seconds for frame in blocked_frames)
                >= duration - 2
            )
            assert max(frame.health_sample_age_seconds for frame in blocked_frames) < 5.0
        else:
            assert max(frame.health_sample_age_seconds for frame in blocked_frames) >= 4.0
        assert not gate.is_set()
        gate.set()

        # Observe several subsequent controller turns, not merely thread liveness.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            pair.assert_undisrupted()
            assert failures.empty(), list(failures.queue)
            stop.wait(0.1)
        assert pair.nodes["node-b"].controller.controller.checkpoints.load().suspect_since is None
        assert all(node.service.publisher_thread.is_alive() for node in pair.nodes.values())
    finally:
        stop.set()
        for node in pair.nodes.values():
            node.cloud_release.set()
            node.sampler_release.set()
        for driver in drivers:
            driver.join(5.0)
        pair.close()
    assert all(not driver.is_alive() for driver in drivers)
    assert failures.empty(), list(failures.queue)


def test_real_peer_loss_completes_takeover_and_stays_active(cluster):
    standby = cluster.nodes["node-b"]
    # Stop delivery from the owner, but keep its cloud state Running until the
    # candidate performs fencing. This models controller/heartbeat failure.
    cluster.nodes["node-a"].transport.send = lambda _heartbeat: None
    for _turn in range(50):
        cluster.tick()
        assert not (
            cluster.nodes["node-a"].kernel.forwarding == "1" and standby.kernel.forwarding == "1"
        )
        if standby.statuses and standby.statuses[-1]["promotion_committed"]:
            break
    else:
        pytest.fail(f"Takeover failed to commit: {standby.statuses[-1]}")
    assert cluster.owner == "node-b"
    assert cluster.compute["node-a"] is ComputeState.STOPPED
    assert standby.kernel.forwarding == "1"
    expected = [
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ]
    assert standby.actions == expected
    for _turn in range(30):
        cluster.tick()
        assert cluster.owner == "node-b"
        assert standby.kernel.forwarding == "1"
        assert standby.actions == expected


def test_short_loss_of_all_paths_repairs_before_peer_takeover(cluster):
    owner = cluster.nodes["node-a"]
    for elapsed in range(60):
        owner.kernel.usable = set() if 20 <= elapsed < 22 else {"xfrm0", "xfrm1"}
        cluster.tick()
        cluster.assert_undisrupted()
    assert ActionKind.REPAIR_LOCAL_DATAPLANE in owner.actions
    assert owner.statuses[-1]["forwarding_continuity"] is True
    assert cluster.nodes["node-b"].controller.controller.checkpoints.load().suspect_since is None


def test_persistent_loss_of_all_paths_eventually_requests_gateway_takeover(cluster):
    owner = cluster.nodes["node-a"]
    standby = cluster.nodes["node-b"]
    owner.kernel.usable.clear()
    for _turn in range(30):
        cluster.tick()
        if ActionKind.STOP_FORMER_OWNER in standby.actions:
            break
    else:
        pytest.fail("Persistent path failure never caused peer fencing")
    assert owner.actions[0] is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert ActionKind.DISABLE_ACTIVE in owner.actions
    assert owner.kernel.forwarding == "0"
    assert standby.kernel.forwarding == "0"


def test_cloud_outage_overlapping_partial_connection_loss_and_unknown_health(cluster):
    owner = cluster.nodes["node-a"]
    for elapsed in range(90):
        owner.cloud_unavailable = elapsed < 60
        owner.kernel.usable = {"xfrm0"}
        owner.local_unknown = 60 <= elapsed < 66
        cluster.tick()
        cluster.assert_undisrupted()
        assert owner.kernel.routes[OTHER_PREFIX] == "xfrm0"
    assert any(
        frame.local_forwarding_ready is True and frame.transfer_ready is False
        for frame in owner.transport.sent
    )
    assert owner.statuses[-1]["state"] == "degraded-path"
    assert cluster.nodes["node-b"].controller.controller.checkpoints.load().suspect_since is None
