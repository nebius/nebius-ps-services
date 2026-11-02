from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import _peer, _snapshot

from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHACheckpointFileStore,
    VMHACloudObservationUnavailable,
    VMHAControllerRuntime,
    VMHAEffectAdapter,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
)
from nebius_vpngw.agent.routing_guard import (
    require_vm_ha_current_boot_ready,
    vm_ha_reload_deferred,
)
from nebius_vpngw.agent.vm_ha.observation import ObservationGrace
from nebius_vpngw.agent.vm_ha.runtime import (
    BGPExportState,
    BoundRouteRuntime,
    CommandResult,
    LocalAuthorityError,
    LocalDataPlaneObservation,
    LocalObservationUnavailable,
    SystemDataPlaneRuntime,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    VMHAController,
)
from nebius_vpngw.deploy.vm_ha_cloud import RetryableHACloudError


def test_grace_requires_both_consecutive_failures_and_elapsed_time() -> None:
    grace = ObservationGrace()
    grace.failed(0.0)
    grace.failed(1.0)
    assert not grace.expired(100.0)
    grace.failed(2.0)
    assert not grace.expired(9.9)
    assert grace.expired(10.0)
    grace.succeeded()
    assert grace.status(100.0) == {
        "state": "available",
        "consecutive_failures": 0,
        "failure_age_seconds": 0.0,
    }


def test_route_repair_continuity_does_not_request_another_agent_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses = iter(
        (
            {"promotion_ready": True, "forwarding_continuity": True},
            {"promotion_ready": False, "forwarding_continuity": True},
            {"promotion_ready": True, "forwarding_continuity": True},
            {"promotion_ready": False, "forwarding_continuity": False},
            {"promotion_ready": True, "forwarding_continuity": True},
        )
    )
    reconcile = Mock()
    ports = SimpleNamespace(
        local=SimpleNamespace(role=SimpleNamespace(value="active"), node_id="node-a"),
        data_plane=SimpleNamespace(request_agent_reconcile=reconcile),
    )
    runtime = DefaultVMHAControllerRuntime(
        SimpleNamespace(step=lambda: next(statuses)),
        ports,
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
    )
    runtime.started_guarded = True
    monkeypatch.setattr(runtime, "_start_peer_listener", lambda: None)
    monkeypatch.setattr(runtime, "_start_heartbeat_workers", lambda: None)
    for _ in range(3):
        runtime.step()
    reconcile.assert_called_once()
    runtime.step()
    runtime.step()
    assert reconcile.call_count == 2


def owner_runtime(tmp_path: Path):
    now = [0.0]
    mode = [DataPlaneMode.ACTIVE]
    health = [LocalReadiness(True, True, True, True)]
    cloud = [
        CloudObservation(
            True,
            "allocation-a",
            "node-a",
            "node-b",
            ComputeState.RUNNING,
            True,
            True,
            True,
            "7",
        )
    ]
    failures = {"cloud": False, "mode": None, "receipt": None}
    routes = [None]
    effects = []
    guards = []
    guard_path = tmp_path / "guard.json"
    guard_path.write_text(json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}))

    def observe_cloud():
        if failures["cloud"]:
            raise RetryableHACloudError("unavailable")
        return cloud[0]

    def observe_mode():
        if failures["mode"] is not None:
            raise failures["mode"]
        return mode[0]

    def observe_routes():
        if failures["receipt"] is not None:
            raise failures["receipt"]
        return routes[0]

    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            cloud=observe_cloud,
            readiness=lambda: health[0],
            data_plane=observe_mode,
            routes=observe_routes,
            peer=lambda: (None, None),
        ),
        state_dir=tmp_path,
        clock=lambda: now[0],
        observation_clock=lambda: now[0],
        boot_id=lambda: "boot-a",
    )
    observed = adapter.observe()
    routes[0] = replace(observed.route_reconciliation_context, operation_id="routes")
    store = VMHACheckpointFileStore(tmp_path / "checkpoint.json")
    store.save(ControllerCheckpoint(state=HAState.ACTIVE))

    def effect(action):
        effects.append(action)
        if action.kind is ActionKind.DISABLE_ACTIVE:
            mode[0] = DataPlaneMode.BLOCKED

    runtime = VMHAControllerRuntime(
        snapshots=adapter,
        effects=VMHAEffectAdapter(
            {kind: effect for kind in ActionKind}, receipt_path=tmp_path / "effect.json"
        ),
        checkpoints=store,
        status_path=tmp_path / "status.json",
        guard=lambda: guards.append("guard") or {},
    )
    return runtime, now, mode, health, cloud, failures, effects, guards


