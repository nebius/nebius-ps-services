"""Compose the VM policy and tunnel runtime across a late health observation."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_ha_tunnel_selection import Effects, config
from tests.unit.test_vm_ha_controller import _owned_cloud, _peer, _reconciled, _snapshot

from nebius_vpngw.agent.main import VMHACheckpointFileStore, VMHAControllerRuntime
from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
from nebius_vpngw.agent.vm_ha.tunnels import Authority, Health, read_state
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ConfiguredRole,
    DataPlaneMode,
    LocalReadiness,
)

pytestmark = pytest.mark.integration


def composed_runtime(tmp_path, monkeypatch):
    now = [100.0]
    cfg = config()
    cfg["vm_ha"].update(
        node={"node_id": "node-b"},
        runtime_binding={"shared_allocation_id": "allocation"},
        generation={"generation_id": "a" * 64},
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    checkpoints = VMHACheckpointFileStore(tmp_path / "controller-checkpoint.json")
    effects = Effects()
    effects.current = Authority("cluster", "node-b", "allocation", "42", 0, "a" * 64)
    vm_effects = []
    overrides = {}
    snapshots = SimpleNamespace(
        last_snapshot=None,
        providers=SimpleNamespace(
            apply_lock_operation_id=lambda: "f" * 64 if overrides.get("apply_locked") else None
        ),
    )

    def observe():
        episode = read_state(tmp_path / "tunnel-selection.json")["episode"]
        snapshot = _snapshot(
            now=now[0],
            configured_role=ConfiguredRole.ACTIVE,
            peer_heartbeat=replace(_peer(configured_role="passive"), tunnel_capability=True),
            peer_received_at=now[0],
            cloud=_owned_cloud(),
            data_plane_mode=DataPlaneMode.ACTIVE,
            readiness=LocalReadiness(
                True, tunnels.static_ready(), True, True, protection_ready=True
            ),
            tunnel_recovery_remaining=None if episode is None else episode["deadline"] - now[0],
        )
        snapshot = replace(snapshot, **overrides)
        snapshot = replace(snapshot, routes_reconciled_context=_reconciled(snapshot))
        snapshots.last_snapshot = snapshot
        return snapshot

    snapshots.observe = observe
    runtime = VMHAControllerRuntime(
        snapshots=snapshots,
        effects=SimpleNamespace(apply=vm_effects.append),
        checkpoints=checkpoints,
        status_path=tmp_path / "status.json",
        guard=lambda: {},
    )
    monkeypatch.setattr(runtime, "_write_status", lambda result: result)
    tunnels = ControllerTunnelRuntime(
        path,
        tmp_path,
        runtime,
        SimpleNamespace(routes=SimpleNamespace(), data_plane=SimpleNamespace()),
        clock=lambda: now[0],
        monotonic=lambda: now[0],
    )
    tunnels.effects = effects
    runtime.tunnel_runtime = tunnels
    runtime.tunnel_step = tunnels.step
    return runtime, tunnels, effects, vm_effects, now, overrides


def test_static_loss_after_tunnel_check_precedes_generic_owner_repair(tmp_path, monkeypatch):
    runtime, tunnels, effects, vm_effects, now, _ = composed_runtime(tmp_path, monkeypatch)

    def lose_path_after_check():
        tunnels.step()
        effects.health["preferred"] = Health.DOWN

    runtime.tunnel_step = lose_path_after_check
    result = runtime.step()
    assert result.reasons == ("native-path-recovery",)
    assert vm_effects == []
    assert runtime.controller.checkpoints.load().repair_attempt is None
    state = read_state(tmp_path / "tunnel-selection.json")
    assert state["operation"] is None
    deadline = state["episode"]["deadline"]
    assert deadline == now[0] + 30

    runtime.tunnel_step = tunnels.step
    for _ in range(29):
        now[0] += 1
        runtime.step()
    assert read_state(tmp_path / "tunnel-selection.json")["operation"] is None
    assert effects.calls == []
    for _ in range(12):
        now[0] += 1
        runtime.step()
    state = read_state(tmp_path / "tunnel-selection.json")
    assert state["operation"]["verb"] == "automatic-recovery"
    assert state["selected"] == {"site": "backup"}
    assert state["operation"]["phase"] == "succeeded"
    assert state["episode"]["deadline"] == deadline
    assert runtime.snapshots.last_snapshot.readiness.promotion_ready
    assert vm_effects == []


@pytest.mark.parametrize("health", [Health.UNKNOWN, Health.HEALTHY])
def test_unconfirmed_loss_does_not_create_tunnel_grace(tmp_path, monkeypatch, health):
    runtime, _, effects, vm_effects, _, overrides = composed_runtime(tmp_path, monkeypatch)
    effects.health["preferred"] = health
    if health is Health.HEALTHY:
        overrides["readiness"] = LocalReadiness(False, True, True, True, protection_ready=True)
    runtime.step()
    assert read_state(tmp_path / "tunnel-selection.json")["episode"] is None
    assert vm_effects == []


@pytest.mark.parametrize("inhibition", ["writer", "apply", "peer", "allocation"])
def test_late_loss_cannot_bypass_tunnel_authority(tmp_path, monkeypatch, inhibition):
    runtime, tunnels, effects, vm_effects, _, overrides = composed_runtime(tmp_path, monkeypatch)

    def lose_path_and_authority():
        tunnels.step()
        effects.health["preferred"] = Health.DOWN
        if inhibition == "writer":
            monkeypatch.setattr(
                "nebius_vpngw.agent.vm_ha_rearm._acquire_rearm_lock", lambda *a, **k: None
            )
        elif inhibition == "apply":
            overrides["apply_locked"] = True
        elif inhibition == "peer":
            overrides["peer_heartbeat"] = None
        else:
            overrides["cloud"] = replace(_owned_cloud(), candidate_attachment_exact=False)

    runtime.tunnel_step = lose_path_and_authority
    runtime.step()
    assert read_state(tmp_path / "tunnel-selection.json")["episode"] is None
    assert effects.calls == []
    if inhibition == "writer":
        assert vm_effects == []


def test_preflight_rechecks_apply_authority_before_vm_effects(tmp_path, monkeypatch):
    runtime, tunnels, effects, vm_effects, _, overrides = composed_runtime(tmp_path, monkeypatch)
    effects.health["preferred"] = Health.DOWN
    before_repair = tunnels.before_owner_repair

    def apply_starts_during_preflight():
        result = before_repair()
        overrides["apply_locked"] = True
        return result

    monkeypatch.setattr(tunnels, "before_owner_repair", apply_starts_during_preflight)
    runtime.step()
    assert [effect.kind for effect in vm_effects] == [ActionKind.DISABLE_ACTIVE]
    assert runtime.controller.checkpoints.load().repair_attempt is None


def test_late_loss_exhaustion_never_stacks_generic_repair(tmp_path, monkeypatch):
    runtime, tunnels, effects, vm_effects, now, _ = composed_runtime(tmp_path, monkeypatch)

    def lose_path_after_check():
        tunnels.step()
        effects.health.update(preferred=Health.DOWN, backup=Health.DOWN)

    runtime.tunnel_step = lose_path_after_check
    runtime.step()
    deadline = read_state(tmp_path / "tunnel-selection.json")["episode"]["deadline"]
    runtime.tunnel_step = tunnels.step
    now[0] = deadline
    runtime.step()
    state = read_state(tmp_path / "tunnel-selection.json")
    assert state["episode"]["exhausted"] and state["episode"]["deadline"] == deadline
    assert vm_effects == []
    assert runtime.controller.checkpoints.load().repair_attempt is None


def test_late_uk_backup_recovers_without_vm_repair_after_long_remote_outage(tmp_path, monkeypatch):
    runtime, tunnels, effects, vm_effects, now, _ = composed_runtime(tmp_path, monkeypatch)
    effects.health.update(preferred=Health.DOWN, backup=Health.DOWN)
    for _ in range(181):
        runtime.step()
        now[0] += 1
    original = read_state(tmp_path / "tunnel-selection.json")["episode"]
    assert original["exhausted"]
    assert vm_effects == []
    assert runtime.controller.checkpoints.load().repair_attempt is None
    effects.health["backup"] = Health.HEALTHY
    for _ in range(15):
        runtime.step()
        now[0] += 1
    state = read_state(tmp_path / "tunnel-selection.json")
    assert state["selected"] == {"site": "backup"}
    assert state["operation"]["result"] == "verified"
    assert state["episode"]["deadline"] == original["deadline"]
    assert runtime.snapshots.last_snapshot.readiness.promotion_ready
    assert vm_effects == []
    assert runtime.controller.checkpoints.load().repair_attempt is None
