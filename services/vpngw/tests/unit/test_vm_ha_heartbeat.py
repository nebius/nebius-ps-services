import threading
from dataclasses import replace

import pytest
from test_vm_ha_evidence import heartbeat

from nebius_vpngw.agent.vm_ha.heartbeat import HeartbeatPublisher
from nebius_vpngw.agent.vm_ha.transport import PeerTransportError
from nebius_vpngw.agent.vm_ha_controller import LocalReadiness


def publisher(
    now,
    sent,
    *,
    identity=lambda: "stable",
    service_identity=None,
    sample=None,
    build=None,
    send=None,
):
    return HeartbeatPublisher(
        clock=lambda: now[0],
        identity=identity,
        service_identity=service_identity or identity,
        sample=sample
        or (
            lambda deadline: LocalReadiness(
                True, True, True, True, serving_ready=True, protection_ready=True
            )
        ),
        build=build
        or (
            lambda snapshot, ready, evidence: replace(
                heartbeat("a"),
                service_healthy=bool(ready is not None and ready.service_healthy),
                evidence=evidence,
            )
        ),
        send=send or (lambda value, remaining: sent.append((value, remaining))),
    )


def test_sample_age_includes_build_and_every_retry():
    now, sent = [100.0], []

    def build(snapshot, ready, evidence):
        now[0] += 0.5
        return replace(heartbeat("a"), evidence=evidence)

    def send(value, remaining):
        sent.append((value, remaining))
        if len(sent) == 1:
            now[0] += 0.5
            raise PeerTransportError("retry")

    pub = publisher(now, sent, build=build, send=send)
    pub.collect()
    pub.publish()
    assert [v.evidence.age_seconds for v, _ in sent] == [0.5, 1.0]
    assert [r for _, r in sent] == [1.5, 1.0]


@pytest.mark.parametrize("transition", ["collect", "publish", "effect"])
def test_path_changes_preserve_only_fresh_service_liveness(transition):
    now, sent, path = [100.0], [], [0]

    def sample(deadline):
        if transition == "collect":
            path[0] += 1
        now[0] += 0.1
        return LocalReadiness(True, True, True, True, serving_ready=True, protection_ready=True)

    pub = publisher(
        now, sent, identity=lambda: path[0], service_identity=lambda: "boot-config", sample=sample
    )
    if transition == "effect":
        pub.progress("effect", True)
    pub.collect()
    if transition == "publish":
        path[0] += 1
    pub.publish()
    assert sent[-1][0].service_healthy
    assert sent[-1][0].evidence.state == "unknown"
    assert not sent[-1][0].evidence.serving
    now[0] = 105
    pub.publish()
    assert not sent[-1][0].service_healthy


@pytest.mark.parametrize("change", ["configuration", "age"])
def test_unknown_path_positive_service_proof_is_rechecked_after_build(change):
    now, sent, config = [100.0], [], [0]

    def build(snapshot, ready, evidence):
        value = replace(heartbeat("a"), service_healthy=ready.service_healthy, evidence=evidence)
        if change == "configuration":
            config[0] += 1
        else:
            now[0] = 105
        return value

    pub = publisher(
        now,
        sent,
        service_identity=lambda: config[0],
        sample=lambda deadline: LocalReadiness(
            True, False, False, False, sample_complete=False, protection_ready=True
        ),
        build=build,
    )
    pub.collect()
    pub.publish()
    assert not sent


@pytest.mark.parametrize("change", ["slow", "identity", "transition"])
def test_partial_or_transition_sample_cannot_claim_health(change):
    now, sent, identity = [100.0], [], [0]

    def sample(deadline):
        assert deadline == 105
        if change == "slow":
            now[0] = deadline
        if change == "identity":
            identity[0] += 1
        return LocalReadiness(True, True, True, True, serving_ready=True, protection_ready=True)

    pub = publisher(now, sent, identity=lambda: identity[0], sample=sample)
    if change == "transition":
        pub.progress("effect", True)
    pub.collect()
    pub.publish()
    assert sent[0][0].evidence.state == "unknown"
    assert not sent[0][0].evidence.serving