def test_local_query_failure_keeps_forwarding_then_fences_only_after_grace(tmp_path: Path) -> None:
    runtime, now, mode, health, _cloud, _failures, effects, guards = owner_runtime(tmp_path)
    runtime.step()
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    for turn in (1.0, 2.0, 9.0):
        now[0] = turn
        status = runtime.step()
        assert status["forwarding_continuity"]
        assert not status["promotion_ready"]
        assert mode[0] is DataPlaneMode.ACTIVE
        assert not effects
        assert not guards
        (tmp_path / "boot").write_text("boot-a")
        cfg = {"vm_ha": {"enabled": True, "generation": {"generation_id": "a" * 64}}}
        with pytest.raises(RuntimeError):
            require_vm_ha_current_boot_ready(
                cfg,
                status_path=tmp_path / "status.json",
                guard_path=tmp_path / "guard.json",
                boot_id_path=tmp_path / "boot",
            )
        assert vm_ha_reload_deferred(
            cfg,
            status_path=tmp_path / "status.json",
            guard_path=tmp_path / "guard.json",
            boot_id_path=tmp_path / "boot",
        )
    now[0] = 11.0
    status = runtime.step()
    assert status["reasons"] == ["local-observation-grace-exhausted"]
    assert effects[-1].kind is ActionKind.DISABLE_ACTIVE
    assert not status["forwarding_continuity"]
    assert not guards


@pytest.mark.parametrize("readiness_also_unavailable", [False, True])
def test_local_receipt_query_failure_uses_the_same_grace(
    tmp_path: Path, readiness_also_unavailable: bool
) -> None:
    runtime, now, mode, health, _cloud, failures, effects, guards = owner_runtime(tmp_path)
    runtime.step()
    failures["receipt"] = LocalObservationUnavailable("FRR query unavailable")
    if readiness_also_unavailable:
        health[0] = replace(health[0], observation_available=False)
    for count, turn in enumerate((1.0, 2.0, 9.0), start=1):
        now[0] = turn
        status = runtime.step()
        assert status["forwarding_continuity"]
        assert not status["promotion_ready"]
        assert runtime.snapshots.local_observation_grace.consecutive_failures == count
        assert mode[0] is DataPlaneMode.ACTIVE
        assert not effects
        assert not guards
    now[0] = 11.0
    status = runtime.step()
    assert status["reasons"] == ["local-observation-grace-exhausted"]
    assert effects[-1].kind is ActionKind.DISABLE_ACTIVE


def test_local_receipt_query_failure_does_not_reuse_changed_ownership(tmp_path: Path) -> None:
    runtime, now, _mode, _health, cloud, failures, effects, _guards = owner_runtime(tmp_path)
    runtime.step()
    failures["receipt"] = LocalObservationUnavailable("FRR query unavailable")
    cloud[0] = replace(cloud[0], ownership_epoch="8")
    now[0] = 1.0
    runtime.step()
    assert effects[-1].kind is ActionKind.DISABLE_ACTIVE


def test_local_receipt_query_success_resets_grace(tmp_path: Path) -> None:
    runtime, now, _mode, _health, _cloud, failures, _effects, _guards = owner_runtime(tmp_path)
    runtime.step()
    failures["receipt"] = LocalObservationUnavailable("FRR query unavailable")
    for turn in (1.0, 2.0):
        now[0] = turn
        runtime.step()
    failures["receipt"] = None
    now[0] = 3.0
    runtime.step()
    assert runtime.snapshots.local_observation_grace.consecutive_failures == 0


