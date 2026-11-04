from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import _cloud, _peer, _snapshot

from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
    VMHAWorkerFailure,
    _run_vm_ha_controller,
)
from nebius_vpngw.agent.vm_ha.heartbeat import HealthSample, HeartbeatSnapshots
from nebius_vpngw.agent.vm_ha.models import PeerHeartbeat, ReplayState, StateValidationError
from nebius_vpngw.agent.vm_ha.runtime import (
    BoundPeerRuntime,
    CommandResult,
    LocalDataPlaneObserver,
    SystemDataPlaneRuntime,
)
from nebius_vpngw.agent.vm_ha.store import AtomicGenerationStore, CorruptStateError, _envelope
from nebius_vpngw.agent.vm_ha.transport import (
    AuthenticatedPeerMessage,
    PeerStateExchange,
    PeerTransportError,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    VMHAController,
)
from nebius_vpngw.cli import _vm_ha_independent_heartbeat_matches


@pytest.mark.parametrize("value", [[], {}, 1, True, "invalid"])
def test_invalid_mode_is_advisory_validation_error(value: object) -> None:
    frame = _peer().to_dict()
    frame["data_plane_mode"] = value
    with pytest.raises(StateValidationError):
        PeerHeartbeat.from_mapping(frame)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "1"])
def test_invalid_age_is_rejected(value: object) -> None:
    frame = _peer().to_dict()
    frame["health_sample_age_seconds"] = value
    with pytest.raises(StateValidationError):
        PeerHeartbeat.from_mapping(frame)


def test_v3_has_no_fallback_reader() -> None:
    frame = _peer().to_dict()
    frame["schema"] = "nebius-vpngw/vm-ha-heartbeat-v3"
    with pytest.raises(StateValidationError):
        PeerHeartbeat.from_mapping(frame)


def test_mutation_invalidates_cached_and_overlapping_health() -> None:
    snapshots = HeartbeatSnapshots()
    sample = HealthSample(1.0, True, True, True, "active")
    snapshots.publish_health(sample, 0)
    snapshots.mutation()
    assert snapshots.read()[0] is None
    snapshots.publish_health(sample, 0)
    observed, _ = snapshots.read()
    assert observed is not None
    assert observed.local_forwarding_ready is None
    assert observed.data_plane_mode is None
    snapshots.mutation(completed=True)
    snapshots.publish_health(sample, 2)
    assert snapshots.read()[0] == sample


@pytest.mark.parametrize("age", [None, 10.0, 100.0])
def test_unknown_health_does_not_restart_suspicion(age: float | None) -> None:
    controller = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    peer = replace(_peer(), health_sample_age_seconds=age)
    first = controller.decide(
        _snapshot(now=100.0, peer_heartbeat=peer, peer_received_at=100.0),
        ControllerCheckpoint(),
    )
    second = controller.decide(
        _snapshot(now=104.0, peer_heartbeat=peer, peer_received_at=104.0), first.checkpoint
    )
    assert first.state is HAState.SUSPECT
    assert second.checkpoint.suspect_since == 100.0
    third = controller.decide(
        _snapshot(now=105.0, peer_heartbeat=peer, peer_received_at=105.0), second.checkpoint
    )
    assert third.state is HAState.FENCING


def test_one_deadline_caps_all_local_commands(tmp_path: Path) -> None:
    clock = [0.0]
    budgets: list[float] = []

    def runner(argv: tuple[str, ...], timeout: float) -> CommandResult:
        budgets.append(timeout)
        clock[0] += 1.0
        if "show bgp" in argv[-1]:
            return CommandResult(0, "{}")
        return CommandResult(0, "[]")

    source = SystemDataPlaneRuntime(
        state_path=tmp_path / "mode.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="a" * 64,
        runner=runner,
        monotonic_clock=lambda: clock[0],
    )
    observer = LocalDataPlaneObserver(source, deadline=5.0, cancelled=lambda: False)
    with pytest.raises(RuntimeError, match="deadline"):
        observer.observe()
    assert budgets == [5.0, 4.0, 3.0, 2.0, 1.0]
    assert clock[0] == 5.0


@pytest.mark.parametrize("blocked_stage", ["controller", "sampler"])
def test_publication_continues_while_other_work_blocks(tmp_path: Path, blocked_stage: str) -> None:
    blocked = threading.Event()
    release = threading.Event()
    published_twice = threading.Event()
    frames: list[object] = []

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            release.wait(0.01)
            raise PeerTransportError("no inbound fixture")

        def send(self, heartbeat: object) -> None:
            frames.append(heartbeat)
            if len(frames) >= 2:
                published_twice.set()

    class Ports:
        heartbeat_snapshots = HeartbeatSnapshots()
        peer_runtime = Peer()
        local = SimpleNamespace(role=SimpleNamespace(value="passive"))

        def sample_local_health(self, *, cancelled: object) -> None:
            if blocked_stage == "sampler":
                blocked.set()
                assert release.wait(5.0)

        def heartbeat(self, **kwargs: object) -> object:
            return kwargs["sequence"]

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            if blocked_stage == "controller":
                blocked.set()
                assert release.wait(5.0)
            return {}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
    )
    errors: list[BaseException] = []

    def drive() -> None:
        try:
            runtime.step()
        except BaseException as error:
            errors.append(error)

    driver = threading.Thread(target=drive)
    driver.start()
    try:
        assert blocked.wait(2.0)
        assert published_twice.wait(3.0)
        assert not release.is_set()
    finally:
        release.set()
        driver.join(6.0)
        runtime.close()
    assert not errors
    assert not driver.is_alive()
    assert runtime.publisher_thread is not None
    assert not runtime.publisher_thread.is_alive()
    assert json.loads((tmp_path / "sequence.json").read_text())["next_sequence"] >= 2


