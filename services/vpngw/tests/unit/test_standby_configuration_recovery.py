"""A restarted warm daemon recovers locally without borrowing owner authority."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.unit.test_vm_ha_controller import _cloud, _peer, _snapshot

from nebius_vpngw.agent.vm_ha.models import HeartbeatEvidence
from nebius_vpngw.agent.vm_ha_checkpoint import (
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    VMHAController,
)


def standby_snapshot(**changes):
    peer = replace(
        _peer(),
        authority_ready=True,
        authority_valid_until=1000,
        evidence=HeartbeatEvidence(state="healthy", cloud_status="complete", cloud_age_seconds=0),
    )
    snapshot = _snapshot(
        cloud=replace(_cloud(), former_attachment_exact=True, candidate_attachment_absent=True),
        peer_heartbeat=peer,
        readiness=LocalReadiness(
            False,
            True,
            False,
            False,
            protection_ready=True,
            local_defects=("daemon-configuration-missing:strongswan",),
        ),
    )
    return replace(snapshot, **changes)


def test_warm_standby_missing_configuration_admits_one_local_repair():
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    result = controller.decide(standby_snapshot(), ControllerCheckpoint())
    assert result.action is not None
    assert result.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert result.action.repair_mode is DataPlaneMode.PASSIVE
    assert result.checkpoint.repair_attempt.repair_mode is DataPlaneMode.PASSIVE
    assert not result.forwarding_enabled


@pytest.mark.parametrize("protection_ready", [False, True])
def test_deferred_standby_repair_requires_its_global_fence_not_owner_destination_guard(
    protection_ready,
):
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    snapshot = standby_snapshot()
    snapshot = replace(
        snapshot, readiness=replace(snapshot.readiness, protection_ready=protection_ready)
    )
    first = controller.decide(snapshot, ControllerCheckpoint())
    replay = controller.decide(snapshot, first.checkpoint)
    assert replay.action == first.action
    assert replay.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert not replay.forwarding_enabled
    assert replay.checkpoint.repair_attempt == first.checkpoint.repair_attempt


def test_standby_attempt_survives_completion_restart_and_exhaustion():
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    first = controller.decide(standby_snapshot(), ControllerCheckpoint())
    checkpoint = controller_checkpoint_from_dict(controller_checkpoint_to_dict(first.checkpoint))
    waiting = controller.decide(
        standby_snapshot(now=102, completed_effect_operation_id=first.action.operation_id),
        checkpoint,
    )
    assert waiting.action is None and waiting.state is HAState.REPAIRING
    exhausted = controller.decide(
        standby_snapshot(now=120, peer_received_at=119), waiting.checkpoint
    )
    assert exhausted.state is HAState.REPAIR_EXHAUSTED
    assert exhausted.action is None and not exhausted.forwarding_enabled
    restored = controller_checkpoint_from_dict(controller_checkpoint_to_dict(exhausted.checkpoint))
    again = controller.decide(standby_snapshot(now=180, peer_received_at=179), restored)
    assert again.action is None and again.checkpoint.repair_attempt == restored.repair_attempt


def test_two_healthy_samples_confirm_and_sixty_seconds_reset_standby_attempt():
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    first = controller.decide(standby_snapshot(), ControllerCheckpoint())
    health = LocalReadiness(True, True, True, True, protection_ready=True)
    checkpoint = first.checkpoint
    for now, expected_state, count in [(102, HAState.REPAIRING, 1), (104, HAState.NORMAL, 2)]:
        result = controller.decide(
            standby_snapshot(
                now=now,
                peer_received_at=now - 1,
                readiness=health,
                completed_effect_operation_id=first.action.operation_id,
            ),
            checkpoint,
        )
        assert result.state is expected_state and result.action is None
        assert not result.forwarding_enabled
        assert result.checkpoint.repair_attempt.healthy_observations == count
        checkpoint = result.checkpoint
    result = controller.decide(
        standby_snapshot(now=162, peer_received_at=161, readiness=health), checkpoint
    )
    assert result.checkpoint.repair_attempt is None


@pytest.mark.parametrize(
    "case",
    [
        "unknown-cloud",
        "local-attachment",
        "missing-owner-attachment",
        "stale-peer",
        "peer-unhealthy",
        "apply",
        "maintenance",
        "incomplete",
        "coverage-only",
    ],
)
def test_standby_reload_rejects_unproven_or_inhibited_authority(case):
    snapshot = standby_snapshot()
    if case == "unknown-cloud":
        snapshot = replace(snapshot, cloud=replace(snapshot.cloud, authoritative=False))
    elif case == "local-attachment":
        snapshot = replace(
            snapshot, cloud=replace(snapshot.cloud, candidate_attachment_absent=False)
        )
    elif case == "missing-owner-attachment":
        snapshot = replace(snapshot, cloud=replace(snapshot.cloud, former_attachment_exact=False))
    elif case == "stale-peer":
        snapshot = replace(snapshot, peer_received_at=1)
    elif case == "peer-unhealthy":
        snapshot = replace(
            snapshot, peer_heartbeat=replace(snapshot.peer_heartbeat, service_healthy=False)
        )
    elif case == "apply":
        snapshot = replace(snapshot, apply_locked=True)
    elif case == "maintenance":
        snapshot = replace(snapshot, transfer_inhibited=True)
    elif case == "incomplete":
        snapshot = replace(snapshot, readiness=replace(snapshot.readiness, sample_complete=False))
    elif case == "coverage-only":
        snapshot = replace(
            snapshot, readiness=replace(snapshot.readiness, service_healthy=True, local_defects=())
        )
    result = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5).decide(
        snapshot, ControllerCheckpoint()
    )
    assert result.action is None or result.action.kind is not ActionKind.REPAIR_LOCAL_DATAPLANE
    assert result.checkpoint.repair_attempt is None
    assert not result.forwarding_enabled


def test_production_tunnel_provider_proves_only_warm_canonical_policy(
    tmp_path, monkeypatch, sample_config
):
    import yaml

    from nebius_vpngw.agent import strongswan_renderer
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = deepcopy(sample_config)
    warm = deepcopy(cfg["connections"][0])
    warm.update(name="warm", routing_mode="bgp", remote_prefixes=["172.20.0.0/24"])
    warm["tunnels"][0]["name"] = "warm-path"
    cfg["connections"].append(warm)
    cfg["vm_ha"] = {"cluster_id": "fixture"}
    path = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", path)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    data_plane = SimpleNamespace(mode=lambda: DataPlaneMode.PASSIVE)
    ports = SimpleNamespace(data_plane=data_plane, routes=SimpleNamespace())
    ControllerTunnelRuntime(config, tmp_path, SimpleNamespace(), ports)
    files = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(
        {**cfg, "_ha_static_passive": True}, rendered_files=files
    )
    strongswan_renderer._write_secret_file(path, files[path])
    assert data_plane.ipsec_configuration_provider().connections == frozenset({"warm-path"})
    # An active file on a passive member must never grant cold-path authority.
    strongswan_renderer.StrongSwanRenderer().render_and_apply(cfg, rendered_files=files)
    strongswan_renderer._write_secret_file(path, files[path])
    with pytest.raises(RuntimeError, match="IPsec loader policy"):
        data_plane.ipsec_configuration_provider()


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("case", ["cold", "prepared", "drift", "permissions", "symlink"])
def test_fenced_static_preparation_is_observable_without_granting_reload(
    tmp_path, monkeypatch, sample_config, mixed, case
):
    import yaml

    from nebius_vpngw.agent import strongswan_renderer
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = deepcopy(sample_config)
    if mixed:
        warm = deepcopy(cfg["connections"][0])
        warm.update(name="warm", routing_mode="bgp", remote_prefixes=["172.20.0.0/24"])
        warm["tunnels"][0]["name"] = "warm-path"
        cfg["connections"].append(warm)
    cfg["vm_ha"] = {"cluster_id": "fixture"}
    path = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", path)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    data_plane = SimpleNamespace(mode=lambda: DataPlaneMode.PASSIVE)
    ControllerTunnelRuntime(
        config, tmp_path, None, SimpleNamespace(data_plane=data_plane, routes=SimpleNamespace())
    )
    files = {}
    rendered = {**cfg, "_ha_static_passive": True} if case == "cold" else cfg
    strongswan_renderer.StrongSwanRenderer().render_and_apply(rendered, rendered_files=files)
    strongswan_renderer._write_secret_file(path, files[path])
    if case == "drift":
        path.write_text(files[path] + "# unrelated policy\n")
    elif case == "permissions":
        path.chmod(0o644)
    elif case == "symlink":
        target = tmp_path / "other"
        path.rename(target)
        path.symlink_to(target)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    if case in {"drift", "permissions", "symlink"}:
        with pytest.raises(RuntimeError, match="IPsec loader policy"):
            data_plane.ipsec_configuration_observer()
    else:
        # Prepared owner policy is a transitional observation. The controller
        # must still prove native coverage and cloud ownership before promoting.
        expected = (frozenset({"warm-path"}) if mixed else frozenset()) if case == "cold" else None
        observed = data_plane.ipsec_configuration_observer()
        assert (observed.connections if observed is not None else None) == expected
    if case != "cold":
        with pytest.raises(RuntimeError, match="IPsec loader policy"):
            data_plane.ipsec_configuration_provider()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


def test_native_observation_does_not_borrow_fenced_candidate_reload_authority(
    tmp_path, monkeypatch, sample_config
):
    import json
    from pathlib import Path

    import yaml

    from nebius_vpngw.agent import strongswan_renderer
    from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime

    cfg = deepcopy(sample_config)
    cfg["vm_ha"] = {"cluster_id": "fixture"}
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    swanctl = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", swanctl)
    files = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(cfg, rendered_files=files)
    strongswan_renderer._write_secret_file(swanctl, files[swanctl])
    state = tmp_path / "data-plane.json"
    state.write_text(json.dumps({"schema": "nebius-vpngw/vm-ha-data-plane-v1", "mode": "passive"}))
    calls = []

    def runner(argv, timeout):
        calls.append(tuple(argv))
        command = Path(argv[0]).name
        assert 0 < timeout <= 5
        if command == "systemctl" and argv[1] == "show":
            return CommandResult(0, "active")
        if command == "sysctl" and argv[1] == "-n":
            return CommandResult(0, "0")
        if command == "vtysh":
            return CommandResult(0, "" if argv[-1] == "show running-config" else "{}")
        if command == "ip":
            return CommandResult(0, "[]" if "-j" in argv else "")
        raise AssertionError(argv)  # No mutation or VICI loader is admitted.

    plane = SystemDataPlaneRuntime(
        state_path=state,
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="a" * 64,
        runner=runner,
    )
    ControllerTunnelRuntime(
        config, tmp_path, None, SimpleNamespace(data_plane=plane, routes=SimpleNamespace())
    )
    observed = plane.observe()
    assert observed.service_healthy and not observed.ipsec_configuration_missing
    assert not observed.forwarding_enabled and not observed.usable_xfrm_prefixes
    with pytest.raises(RuntimeError, match="IPsec loader policy"):
        plane._missing_ipsec_configuration()
    assert calls