def test_a_successful_observation_resets_the_local_failure_streak(tmp_path: Path) -> None:
    runtime, now, _mode, health, _cloud, _failures, effects, _guards = owner_runtime(tmp_path)
    unavailable = LocalReadiness(False, False, False, False, observation_available=False)
    for turn in (1.0, 2.0):
        now[0] = turn
        health[0] = unavailable
        runtime.step()
    now[0] = 20.0
    health[0] = LocalReadiness(True, True, True, True)
    runtime.step()
    health[0] = unavailable
    now[0] = 21.0
    status = runtime.step()
    assert status["observation_health"]["local"]["consecutive_failures"] == 1
    assert status["observation_health"]["local"]["failure_age_seconds"] == 0.0
    assert not effects


def test_prolonged_cloud_failure_does_not_consume_local_grace(tmp_path: Path) -> None:
    runtime, now, mode, health, _cloud, failures, effects, guards = owner_runtime(tmp_path)
    failures["cloud"] = True
    for turn in (1.0, 60.0, 100.0):
        now[0] = turn
        with pytest.raises(VMHACloudObservationUnavailable):
            runtime.step()
    assert runtime.snapshots.cloud_observation_grace.expired(100.0)
    assert mode[0] is DataPlaneMode.ACTIVE
    failures["cloud"] = False
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    now[0] = 101.0
    status = runtime.step()
    assert status["observation_health"]["local"]["failure_age_seconds"] == 0.0
    assert status["forwarding_continuity"]
    assert not effects
    assert not guards


def test_unavailable_forwarding_query_uses_grace_but_invalid_authority_does_not(
    tmp_path: Path,
) -> None:
    runtime, now, mode, _health, _cloud, failures, effects, guards = owner_runtime(tmp_path)
    runtime.step()
    now[0] = 1.0
    failures["mode"] = RuntimeError("sysctl query unavailable")
    status = runtime.step()
    assert status["forwarding_continuity"]
    assert mode[0] is DataPlaneMode.ACTIVE
    assert not effects
    assert not guards
    failures["mode"] = LocalAuthorityError("durable authority conflicts")
    with pytest.raises(LocalAuthorityError):
        runtime.step()
    assert guards == ["guard"]


def test_positive_ownership_loss_overrides_local_observation_grace(tmp_path: Path) -> None:
    runtime, now, _mode, health, cloud, _failures, effects, _guards = owner_runtime(tmp_path)
    runtime.step()
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    cloud[0] = replace(
        cloud[0],
        observed_owner_node_id="node-b",
        candidate_attachment_exact=False,
        ownership_re_read_exact=False,
        former_attachment_absent=False,
        former_attachment_exact=True,
    )
    now[0] = 1.0
    runtime.step()
    assert effects[-1].kind is ActionKind.DISABLE_ACTIVE


def test_peer_unknown_health_gets_grace_before_suspicion_and_takeover(tmp_path: Path) -> None:
    now = [0.0]
    unknown = replace(_peer(), local_forwarding_ready=None)
    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            cloud=lambda: None,
            readiness=lambda: None,
            data_plane=lambda: None,
            routes=lambda: None,
            peer=lambda: (None, None),
        ),
        state_dir=tmp_path,
        observation_clock=lambda: now[0],
    )
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    checkpoint = ControllerCheckpoint()
    for turn, expected in (
        (0.0, HAState.NORMAL),
        (5.0, HAState.NORMAL),
        (10.0, HAState.SUSPECT),
        (14.0, HAState.SUSPECT),
        (15.0, HAState.FENCING),
    ):
        now[0] = turn
        peer = replace(unknown, sequence=int(turn))
        grace = adapter._peer_in_observation_grace(peer, turn, turn)
        decision = policy.decide(
            _snapshot(
                now=turn, peer_heartbeat=peer, peer_received_at=turn, peer_observation_grace=grace
            ),
            checkpoint,
        )
        assert decision.state is expected
        checkpoint = decision.checkpoint
    assert decision.action.kind is ActionKind.STOP_FORMER_OWNER


