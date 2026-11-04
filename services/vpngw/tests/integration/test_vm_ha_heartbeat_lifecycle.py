"""Heartbeat lifecycle integration with simulated cloud, OS and TLS boundaries."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from tests.integration.test_vm_ha_observation_lifecycle import FaultCluster
from tests.unit.test_vm_ha_static_selection import action

from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent.main import DefaultVMHAControllerRuntime
from nebius_vpngw.agent.vm_ha import AtomicGenerationStore
from nebius_vpngw.agent.vm_ha.heartbeat import HealthSample
from nebius_vpngw.agent.vm_ha.models import StalePeerStateError
from nebius_vpngw.agent.vm_ha.runtime import BoundPeerRuntime
from nebius_vpngw.agent.vm_ha.transport import (
    AuthenticatedPeerMessage,
    PeerStateExchange,
    PeerTransportError,
)
from nebius_vpngw.agent.vm_ha_controller import ActionKind, ComputeState, HAState

pytestmark = pytest.mark.integration


def message(frame):
    return AuthenticatedPeerMessage(
        frame, frame.node_id, frame.certificate_fingerprint, frame.mtls_epoch
    )


@pytest.fixture
def pair(tmp_path):
    cluster = FaultCluster(tmp_path)
    try:
        yield cluster
    finally:
        cluster.close()


def supply_cloud_handlers(node):
    """Bind the unused methods needed by the production local-mutation wrapper."""

    def unexpected(_action):
        raise AssertionError("Local heartbeat test attempted a cloud mutation")

    for name in (
        "stop_former",
        "detach_former",
        "detach_candidate_for_reproof",
        "attach_candidate",
    ):
        setattr(node.ports.cloud, name, unexpected)


def test_synthetic_recovery_during_suspicion_cancels_fencing(pair):
    owner, standby = pair.nodes.values()
    pair.now = 110.0
    pair.tick(publish=False)
    assert standby.statuses[-1]["state"] == HAState.SUSPECT.value
    assert standby.controller.controller.checkpoints.load().suspect_since == 110.0
    pair.now = 114.0
    owner.ports.sample_local_health(cancelled=lambda: False)
    owner.service._exchange_peer_state()
    standby.ports.peer_runtime.poll(timeout_seconds=0.1)
    pair.tick(publish=False)
    assert standby.controller.controller.checkpoints.load().suspect_since is None
    pair.assert_undisrupted()


@pytest.mark.parametrize("kind", ["duplicate", "reordered", "old-health", "old-timestamp"])
def test_synthetic_unusable_messages_do_not_extend_suspicion(pair, kind):
    owner, standby = pair.nodes.values()
    # Accept sequence 2 first, so a lower sequence is genuinely reordered.
    owner.service._exchange_peer_state()
    owner.service._exchange_peer_state()
    standby.ports.peer_runtime.poll(timeout_seconds=0.1)
    standby.ports.peer_runtime.poll(timeout_seconds=0.1)
    accepted, accepted_at = standby.ports.peer_runtime.observe()
    pair.now = 111.0
    pair.tick(publish=False)
    for now in (112.0, 113.0, 115.0):
        pair.now = now
        frame = accepted
        if kind == "reordered":
            frame = replace(frame, sequence=frame.sequence - 1)
        elif kind == "old-health":
            template = owner.ports.heartbeat(
                boot_id=owner.boot_id, sequence=int(now), clock=pair.clock
            )
            frame = replace(template, health_sample_age_seconds=20.0)
        elif kind == "old-timestamp":
            frame = replace(frame, sequence=int(now))
        standby.transport.inbox.put(message(frame))
        if kind == "old-health":
            standby.ports.peer_runtime.poll(timeout_seconds=0.1)
        else:
            with pytest.raises(StalePeerStateError):
                standby.ports.peer_runtime.poll(timeout_seconds=0.1)
            assert standby.ports.peer_runtime.observe() == (accepted, accepted_at)
        standby.step()
        assert standby.controller.controller.checkpoints.load().suspect_since == 111.0
        assert not standby.actions
    pair.now = 116.0
    standby.step()
    assert standby.actions == [ActionKind.STOP_FORMER_OWNER]


@pytest.mark.parametrize("direction", ["owner-to-standby", "standby-to-owner", "both"])
def test_synthetic_directional_partition_and_reconnection_keep_one_owner(pair, direction):
    original = {}
    for node_id, node in pair.nodes.items():
        if direction == "both" or (node_id == "node-a") == (direction == "owner-to-standby"):
            original[node_id] = node.transport.send
            node.transport.send = lambda _frame: None
    for _turn in range(40):
        pair.tick()
        assert sum(node.kernel.forwarding == "1" for node in pair.nodes.values()) <= 1
        if direction == "standby-to-owner":
            pair.assert_undisrupted()
        elif pair.nodes["node-b"].statuses[-1]["promotion_committed"]:
            break
    if direction != "standby-to-owner":
        assert pair.owner == "node-b"
        assert pair.compute["node-a"] is ComputeState.STOPPED
        assert pair.nodes["node-b"].kernel.forwarding == "1"
    for node_id, send in original.items():
        pair.nodes[node_id].transport.send = send
    for _turn in range(20):
        pair.tick()
        assert sum(node.kernel.forwarding == "1" for node in pair.nodes.values()) <= 1
    assert pair.owner == ("node-a" if direction == "standby-to-owner" else "node-b")


def test_synthetic_restart_keeps_sequence_and_cached_peer_is_not_fresh(pair):
    owner, standby = pair.nodes.values()
    accepted, _ = standby.ports.peer_runtime.observe()
    store = AtomicGenerationStore(standby.state_dir / "replay")
    store.save_accepted_peer_heartbeat(owner.node_id, accepted)
    exchange = PeerStateExchange(
        standby.transport,
        cluster_id="cluster-a",
        peer_node_id=owner.node_id,
        replay_store=store,
        max_heartbeat_age_seconds=10.0,
        max_clock_skew_seconds=0.0,
        wall_clock=pair.clock,
        monotonic_clock=pair.clock,
    )
    receiver = BoundPeerRuntime(
        exchange, clock=pair.clock, peer_node_id=owner.node_id, heartbeat_store=store
    )
    assert receiver.observe() == (accepted, None)
    standby.controller.snapshots.providers = replace(
        standby.controller.snapshots.providers,
        peer=receiver.observe,
    )
    standby.step()
    assert standby.statuses[-1]["state"] == HAState.SUSPECT.value
    assert not standby.actions
    standby.transport.inbox.put(message(accepted))
    with pytest.raises(StalePeerStateError):
        receiver.poll(timeout_seconds=0.1)
    owner.service.close(restore_guard=False)
    restarted = DefaultVMHAControllerRuntime(
        owner.controller,
        owner.ports,
        boot_id=owner.boot_id,
        sequence_path=owner.state_dir / "sequence.json",
        guard_path=owner.state_dir / "guard.json",
        clock=pair.clock,
    )
    try:
        restarted._exchange_peer_state()
        latest = receiver.poll(timeout_seconds=0.1)
        assert latest.sequence > accepted.sequence
        assert receiver.observe()[1] == pair.clock()
        standby.step()
        assert standby.controller.controller.checkpoints.load().suspect_since is None
        assert not standby.actions
        standby.transport.inbox.put(message(replace(latest, boot_id="new-owner-boot", sequence=0)))
        receiver.poll(timeout_seconds=0.1)
        standby.transport.inbox.put(message(replace(latest, sequence=latest.sequence + 100)))
        with pytest.raises(StalePeerStateError):
            receiver.poll(timeout_seconds=0.1)
    finally:
        restarted.close(restore_guard=False)


def test_receiver_persistence_stall_does_not_refresh_old_health(pair, monkeypatch):
    owner, standby = pair.nodes.values()
    entered, release = threading.Event(), threading.Event()
    errors = []
    store = standby.ports.peer_runtime.exchange.replay_store
    save = store.save_replay_state

    def blocked_save(peer_id, replay):
        save(peer_id, replay)
        entered.set()
        assert release.wait(5.0)

    def receive():
        try:
            standby.ports.peer_runtime.poll(timeout_seconds=0.1)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(store, "save_replay_state", blocked_save)
    owner.service._exchange_peer_state()
    worker = threading.Thread(target=receive)
    worker.start()
    try:
        assert entered.wait(2.0)
        pair.now = 112.0
        standby.step()
        assert standby.statuses[-1]["state"] == HAState.SUSPECT.value
    finally:
        release.set()
        worker.join(3.0)
    assert not worker.is_alive() and not errors
    assert standby.ports.peer_runtime.observe()[1] == 101.0
    standby.step()
    assert standby.controller.controller.checkpoints.load().suspect_since == 112.0
    monkeypatch.setattr(store, "save_replay_state", save)
    owner.ports.sample_local_health(cancelled=lambda: False)
    owner.service._exchange_peer_state()
    standby.ports.peer_runtime.poll(timeout_seconds=0.1)
    standby.step()
    assert standby.controller.controller.checkpoints.load().suspect_since is None
    pair.assert_undisrupted()


def test_concurrent_sample_cannot_publish_healthy_across_real_mutation(pair, monkeypatch):
    owner, standby = pair.nodes.values()
    supply_cloud_handlers(owner)
    sample_entered, sample_release = threading.Event(), threading.Event()
    mutation_entered, mutation_release = threading.Event(), threading.Event()
    errors = []
    run = owner.data_plane.runner

    def blocked_run(arguments, timeout):
        if threading.current_thread().name == "overlap-sampler" and arguments[0].endswith("vtysh"):
            sample_entered.set()
            assert sample_release.wait(5.0)
        return run(arguments, timeout)

    def repair(_action):
        mutation_entered.set()
        assert mutation_release.wait(5.0)

    def collect_errors(callback):
        try:
            callback()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(owner.data_plane, "runner", blocked_run)
    monkeypatch.setattr(owner.data_plane, "repair_local", repair)
    effect = replace(
        action(),
        kind=ActionKind.REPAIR_LOCAL_DATAPLANE,
        target_node_id=owner.node_id,
        allocation_id="allocation-a",
        boot_id=owner.boot_id,
        ownership_epoch="42",
        repair_deadline_at=pair.clock() + 5.0,
        repair_reasons=("static-route-repair-required",),
    )
    handler = owner.ports.handlers()[ActionKind.REPAIR_LOCAL_DATAPLANE]
    sampler = threading.Thread(
        target=collect_errors,
        args=(lambda: owner.ports.sample_local_health(cancelled=lambda: False),),
        name="overlap-sampler",
    )
    mutation = threading.Thread(target=collect_errors, args=(lambda: handler(effect),))
    sampler.start()
    try:
        assert sample_entered.wait(2.0)
        mutation.start()
        assert mutation_entered.wait(2.0)
        sample_release.set()
        sampler.join(2.0)
        owner.service._exchange_peer_state()
        standby.ports.peer_runtime.poll(timeout_seconds=0.1)
        frame, _ = standby.ports.peer_runtime.observe()
        assert frame.local_forwarding_ready is None and frame.data_plane_mode is None
        standby.step()
        assert not standby.actions
    finally:
        sample_release.set()
        mutation_release.set()
        sampler.join(3.0)
        if mutation.ident is not None:
            mutation.join(3.0)
    assert not sampler.is_alive() and not mutation.is_alive() and not errors
    pair.tick()
    assert owner.transport.sent[-1].local_forwarding_ready is True
    pair.assert_undisrupted()


def test_synthetic_continuing_unknown_samples_cannot_reset_suspicion(pair):
    owner, standby = pair.nodes.values()
    started = pair.now
    for elapsed in range(16):
        pair.now = started + elapsed
        samples = owner.ports.heartbeat_snapshots
        samples.publish_health(
            HealthSample(pair.clock(), True, None, None, "active"),
            samples.revision(),
        )
        owner.service._exchange_peer_state()
        standby.ports.peer_runtime.poll(timeout_seconds=0.1)
        standby.step()
        checkpoint = standby.controller.controller.checkpoints.load()
        if elapsed < 10:
            assert checkpoint.suspect_since is None
        else:
            assert checkpoint.suspect_since == started + 10.0
        if elapsed < 15:
            assert not standby.actions
    assert standby.actions == [ActionKind.STOP_FORMER_OWNER]


def test_publisher_reservation_stall_uses_current_health_when_it_resumes(pair, monkeypatch):
    owner, standby = pair.nodes.values()
    entered, release = threading.Event(), threading.Event()
    errors = []
    reserve = owner.service._reserve_heartbeat_sequence

    def delayed_reservation():
        sequence = reserve()
        entered.set()
        assert release.wait(5.0)
        return sequence

    def publish():
        try:
            owner.service._exchange_peer_state()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(owner.service, "_reserve_heartbeat_sequence", delayed_reservation)
    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert entered.wait(2.0)
        pair.now = 112.0
        standby.step()
        assert standby.statuses[-1]["state"] == HAState.SUSPECT.value
        owner.ports.sample_local_health(cancelled=lambda: False)
    finally:
        release.set()
        worker.join(3.0)
    assert not worker.is_alive() and not errors
    standby.ports.peer_runtime.poll(timeout_seconds=0.1)
    frame, received_at = standby.ports.peer_runtime.observe()
    assert received_at == pair.clock()
    assert frame.local_forwarding_ready is True
    assert frame.health_sample_age_seconds == 0.0
    standby.step()
    assert standby.controller.controller.checkpoints.load().suspect_since is None
    pair.assert_undisrupted()


def write_report(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def child_service(root, scenario, sequence_archive):
    """Run the production signal/retry/cleanup loop in its own Python process."""
    # Seed the running pair without starting workers before fault hooks and
    # persisted sequence state have been installed.
    with pytest.MonkeyPatch.context() as setup:
        setup.setattr(DefaultVMHAControllerRuntime, "_start_peer_listener", lambda _self: None)
        setup.setattr(DefaultVMHAControllerRuntime, "_start_heartbeat_workers", lambda _self: None)
        cluster = FaultCluster(root, real_time=True)
    owner, standby = cluster.nodes.values()
    supply_cloud_handlers(owner)
    if sequence_archive is not None and sequence_archive.exists():
        owner.service.sequence_path.write_bytes(sequence_archive.read_bytes())
    stop = threading.Event()
    fault = threading.Event()
    transport_entered = threading.Event()
    transport_release = threading.Event()
    transport_release.set()
    errors = []
    receive = owner.transport.receive
    send = owner.transport.send
    run = owner.data_plane.runner
    standby_receive = standby.transport.receive

    def faulty_send(frame):
        if scenario == "publisher-timeout" and fault.is_set() and not transport_release.is_set():
            transport_entered.set()
            raise PeerTransportError("injected recoverable send timeout")
        if scenario == "publisher-stall" and fault.is_set():
            transport_entered.set()
            assert transport_release.wait(20.0)
        if scenario == "publisher-crash" and fault.is_set():
            raise ValueError("injected publisher failure")
        send(frame)

    def faulty_receive(*, timeout_seconds):
        if scenario == "listener-crash" and fault.is_set():
            raise ValueError("injected listener failure")
        return receive(timeout_seconds=timeout_seconds)

    def faulty_run(arguments, timeout):
        if (
            scenario == "sampler-crash"
            and fault.is_set()
            and threading.current_thread().name == "nebius-vpngw-vm-ha-local-health"
        ):
            raise ValueError("injected sampler failure")
        return run(arguments, timeout)

    def stalled_receiver(*, timeout_seconds):
        if scenario == "receiver-stall" and fault.is_set():
            transport_entered.set()
            assert transport_release.wait(20.0)
        return standby_receive(timeout_seconds=timeout_seconds)

    standby.transport.receive = stalled_receiver
    close = owner.service.close

    def supervised_close(*, restore_guard=True):
        stop.set()
        # The fake command boundary completes on shutdown, as a bounded command
        # would. Production close still installs its guard and joins workers.
        owner.cloud_release.set()
        owner.sampler_release.set()
        transport_release.set()
        return close(restore_guard=restore_guard)

    owner.service.close = supervised_close
    owner.transport.send = faulty_send
    owner.transport.receive = faulty_receive
    owner.data_plane.runner = faulty_run
    if scenario == "startup-unknown":
        owner.sampler_release.clear()
        owner.ports.heartbeat_snapshots.mutation()
        owner.ports.heartbeat_snapshots.mutation(completed=True)

    def peer_driver():
        try:
            while not stop.is_set():
                standby.service.step()
                stop.wait(0.1)
        except BaseException as error:
            if not stop.is_set():
                errors.append(repr(error))

    def supervise():
        armed_at = None
        try:
            while not stop.is_set():
                if (root / "release").exists():
                    owner.cloud_release.set()
                    owner.sampler_release.set()
                if armed_at is None and len(owner.transport.sent) >= 3:
                    armed_at = time.monotonic()
                    if scenario in {"publisher-stall", "receiver-stall", "publisher-timeout"}:
                        transport_release.clear()
                    fault.set()
                    if scenario in {"cloud-retry", "shutdown-cloud"}:
                        owner.cloud_entered.clear()
                        if scenario == "cloud-retry":
                            owner.cloud_unavailable = True
                        else:
                            owner.cloud_release.clear()
                    if scenario == "shutdown-sampler":
                        owner.sampler_entered.clear()
                        owner.sampler_release.clear()
                    write_report(root / "armed.json", {"frames": len(owner.transport.sent)})
                if armed_at is not None:
                    elapsed = time.monotonic() - armed_at
                    if scenario == "cloud-retry" and elapsed >= 6.0:
                        owner.cloud_unavailable = False
                        grace = owner.controller.snapshots.cloud_observation_grace
                        if (
                            grace.consecutive_failures == 0
                            and owner.controller.snapshots.last_snapshot.now
                            >= 100.0 + armed_at - cluster.origin + 6.0
                        ):
                            write_report(
                                root / "recovered.json", {"frames": len(owner.transport.sent)}
                            )
                    if scenario == "startup-unknown" and elapsed >= 1.0:
                        owner.sampler_release.set()
                        if owner.transport.sent[-1].local_forwarding_ready is True:
                            write_report(
                                root / "recovered.json", {"frames": len(owner.transport.sent)}
                            )
                    if (
                        scenario in {"publisher-stall", "receiver-stall", "publisher-timeout"}
                        and elapsed >= 6.0
                    ):
                        assert transport_entered.is_set()
                        transport_release.set()
                        # Allow stale backlog to drain and accept a fresh frame.
                        if elapsed >= 8.0:
                            latest, received_at = standby.ports.peer_runtime.observe()
                            if received_at is not None and cluster.clock() - received_at < 2.0:
                                write_report(root / "recovered.json", {"sequence": latest.sequence})
                    if scenario == "shutdown-cloud" and owner.cloud_entered.is_set():
                        write_report(root / "blocked.json", {"stage": "cloud"})
                    if scenario == "shutdown-sampler" and owner.sampler_entered.is_set():
                        write_report(root / "blocked.json", {"stage": "sampler"})
                # Observe the pair continuously, not just final status.
                if scenario.endswith("-crash") and fault.is_set():
                    assert cluster.owner == "node-a"
                    assert not standby.actions
                else:
                    cluster.assert_undisrupted()
                stop.wait(0.02)
        except BaseException as error:
            if not stop.is_set():
                errors.append(repr(error))

    peer_thread = threading.Thread(target=peer_driver, name="peer-controller")
    monitor = threading.Thread(target=supervise, name="lifecycle-monitor")
    peer_thread.start()
    monitor.start()
    outcome = None
    exit_code = 0
    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setenv("NOTIFY_SOCKET", "simulated-systemd-notify")

            def notify(arguments, **kwargs):
                assert arguments[0] == "/usr/bin/systemd-notify"
                return subprocess.CompletedProcess(arguments, 0)

            patcher.setattr(agent_main.subprocess, "run", notify)
            agent_main._run_vm_ha_controller(
                owner.service, config_path=owner.state_dir / "config.yaml"
            )
    except SystemExit as error:
        outcome = "signal-stop"
        exit_code = int(error.code or 0)
    except BaseException as error:
        outcome = type(error).__name__
        exit_code = 17
    finally:
        stop.set()
        owner.cloud_release.set()
        owner.sampler_release.set()
        peer_thread.join(5.0)
        monitor.join(5.0)
        standby.service.close(restore_guard=False)
        if sequence_archive is not None:
            sequence_archive.write_bytes(owner.service.sequence_path.read_bytes())
        frames = []
        for frame in owner.transport.sent:
            frames.append(
                {
                    "sequence": frame.sequence,
                    "sent_at": frame.sent_at,
                    "health": frame.local_forwarding_ready,
                }
            )
        workers = []
        for runtime in (owner.service, standby.service):
            for thread in (
                runtime.publisher_thread,
                runtime.sampler_thread,
                runtime.listener_thread,
            ):
                if thread is not None:
                    workers.append(thread.is_alive())
        write_report(
            root / "result.json",
            {
                "outcome": outcome,
                "forwarding": owner.kernel.forwarding,
                "closed": owner.service.closed,
                "workers_alive": workers,
                "peer_actions": [kind.value for kind in standby.actions],
                "errors": errors,
                "frames": frames,
                "driver_alive": peer_thread.is_alive() or monitor.is_alive(),
            },
        )
    return exit_code


def start_child(root, scenario, sequence_archive=None):
    root.mkdir()
    command = [
        sys.executable,
        "-m",
        "tests.integration.test_vm_ha_heartbeat_lifecycle",
        "--child",
        str(root),
        scenario,
    ]
    if sequence_archive is not None:
        command.append(str(sequence_archive))
    project = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src") + os.pathsep + str(project)
    log_path = root / "process.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command, cwd=project, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
    return process


def wait_file(process, path, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            pytest.fail(
                f"Child exited before {path.name}: {path.parent.joinpath('process.log').read_text()[-4000:]}"
            )
        assert time.monotonic() < deadline, f"Timed out waiting for {path}"
        time.sleep(0.05)
    return json.loads(path.read_text())


def stop_child(process, root):
    (root / "release").touch()
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=12.0)
    result = json.loads((root / "result.json").read_text())
    assert process.returncode == 0
    assert result["outcome"] == "signal-stop"
    return result


def assert_clean_exit(result):
    assert result["closed"]
    assert result["forwarding"] == "0"
    assert not any(result["workers_alive"])
    assert not result["driver_alive"]
    assert not result["peer_actions"]
    assert not result["errors"], result["errors"]


@pytest.mark.parametrize(
    "scenario",
    [
        "cloud-retry",
        "startup-unknown",
        "shutdown-cloud",
        "shutdown-sampler",
        "publisher-stall",
        "receiver-stall",
        "publisher-timeout",
    ],
)
def test_real_service_loop_retries_startup_and_signal_cleanup(tmp_path, scenario):
    root = tmp_path / "child"
    process = start_child(root, scenario)
    try:
        wait_file(process, root / "armed.json")
        if scenario in {
            "cloud-retry",
            "startup-unknown",
            "publisher-stall",
            "receiver-stall",
            "publisher-timeout",
        }:
            wait_file(process, root / "recovered.json")
        else:
            wait_file(process, root / "blocked.json")
        result = stop_child(process, root)
        assert_clean_exit(result)
        if scenario == "cloud-retry":
            assert len(result["frames"]) >= 10
            assert "retrying in 5 seconds" in (root / "process.log").read_text()
        if scenario == "startup-unknown":
            assert any(frame["health"] is None for frame in result["frames"])
            assert any(frame["health"] is True for frame in result["frames"][1:])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)


@pytest.mark.parametrize("scenario", ["publisher-crash", "sampler-crash", "listener-crash"])
def test_real_worker_failure_exits_service_guarded_and_joins_every_worker(tmp_path, scenario):
    root = tmp_path / "child"
    process = start_child(root, scenario)
    try:
        process.wait(timeout=15.0)
        assert process.returncode == 17, (root / "process.log").read_text()[-4000:]
        result = json.loads((root / "result.json").read_text())
        assert result["outcome"] == "VMHAWorkerFailure"
        assert_clean_exit(result)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)


def test_real_process_restart_continues_durable_publisher_sequence(tmp_path):
    archive = tmp_path / "sequence.json"
    previous = -1
    for attempt in range(2):
        root = tmp_path / f"process-{attempt}"
        process = start_child(root, "restart", archive)
        try:
            wait_file(process, root / "armed.json")
            result = stop_child(process, root)
            assert_clean_exit(result)
            # Frame zero is the harness's pre-service baseline. Every actual
            # service publication must advance beyond the previous process.
            sequences = []
            for frame in result["frames"][1:]:
                sequences.append(frame["sequence"])
            assert sequences and min(sequences) > previous
            assert sequences == sorted(set(sequences))
            previous = max(sequences)
            assert json.loads(archive.read_text())["next_sequence"] > previous
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5.0)


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "--child":
        raise SystemExit("Use pytest to execute the lifecycle tests")
    archive = Path(sys.argv[4]) if len(sys.argv) == 5 else None
    raise SystemExit(child_service(Path(sys.argv[2]), sys.argv[3], archive))