def test_receipt_persistence_delay_does_not_refresh_health() -> None:

    clock = [50.0]
    heartbeat = replace(_peer(), sent_at="1970-01-01T00:16:40Z", health_sample_age_seconds=1.0)

    class Transport:
        def receive(self, *, timeout_seconds: float) -> AuthenticatedPeerMessage:
            return AuthenticatedPeerMessage(
                heartbeat,
                heartbeat.node_id,
                heartbeat.certificate_fingerprint,
                heartbeat.mtls_epoch,
            )

        def send(self, value: object) -> None:
            return None

    class Store:
        def load_replay_state(self, peer_node_id: str) -> ReplayState | None:
            return None

        def save_replay_state(self, peer_node_id: str, replay: ReplayState) -> None:
            clock[0] = 54.0

    exchange = PeerStateExchange(
        Transport(),
        cluster_id=heartbeat.cluster_id,
        peer_node_id=heartbeat.node_id,
        replay_store=Store(),
        wall_clock=lambda: 1002.0,
        monotonic_clock=lambda: clock[0],
    )
    runtime = BoundPeerRuntime(exchange, clock=lambda: clock[0], peer_node_id=heartbeat.node_id)
    runtime.poll(timeout_seconds=2.0)
    received, received_at = runtime.observe()
    assert received is not None
    assert received_at == 50.0
    assert received.health_sample_age_seconds == 8.0
    controller = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    assert not controller._peer_fresh(
        _snapshot(now=54.0, peer_heartbeat=received, peer_received_at=received_at)
    )


def test_upgrade_cache_reset_retains_replay_protection(tmp_path: Path) -> None:

    store = AtomicGenerationStore(tmp_path)
    heartbeat = _peer(node_id="node-b")
    replay = ReplayState(heartbeat.boot_id, heartbeat.sequence)
    store.save_replay_state("node-b", replay)
    store.save_accepted_peer_heartbeat("node-b", heartbeat)
    path = store._accepted_peer_heartbeat_path("node-b")
    legacy = heartbeat.to_dict()
    legacy["schema"] = "nebius-vpngw/vm-ha-heartbeat-v3"
    path.write_text(json.dumps(_envelope(legacy)))
    with pytest.raises(CorruptStateError):
        store.load_accepted_peer_heartbeat("node-b")
    store.request_accepted_peer_heartbeat_reset("node-b", generation_id=heartbeat.generation_id)
    assert store.consume_accepted_peer_heartbeat_reset(
        "node-b", generation_id=heartbeat.generation_id
    )
    assert store.load_accepted_peer_heartbeat("node-b") is None
    assert store.load_replay_state("node-b") == replay


def test_snapshot_reads_peer_and_clock_after_every_blocking_provider(tmp_path: Path) -> None:
    clock = [100.0]
    order: list[str] = []
    latest = [_peer(node_id="node-b")]

    def advance(stage: str) -> None:
        order.append(stage)
        clock[0] += 12.0
        latest[0] = replace(latest[0], sequence=latest[0].sequence + 1)

    def cloud():
        advance("cloud")
        return replace(_cloud(owner="node-b"), allocation_id="allocation-a")

    def readiness():
        advance("readiness")
        return LocalReadiness(True, True, True, True)

    def routes():
        advance("routes")
        return None

    def peer():
        order.append("peer")
        return latest[0], clock[0] - 1.0

    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=peer,
            cloud=cloud,
            readiness=readiness,
            routes=routes,
            data_plane=lambda: DataPlaneMode.PASSIVE,
        ),
        state_dir=tmp_path,
        clock=lambda: clock[0],
        boot_id=lambda: "boot-a",
    )
    snapshot = adapter.observe()
    assert order == ["cloud", "readiness", "routes", "peer"]
    assert snapshot.now == 136.0
    assert snapshot.peer_received_at == 135.0
    assert snapshot.peer_heartbeat is latest[0]


@pytest.mark.parametrize("capabilities", [[], None, ["old-capability"]])
def test_upgrade_rejects_missing_running_capability(capabilities: object) -> None:
    assert not _vm_ha_independent_heartbeat_matches(
        {
            "controller_capabilities": capabilities,
            "mtls": {"peer": {"fresh": True}},
        }
    )


@pytest.mark.parametrize("peer", [None, {}, {"fresh": False}])
def test_upgrade_rejects_missing_or_stale_peer(peer: object) -> None:
    assert not _vm_ha_independent_heartbeat_matches(
        {
            "controller_capabilities": ["vm-ha-independent-heartbeat-v2"],
            "mtls": {"peer": peer},
        }
    )


def test_required_worker_failure_exits_guarded_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guards: list[str] = []
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}))

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError("fixture offline")

        def send(self, value: object) -> None:
            return None

    class Ports:
        heartbeat_snapshots = HeartbeatSnapshots()
        peer_runtime = Peer()

        def sample_local_health(self, *, cancelled: object) -> None:
            raise ValueError("unexpected observer implementation failure")

        def heartbeat(self, **kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            guards.append(boot_id)

        def close(self) -> None:
            return None

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        SimpleNamespace(step=lambda: {}),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
    )
    monkeypatch.setenv("NOTIFY_SOCKET", "fixture")
    monkeypatch.setattr("nebius_vpngw.agent.main.subprocess.run", lambda *args, **kwargs: None)
    with pytest.raises(VMHAWorkerFailure):
        _run_vm_ha_controller(runtime, config_path=config_path)
    assert guards == ["boot-a", "boot-a"]
    assert runtime.closed
    for thread in (runtime.listener_thread, runtime.sampler_thread, runtime.publisher_thread):
        assert thread is not None
        assert not thread.is_alive()