def test_unknown_peer_replay_and_stale_samples_do_not_refresh_grace(tmp_path: Path) -> None:
    runtime, now, _mode, _health, _cloud, _failures, _effects, _guards = owner_runtime(tmp_path)
    adapter = runtime.snapshots
    peer = replace(_peer(), local_forwarding_ready=None)
    assert adapter._peer_in_observation_grace(peer, 0.0, 0.0)
    now[0] = 9.0
    assert adapter._peer_in_observation_grace(peer, 0.0, 9.0)
    assert adapter.peer_observation_grace.consecutive_failures == 1
    now[0] = 11.0
    assert not adapter._peer_in_observation_grace(peer, 0.0, 11.0)
    assert adapter.peer_observation_grace.first_failure_at == 0.0
    stale_healthy = replace(
        peer, sequence=2, local_forwarding_ready=True, health_sample_age_seconds=20.0
    )
    assert not adapter._peer_in_observation_grace(stale_healthy, 11.0, 11.0)
    assert adapter.peer_observation_grace.first_failure_at == 0.0
    peer = replace(peer, sequence=2)
    assert adapter._peer_in_observation_grace(peer, 11.0, 11.0)
    peer = replace(peer, sequence=3)
    assert not adapter._peer_in_observation_grace(peer, 11.0, 11.0)


def test_confirmed_bad_peer_health_does_not_receive_unknown_health_grace(tmp_path: Path) -> None:
    runtime, now, _mode, _health, _cloud, _failures, _effects, _guards = owner_runtime(tmp_path)
    adapter = runtime.snapshots
    unknown = replace(_peer(), local_forwarding_ready=None)
    assert adapter._peer_in_observation_grace(unknown, 0.0, 0.0)
    now[0] = 1.0
    assert not adapter._peer_in_observation_grace(replace(unknown, service_healthy=False), 1.0, 1.0)
    assert adapter.peer_observation_grace.consecutive_failures == 0


def test_unknown_health_does_not_cancel_suspicion_from_a_confirmed_failure() -> None:
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    failed = replace(_peer(), local_forwarding_ready=False)
    suspected = policy.decide(
        _snapshot(now=100.0, peer_heartbeat=failed, peer_received_at=100.0),
        ControllerCheckpoint(),
    )
    assert suspected.state is HAState.SUSPECT
    unknown = replace(failed, sequence=2, local_forwarding_ready=None)
    takeover = policy.decide(
        _snapshot(
            now=105.0, peer_heartbeat=unknown, peer_received_at=105.0, peer_observation_grace=True
        ),
        suspected.checkpoint,
    )
    assert takeover.action.kind is ActionKind.STOP_FORMER_OWNER


def test_partial_bgp_connection_loss_retains_other_connection_but_blocks_promotion() -> None:
    routes = object.__new__(BoundRouteRuntime)
    routes.static_manifest = SimpleNamespace(prefixes=frozenset())
    routes.bgp_policies = [
        {"connection": "home", "remote_prefixes": ["192.168.0.0/24"]},
        {"connection": "uk", "remote_prefixes": ["10.80.1.0/24"]},
    ]
    routes.connection_prefixes = {"home": {"192.168.0.0/24"}, "uk": {"10.80.1.0/24"}}
    routes.connection_sessions = {
        "home": frozenset({"169.254.1.2"}),
        "uk": frozenset({"169.254.2.2"}),
    }
    routes.expected_import_policy_digest = "import-policy"
    observed = LocalDataPlaneObservation(
        service_healthy=True,
        forwarding_enabled=True,
        static_prefixes=frozenset(),
        configured_bgp_sessions=frozenset({"169.254.1.2", "169.254.2.2"}),
        established_bgp_sessions=frozenset({"169.254.1.2"}),
        learned_bgp_prefixes=frozenset({"192.168.0.0/24"}),
        usable_xfrm_prefixes=frozenset({"192.168.0.0/24"}),
        observed_bgp_policy_digest="import-policy",
        bgp_export_state=BGPExportState.UNKNOWN,
        established_bgp_exports_ready=True,
    )
    readiness = routes.readiness_from_observation(observed, DataPlaneMode.ACTIVE)
    assert readiness.observation_available
    assert not readiness.promotion_ready
    assert readiness.forwarding_ready
    assert readiness.degraded_connections == ("uk",)
    # An observed policy/export violation never becomes partial serving readiness.
    unsafe = routes.readiness_from_observation(
        replace(observed, bgp_export_state=BGPExportState.DRIFT), DataPlaneMode.ACTIVE
    )
    assert not unsafe.partial_path_available
    assert not unsafe.forwarding_ready
    unavailable = routes.readiness_from_observation(
        replace(observed, established_bgp_exports_ready=False), DataPlaneMode.ACTIVE
    )
    assert not unavailable.observation_available