def test_cloud_effect_keeps_local_health_but_dataplane_effect_invalidates():
    now, sent = [100.0], []
    pub = publisher(now, sent)
    pub.collect()
    pub.progress("effect")
    pub.publish()
    assert sent[-1][0].evidence.serving
    pub.progress("effect", True)
    pub.publish()
    assert sent[-1][0].evidence.state == "unknown"
    pub.invalidate()
    pub.publish()
    assert len(sent) == 2


@pytest.mark.parametrize("phase", ["observing", "idle", "effect"])
def test_phase_only_progress_does_not_wait_for_network_publication(phase):
    now, sent = [100.0], []
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()

    def send(value, remaining):
        entered.set()
        assert release.wait(2)
        sent.append(value)

    pub = publisher(now, sent, send=send)
    pub.collect()
    sample = pub.local
    sender = threading.Thread(target=pub.publish)
    sender.start()
    assert entered.wait(1)

    def progress():
        pub.progress(phase)
        updated.set()

    updater = threading.Thread(target=progress)
    updater.start()
    try:
        # Network publication may use its whole allowance. An advisory phase
        # change must leave the controller free to verify its repair meanwhile.
        assert updated.wait(0.25)
        assert pub.phase == phase and pub.local is sample
        assert pub.generation == 0 and not pub.health_transition
        assert sender.is_alive()
    finally:
        release.set()
        updater.join(2)
        sender.join(2)
    assert not updater.is_alive() and not sender.is_alive()
    assert len(sent) == 1 and sent[0].evidence.serving


@pytest.mark.parametrize("entering", [True, False])
def test_health_transition_still_drains_admitted_publication(entering):
    now, sent = [100.0], []
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()

    def send(value, remaining):
        entered.set()
        assert release.wait(2)
        sent.append(value)

    pub = publisher(now, sent, send=send)
    if not entering:
        pub.progress("effect", True)
    pub.collect()
    generation = pub.generation
    sender = threading.Thread(target=pub.publish)
    sender.start()
    assert entered.wait(1)

    def progress():
        pub.progress("effect" if entering else "idle", entering)
        updated.set()

    updater = threading.Thread(target=progress)
    updater.start()
    try:
        assert not updated.wait(0.05)
        assert pub.generation == generation
    finally:
        release.set()
        updater.join(2)
        sender.join(2)
    assert updated.is_set() and not updater.is_alive() and not sender.is_alive()
    assert pub.generation == generation + 1 and pub.local is None
    assert pub.health_transition is entering
    pub.publish()
    assert sent[-1].evidence.state == "unknown" and not sent[-1].evidence.serving


def test_shutdown_waits_for_admitted_send_and_rejects_late_collection():
    now, sent = [100.0], []
    entered, release = threading.Event(), threading.Event()

    def send(value, remaining):
        entered.set()
        assert release.wait(2)
        sent.append(value)

    pub = publisher(now, sent, send=send)
    pub.collect()
    sender = threading.Thread(target=pub.publish)
    sender.start()
    assert entered.wait(1)
    closer = threading.Thread(target=pub.invalidate)
    closer.start()
    assert pub.stop.wait(1)
    assert closer.is_alive()
    release.set()
    sender.join(2)
    closer.join(2)
    assert not sender.is_alive() and not closer.is_alive()
    pub.collect()
    pub.publish()
    assert pub.local is None and len(sent) == 1


def test_listener_failure_drains_publication_before_forwarding_guard():
    from types import SimpleNamespace

    from nebius_vpngw.agent.main import DefaultVMHAControllerRuntime

    now, sent = [100.0], []
    entered, release, guarded = threading.Event(), threading.Event(), threading.Event()

    def send(value, remaining):
        entered.set()
        assert release.wait(2)
        assert not guarded.is_set()
        sent.append(value)

    pub = publisher(now, sent, send=send)
    pub.collect()
    pub.start = lambda: None
    runtime = object.__new__(DefaultVMHAControllerRuntime)
    runtime.publisher = pub
    runtime.started_guarded = True
    runtime._start_peer_listener = lambda: None
    runtime.listener_error = RuntimeError("listener failed")
    runtime.boot_id = "boot"
    runtime.ports = SimpleNamespace(install_shutdown_guard=lambda **kwargs: guarded.set())
    sender = threading.Thread(target=pub.publish)
    sender.start()
    assert entered.wait(1)
    errors = []

    def fail():
        try:
            runtime.step()
        except RuntimeError as error:
            errors.append(error)

    failure = threading.Thread(target=fail)
    failure.start()
    assert pub.stop.wait(1)
    assert not guarded.is_set()
    release.set()
    sender.join(2)
    failure.join(2)
    assert guarded.is_set() and len(errors) == 1
    pub.publish()
    assert len(sent) == 1


@pytest.mark.parametrize("failure", ["observation", "listener", "sampler"])
def test_failed_service_runtime_exits_instead_of_retrying_without_heartbeats(monkeypatch, failure):
    from types import SimpleNamespace

    from nebius_vpngw.agent.main import DefaultVMHAControllerRuntime, _run_vm_ha_controller

    events = []
    pub = publisher([100.0], [])
    pub.start = lambda: None
    if failure == "sampler":
        pub.error = RuntimeError("sample worker failed")

    def observe():
        events.append("observation")
        raise RuntimeError("observation deadline exhausted")

    runtime = object.__new__(DefaultVMHAControllerRuntime)
    runtime.publisher = pub
    runtime.started_guarded = True
    runtime._start_peer_listener = lambda: None
    runtime.listener_error = RuntimeError("listener failed") if failure == "listener" else None
    runtime.boot_id = "boot"
    runtime.config = None
    runtime.controller = SimpleNamespace(step=observe)
    runtime.ports = SimpleNamespace(install_shutdown_guard=lambda **kwargs: events.append("guard"))
    runtime.close = lambda: events.append("closed")
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda path: {})
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/test-notify")
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.subprocess.run", lambda *args, **kwargs: events.append("ready")
    )

    def must_not_retry(_seconds):
        pytest.fail("a stopped heartbeat publisher cannot support another controller step")

    monkeypatch.setattr("nebius_vpngw.agent.main.time.sleep", must_not_retry)
    with pytest.raises(RuntimeError):
        _run_vm_ha_controller(runtime)
    assert pub.stop.is_set()
    assert events[0] == "ready" and events[-2:] == ["guard", "closed"]
    assert events.count("observation") == (1 if failure == "observation" else 0)


@pytest.mark.parametrize("probe_succeeds", [True, False])
def test_independent_probe_is_fresh_bounded_and_never_updates_hysteresis(
    tmp_path, monkeypatch, probe_succeeds
):
    import json
    from types import SimpleNamespace

    from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects
    from nebius_vpngw.agent.vm_ha.tunnels import Health

    probe = {"source_ip": "192.0.2.1", "target_ip": "198.51.100.1", "interval_seconds": 30}
    state = tmp_path / "tunnel-probes.json"
    state.write_text(json.dumps({"path": {"probe": probe, "health": "healthy", "observed_at": 1}}))
    original = state.read_bytes()
    now, commands = [100.0], []

    def command(argv, limit):
        commands.append((argv, limit))
        now[0] += 1
        if argv[0] == "ip":
            return SimpleNamespace(returncode=0, stdout='[{"addr_info":[{"local":"192.0.2.1"}]}]')
        return SimpleNamespace(returncode=0 if probe_succeeds else 1, stdout="")

    monkeypatch.setattr("nebius_vpngw.agent.vm_ha.tunnel_runtime._run_command", command)
    effects = NativeTunnelEffects(
        {"gateway": {"tunnel_probes": {"path": probe}}},
        tmp_path,
        lambda authority: False,
        active_revalidate=lambda authority: False,
        clock=lambda: now[0],
        command_runner=command,
    )
    effects.effect_deadline = 105
    effects.observer.observe = lambda *args, **kwargs: Health.HEALTHY
    policy = SimpleNamespace(mode="static", paths=[SimpleNamespace(name="path", interface="xfrm0")])
    result = effects.observe_readonly(policy, "path", selected=True)
    assert result is (Health.HEALTHY if probe_succeeds else Health.UNKNOWN)
    assert [cmd[0][0] for cmd in commands] == ["ip", "ping"]
    assert all(limit <= 3 for _, limit in commands)
    assert state.read_bytes() == original and len(list(tmp_path.iterdir())) == 1
    now[0] = 105
    assert effects.observe_readonly(policy, "path", selected=True) is Health.UNKNOWN
    assert len(commands) == 2