def test_deferring_reload_does_not_accept_a_different_generation_or_boot(tmp_path: Path) -> None:
    runtime, _now, _mode, health, _cloud, _failures, _effects, _guards = owner_runtime(tmp_path)
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    runtime.step()
    boot_path = tmp_path / "boot"
    boot_path.write_text("boot-a")
    cfg = {"vm_ha": {"enabled": True, "generation": {"generation_id": "wrong-generation"}}}
    assert not vm_ha_reload_deferred(
        cfg, status_path=tmp_path / "status.json", boot_id_path=boot_path
    )
    cfg["vm_ha"]["generation"]["generation_id"] = "a" * 64
    boot_path.write_text("next-boot")
    assert not vm_ha_reload_deferred(
        cfg, status_path=tmp_path / "status.json", boot_id_path=boot_path
    )


def test_systemctl_query_error_is_unknown_and_does_not_reuse_old_service_failure(
    tmp_path: Path,
) -> None:
    data_plane = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="policy",
        runner=lambda _argv, _timeout: CommandResult(1, "cannot query service manager"),
    )
    data_plane.last_service_health = False
    routes = object.__new__(BoundRouteRuntime)
    routes.data_plane = data_plane
    readiness = routes.readiness()
    assert not readiness.observation_available
    assert data_plane.last_service_health is None


def test_apply_lock_overrides_local_observation_grace(tmp_path: Path) -> None:
    runtime, _now, _mode, health, _cloud, _failures, effects, _guards = owner_runtime(tmp_path)
    runtime.step()
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    snapshot = runtime.snapshots.observe()
    snapshot = replace(snapshot, apply_locked=True)
    decision = runtime.controller.policy.decide(snapshot, runtime.controller.checkpoints.load())
    assert decision.action.kind is ActionKind.DISABLE_ACTIVE
    assert not effects


def test_ordinary_agent_defers_reload_without_touching_healthy_services_during_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _now, _mode, health, _cloud, _failures, effects, guards = owner_runtime(tmp_path)
    health[0] = LocalReadiness(False, False, False, False, observation_available=False)
    runtime.step()
    cfg = {"vm_ha": _runtime_config(), "connections": []}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    (tmp_path / "boot").write_text("boot-a")
    paths = {
        "status_path": tmp_path / "status.json",
        "guard_path": tmp_path / "guard.json",
        "boot_id_path": tmp_path / "boot",
    }
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "agent-state.json")
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: require_vm_ha_current_boot_ready(cfg, **paths),
    )
    monkeypatch.setattr(
        agent_main, "vm_ha_reload_deferred", lambda config: vm_ha_reload_deferred(config, **paths)
    )
    agent = agent_main.Agent()
    agent.ss = Mock()
    agent.frr = Mock()
    agent.xfrm = Mock()
    agent.reload()
    agent.ss.render_and_apply.assert_not_called()
    agent.frr.render_and_apply.assert_not_called()
    assert not effects
    assert not guards