def identity_runtime(tmp_path):
    import json
    from types import SimpleNamespace

    from nebius_vpngw.agent.main import DefaultVMHAControllerRuntime
    from nebius_vpngw.agent.vm_ha.store import _envelope
    from nebius_vpngw.agent.vm_ha.tunnels import empty_state
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    runtime = object.__new__(DefaultVMHAControllerRuntime)
    runtime.boot_id = "boot"
    runtime.sequence_path = tmp_path / "sequence.json"
    runtime.guard_path = tmp_path / "guard.json"
    runtime.config_path = tmp_path / "config.yaml"
    runtime.config_path.write_text("fixture: true\n")
    runtime.monotonic_clock = lambda: 100.0
    runtime.ports = SimpleNamespace(
        sample_local_health=lambda **kw: LocalReadiness(
            True, True, True, True, serving_ready=True, protection_ready=True
        )
    )
    state = empty_state()
    state["episode"] = {
        "owner": {
            "cluster": "cluster",
            "owner": "node",
            "allocation": "allocation",
            "incarnation": 1,
        },
        "started": 80.0,
        "deadline": 110.0,
        "last_observed": 100.0,
        "healthy_since": None,
        "exhausted": False,
    }
    records = {
        "guard.json": {
            "schema": "nebius-vpngw/vm-ha-status-v1",
            "guard_boot_id": "boot",
            "data_plane_mode": "active",
            "installed_at": 100.0,
        },
        "data-plane.json": {
            "schema": "nebius-vpngw/vm-ha-data-plane-v1",
            "boot_id": "boot",
            "mode": "active",
            "operation_id": "prepare",
        },
        "controller-checkpoint.json": controller_checkpoint_to_dict(
            ControllerCheckpoint(ownership_incarnation=1)
        ),
        "standby-auto-healing-policy.json": {"phase": "committed", "updated_at": 100.0},
        "tunnel-selection.json": state,
    }

    def save():
        for name, value in records.items():
            (tmp_path / name).write_text(
                json.dumps(_envelope(value) if name == "tunnel-selection.json" else value)
            )

    save()
    return runtime, records, save


def recovered_checkpoint(*, passive=True, observations=2, healthy_since=100.0):
    from nebius_vpngw.agent.vm_ha_controller import (
        ControllerCheckpoint,
        DataPlaneMode,
        HAState,
        RepairAttempt,
    )

    return ControllerCheckpoint(
        state=HAState.NORMAL if passive else HAState.ACTIVE,
        ownership_incarnation=1,
        repair_attempt=RepairAttempt(
            operation_id="repair",
            owner_node_id="owner",
            allocation_id="allocation",
            ownership_epoch="epoch",
            ownership_incarnation=1,
            generation_id="generation",
            boot_id="boot",
            failure_fingerprint=("daemon-missing",),
            started_at=80.0,
            deadline_at=200.0,
            healthy_since=healthy_since,
            healthy_observations=observations,
            repair_mode=DataPlaneMode.PASSIVE if passive else DataPlaneMode.ACTIVE,
        ),
    )


@pytest.mark.parametrize("passive", [True, False])
@pytest.mark.parametrize("phase", ["collect", "publish"])
def test_completed_repair_retirement_preserves_fresh_health(tmp_path, passive, phase):
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict

    runtime, records, save = identity_runtime(tmp_path)
    checkpoint = recovered_checkpoint(passive=passive)
    records["controller-checkpoint.json"] = controller_checkpoint_to_dict(checkpoint)
    mode = "passive" if passive else "active"
    records["guard.json"]["data_plane_mode"] = mode
    records["data-plane.json"]["mode"] = mode
    save()
    now, sent = [160.0], []

    def retire():
        records["controller-checkpoint.json"] = controller_checkpoint_to_dict(
            replace(checkpoint, repair_attempt=None)
        )
        save()

    def sample(deadline):
        if phase == "collect":
            retire()
        return LocalReadiness(
            True, True, True, True, serving_ready=not passive, protection_ready=True
        )

    pub = publisher(
        now,
        sent,
        identity=runtime._health_identity,
        service_identity=runtime._service_health_identity,
        sample=sample,
    )
    pub.collect()
    if phase == "publish":
        retire()
    before = (tmp_path / "controller-checkpoint.json").read_bytes()
    pub.publish()
    assert sent[-1][0].evidence.state == "healthy"
    assert sent[-1][0].evidence.serving is not passive
    assert sent[-1][0].service_healthy
    assert (tmp_path / "controller-checkpoint.json").read_bytes() == before


@pytest.mark.parametrize(
    "change",
    ["one-observation", "no-healthy-time", "repairing", "exhausted", "wrong-mode", "pending"],
)
def test_unconfirmed_repair_retirement_still_invalidates_health_identity(tmp_path, change):
    from nebius_vpngw.agent.vm_ha.models import DigestSet
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import (
        ActionKind,
        ControllerAction,
        DataPlaneMode,
        HAState,
    )

    runtime, records, save = identity_runtime(tmp_path)
    checkpoint = recovered_checkpoint(
        observations=1 if change == "one-observation" else 2,
        healthy_since=None if change == "no-healthy-time" else 100.0,
    )
    if change in {"repairing", "exhausted"}:
        checkpoint = replace(
            checkpoint,
            state=HAState.REPAIRING if change == "repairing" else HAState.REPAIR_EXHAUSTED,
        )
    if change == "wrong-mode":
        checkpoint = replace(
            checkpoint,
            repair_attempt=replace(checkpoint.repair_attempt, repair_mode=DataPlaneMode.ACTIVE),
        )
    if change == "pending":
        checkpoint = replace(
            checkpoint,
            pending_action=ControllerAction(
                kind=ActionKind.ENTER_PASSIVE,
                operation_id="pending",
                boot_id="boot",
                target_node_id="owner",
                allocation_id="allocation",
                ownership_epoch="epoch",
                generation_id="generation",
                digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
                ownership_incarnation=1,
            ),
        )
    records["controller-checkpoint.json"] = controller_checkpoint_to_dict(checkpoint)
    save()
    before = runtime._health_identity()
    records["controller-checkpoint.json"] = controller_checkpoint_to_dict(
        replace(checkpoint, repair_attempt=None)
    )
    save()
    assert runtime._health_identity() != before


def test_malformed_checkpoint_cannot_be_normalized_as_completed_repair(tmp_path):
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict

    runtime, records, save = identity_runtime(tmp_path)
    records["controller-checkpoint.json"] = controller_checkpoint_to_dict(recovered_checkpoint())
    del records["controller-checkpoint.json"]["repair_attempt"]["generation_id"]
    save()
    with pytest.raises(ValueError, match="repair attempt"):
        runtime._health_identity()


def test_diagnostic_record_updates_do_not_starve_healthy_collection(tmp_path):
    runtime, records, save = identity_runtime(tmp_path)
    now, sent = [100.0], []

    def sample(deadline):
        records["guard.json"]["installed_at"] += 2
        records["standby-auto-healing-policy.json"]["updated_at"] += 2
        records["tunnel-selection.json"]["episode"].update(last_observed=102.0, healthy_since=101.0)
        save()
        now[0] += 2
        return runtime._sample_local_health(deadline)

    pub = publisher(now, sent, identity=runtime._health_identity, sample=sample)
    pub.collect()
    pub.publish()
    assert sent[-1][0].evidence.state == "healthy"
    assert sent[-1][0].evidence.serving


@pytest.mark.parametrize(
    "change",
    [
        "guard-boot",
        "guard-mode",
        "dataplane-boot",
        "checkpoint",
        "selection",
        "deadline",
        "exhausted",
        "policy",
        "config",
    ],
)
def test_semantic_identity_change_invalidates_overlapping_health(tmp_path, change):
    runtime, records, save = identity_runtime(tmp_path)
    now, sent = [100.0], []

    def sample(deadline):
        if change == "guard-boot":
            records["guard.json"]["guard_boot_id"] = "new-boot"
        if change == "guard-mode":
            records["guard.json"]["data_plane_mode"] = "passive"
        if change == "dataplane-boot":
            records["data-plane.json"]["boot_id"] = "new-boot"
        if change == "checkpoint":
            records["controller-checkpoint.json"]["ownership_incarnation"] += 1
        if change == "selection":
            records["tunnel-selection.json"]["selected"] = {"connection": "backup"}
        if change == "deadline":
            records["tunnel-selection.json"]["episode"].update(started=81.0, deadline=111.0)
        if change == "exhausted":
            records["tunnel-selection.json"]["episode"]["exhausted"] = True
        if change == "policy":
            records["standby-auto-healing-policy.json"]["phase"] = "prepare"
        if change == "config":
            runtime.config_path.write_text("fixture: changed\n")
        save()
        return LocalReadiness(True, True, True, True, serving_ready=True, protection_ready=True)

    pub = publisher(now, sent, identity=runtime._health_identity, sample=sample)
    pub.collect()
    pub.publish()
    assert sent[-1][0].evidence.state == "unknown"
    assert not sent[-1][0].evidence.serving


def test_tunnel_success_counter_is_diagnostic_but_completion_invalidates(tmp_path):
    runtime, records, save = identity_runtime(tmp_path)
    operation = {
        "id": "operation",
        "connection": "site",
        "target": "backup",
        "verb": "failover",
        "requested_verb": "failover",
        "requested_target": "backup",
        "previous": "preferred",
        "phase": "verify",
        "deadline": 110.0,
        "attempt_deadline": 108.0,
        "successes": 0,
        "attempted": ["backup"],
        "result": None,
        "effects_started": True,
    }
    records["tunnel-selection.json"]["operation"] = operation
    save()
    before = runtime._health_identity()
    operation["successes"] = 1
    save()
    assert runtime._health_identity() == before
    operation.update(successes=2, phase="succeeded", result="succeeded")
    save()
    assert runtime._health_identity() != before


def test_tunnel_checksum_is_validated_before_identity_projection(tmp_path):
    import json

    from nebius_vpngw.agent.vm_ha.store import CorruptStateError

    runtime, records, save = identity_runtime(tmp_path)
    path = tmp_path / "tunnel-selection.json"
    envelope = json.loads(path.read_text())
    envelope["payload"]["episode"]["last_observed"] += 1
    path.write_text(json.dumps(envelope))
    with pytest.raises(CorruptStateError):
        runtime._health_identity()


@pytest.mark.parametrize(
    "record,field,value",
    [
        ("guard.json", "guard_boot_id", "previous-boot"),
        ("guard.json", "data_plane_mode", "blocked"),
        ("guard.json", "schema", "invalid"),
        ("data-plane.json", "boot_id", "previous-boot"),
        ("data-plane.json", "mode", "passive"),
        ("data-plane.json", "schema", "invalid"),
    ],
)
def test_stable_invalid_authority_cannot_produce_serving_evidence(tmp_path, record, field, value):
    runtime, records, save = identity_runtime(tmp_path)
    records[record][field] = value
    save()
    now, sent = [100.0], []
    pub = publisher(
        now, sent, identity=runtime._health_identity, sample=runtime._sample_local_health
    )
    pub.collect()
    pub.publish()
    assert sent[-1][0].evidence.state == "unknown"
    assert not sent[-1][0].evidence.serving


def test_current_passive_authority_cannot_claim_serving(tmp_path):
    runtime, records, save = identity_runtime(tmp_path)
    records["guard.json"]["data_plane_mode"] = records["data-plane.json"]["mode"] = "passive"
    save()
    assert not runtime._sample_local_health(105).serving_ready
