"""Restart must separate durable tunnel intent from forwarding authority."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_ha_tunnel_selection import AUTH, Effects, config, native_bgp_effects
from tests.unit.test_vm_ha_controller import _owned_cloud, _peer, _snapshot

from nebius_vpngw.agent.main import VMHACheckpointFileStore
from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
from nebius_vpngw.agent.vm_ha.tunnels import Authority, Health, TunnelController, read_state
from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode


def restart_runtime(tmp_path, mode=DataPlaneMode.PASSIVE):
    cfg = config("bgp")
    cfg["vm_ha"].update(
        node={"node_id": "node-b"},
        runtime_binding={"shared_allocation_id": "allocation"},
        generation={"generation_id": "a" * 64},
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    checkpoint = VMHACheckpointFileStore(tmp_path / "controller-checkpoint.json")
    authority = Authority("cluster", "node-b", "allocation", "42", 0, "a" * 64)
    snapshot = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=mode,
        peer_heartbeat=replace(_peer(), tunnel_capability=True),
        peer_received_at=100.0,
    )
    snapshots = SimpleNamespace(
        observe=lambda: snapshot,
        last_snapshot=snapshot,
        providers=SimpleNamespace(apply_lock_operation_id=lambda: None),
    )
    runtime = ControllerTunnelRuntime(
        path,
        tmp_path,
        SimpleNamespace(controller=SimpleNamespace(checkpoints=checkpoint), snapshots=snapshots),
        SimpleNamespace(
            routes=SimpleNamespace(),
            data_plane=SimpleNamespace(mode=lambda: snapshot.data_plane_mode),
        ),
        clock=lambda: 100.0,
    )
    effects = Effects()
    effects.current = authority
    runtime.effects = effects
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    selector.request("failover", "backup", authority, 99.0)
    return runtime, selector, effects, authority


@pytest.mark.parametrize("phase", ["activate", "verify"])
@pytest.mark.parametrize("mode", [DataPlaneMode.PASSIVE, DataPlaneMode.ORIGINATING])
def test_pending_bgp_cannot_advance_while_fenced(tmp_path, phase, mode):
    runtime, selector, effects, _ = restart_runtime(tmp_path, mode)
    selector.state["selected"] = {"site": "backup"}
    selector.state["operation"].update(phase=phase, successes=1)
    selector._save()
    deadline = selector.state["operation"]["deadline"]
    runtime.step()
    state = read_state(selector.path)
    assert effects.calls == []
    assert state["operation"]["phase"] == phase
    assert state["operation"]["successes"] == 0
    assert state["operation"]["deadline"] == deadline


@pytest.mark.parametrize("health", [Health.DOWN, Health.UNKNOWN, Health.HEALTHY])
def test_origination_reconsiders_failed_selection_without_native_effects(tmp_path, health):
    runtime, selector, effects, _ = restart_runtime(tmp_path, DataPlaneMode.ORIGINATING)
    selector.state["selected"] = {"site": "backup"}
    selector._save()
    before = deepcopy(selector.state["operation"])
    effects.health["backup"] = health
    runtime.step()
    state = read_state(selector.path)
    assert state["selected"] == ({} if health is Health.DOWN else {"site": "backup"})
    assert state["operation"]["verb"] == ("release" if health is Health.DOWN else "failover")
    assert all(
        state["operation"][key] == before[key] for key in ("id", "deadline", "attempt_deadline")
    )
    assert effects.calls == []
    assert runtime.authority() is None


@pytest.mark.parametrize(
    "condition",
    ["apply-lock", "inhibited", "attachment", "unknown-cloud", "stale-peer", "generation", "boot"],
)
def test_origination_intent_recovery_requires_current_authority(tmp_path, condition):
    runtime, selector, effects, _ = restart_runtime(tmp_path, DataPlaneMode.ORIGINATING)
    selector.state["selected"] = {"site": "backup"}
    selector._save()
    effects.health["backup"] = Health.DOWN
    snapshot = runtime.controller.snapshots.last_snapshot
    changes = {
        "apply-lock": {"apply_locked": True},
        "inhibited": {"transfer_inhibited": True},
        "attachment": {"cloud": replace(snapshot.cloud, candidate_attachment_exact=False)},
        "unknown-cloud": {"cloud": replace(snapshot.cloud, authoritative=False)},
        "stale-peer": {"peer_received_at": 1.0},
        "generation": {"local_generation_id": "b" * 64},
        "boot": {"guard_boot_id": "old-boot"},
    }
    runtime.controller.snapshots.observe = lambda: replace(snapshot, **changes[condition])
    before = selector.path.read_bytes()
    runtime.step()
    assert selector.path.read_bytes() == before
    assert effects.calls == []


@pytest.mark.parametrize("phase", ["activate", "verify", "succeeded"])
@pytest.mark.parametrize("health", [Health.HEALTHY, Health.DOWN, Health.UNKNOWN])
def test_fenced_preparation_preserves_or_releases_exact_selection(tmp_path, phase, health):
    runtime, selector, effects, authority = restart_runtime(tmp_path)
    selector.state["selected"] = {"site": "backup"}
    selector.state["operation"]["phase"] = phase
    selector._save()
    effects.health["backup"] = health
    before = deepcopy(selector.state["operation"])
    selector.prepare_bgp_resume(authority, 100.0, revalidate=lambda: True)
    assert effects.calls == []
    assert selector.state["selected"] == ({} if health is Health.DOWN else {"site": "backup"})
    op = selector.state["operation"]
    assert all(
        op[k] == before[k]
        for k in ("id", "requested_verb", "requested_target", "deadline", "attempt_deadline")
    )
    if phase != "succeeded" and health is Health.DOWN:
        assert op["verb"] == "release" and op["phase"] == "activate"
        assert op["result"] == "target-failed-restoring-policy"


def test_historical_deadline_does_not_expire_completed_healthy_override(tmp_path):
    _, selector, _, authority = restart_runtime(tmp_path)
    selector.state["selected"] = {"site": "backup"}
    selector.state["operation"].update(phase="succeeded", result="verified")
    selector._save()
    selector.prepare_bgp_resume(authority, 10000.0, revalidate=lambda: True)
    assert selector.state["selected"] == {"site": "backup"}
    assert selector.state["operation"]["phase"] == "succeeded"


@pytest.mark.parametrize("health", [Health.DOWN, Health.UNKNOWN])
def test_failback_waits_for_preferred_evidence_then_expired_cleanup(tmp_path, health):
    _, selector, effects, authority = restart_runtime(tmp_path)
    selector.state["selected"] = {"site": "backup"}
    selector.state["operation"].update(
        verb="failback", requested_verb="failback", target="preferred"
    )
    selector._save()
    effects.health["preferred"] = health
    selector.prepare_bgp_resume(authority, 100, revalidate=lambda: True)
    assert selector.state["selected"] == {"site": "backup"}
    selector.prepare_bgp_resume(authority, 130, revalidate=lambda: True)
    assert selector.state["selected"] == {}
    assert selector.state["operation"]["verb"] == "release"
    assert selector.state["operation"]["phase"] == "activate"
    assert effects.calls == []


@pytest.mark.parametrize(
    "received, expected",
    [
        ('{"routes":{"10.1.0.0/16":[{"valid":true}]}}', Health.HEALTHY),
        ('{"routes":{}}', Health.DOWN),
        ("{}", Health.UNKNOWN),
        ('{"routes":[]}', Health.UNKNOWN),
        ('{"routes":{"10.1.0.0/16":null}}', Health.UNKNOWN),
        ('{"routes":{"10.1.0.0/16":[]}}', Health.UNKNOWN),
        ('{"routes":{"10.1.0.0/16":[{}]}}', Health.UNKNOWN),
    ],
)
def test_candidate_uses_strict_received_coverage_without_exports(tmp_path, received, expected):
    effects, policy, responses = native_bgp_effects(tmp_path)
    peer = policy.paths[0].peer
    responses[("vtysh", "-c", f"show bgp ipv4 unicast neighbors {peer} routes json")] = received
    responses[("vtysh", "-c", f"show bgp ipv4 unicast neighbors {peer} advertised-routes json")] = (
        '{"advertisedRoutes":{},"totalPrefixCounter":0}'
    )
    assert effects.candidate(policy, "preferred") is expected


def test_autonomous_release_invalidates_cached_command_success(tmp_path):
    from tests.unit.test_ha_tunnel_selection import request_config, request_payload

    from nebius_vpngw.agent.vm_ha.tunnel_requests import submit_request

    cfg = request_config()
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", Effects())
    selector.request("failover", "backup", AUTH, 100)
    for now in range(101, 105):
        selector.tick(AUTH, now)
    old = selector.state["operation"]["id"]
    selector.state["selected"].clear()
    selector._save()
    result = submit_request(cfg, tmp_path, request_payload(), now=106)
    assert result["result"] == "outstanding"
    assert result["operation_id"] != old


def test_preparation_rechecks_authority_after_observations_before_persistence(tmp_path):
    _, selector, effects, authority = restart_runtime(tmp_path)
    selector.state["selected"] = {"site": "backup"}
    selector._save()
    before = selector.path.read_bytes()
    effects.health["backup"] = Health.DOWN
    selector.prepare_bgp_resume(authority, 100, revalidate=lambda: False)
    assert selector.path.read_bytes() == before
    assert selector.state == read_state(selector.path)


def test_multiple_failed_overrides_preserve_unrelated_static_operation_and_episode(tmp_path):
    cfg = config("static")
    for index in (1, 2):
        connection = deepcopy(config("bgp")["connections"][0])
        connection["name"] = f"bgp{index}"
        connection["remote_prefixes"] = [f"10.{index + 1}.0.0/16"]
        for position, tunnel in enumerate(connection["tunnels"]):
            tunnel["name"] += str(index)
            tunnel["remote_public_ip"] = f"198.51.100.{index * 2 + position}"
        cfg["connections"].append(connection)
    effects = Effects()
    selector = TunnelController(cfg, tmp_path / "state", effects)
    selector.request("failover", "backup", AUTH, 100)
    selector.state["selected"].update(bgp1="backup1", bgp2="backup2")
    effects.health.update(backup1=Health.DOWN, backup2=Health.DOWN)
    selector._save()
    before = deepcopy(selector.state)
    selector.prepare_bgp_resume(AUTH, 101, revalidate=lambda: True)
    assert selector.state["selected"] == {}
    assert selector.state["operation"] == before["operation"]
    assert selector.state["episode"] == before["episode"]
    assert effects.calls == []


@pytest.mark.parametrize("change", ["incarnation", "generation", "allocation", "owner"])
def test_fenced_preparation_never_rebinds_stale_intent(tmp_path, change):
    _, selector, effects, authority = restart_runtime(tmp_path)
    before = selector.path.read_bytes()
    value = 1 if change == "incarnation" else "different"
    selector.prepare_bgp_resume(replace(authority, **{change: value}), 100, revalidate=lambda: True)
    assert selector.path.read_bytes() == before
    assert effects.calls == []


def test_forwarding_revocation_during_verification_cannot_complete_operation(tmp_path):
    effects = Effects()
    selector = TunnelController(config("bgp"), tmp_path / "state", effects)
    selector.request("failover", "backup", AUTH, 100)
    selector.state["operation"].update(phase="verify", successes=1)
    active = iter((True, False))
    effects.active = lambda authority: next(active)
    selector.tick(AUTH, 101)
    assert selector.state["operation"]["phase"] == "verify"
    assert selector.state["operation"]["successes"] == 0


@pytest.mark.parametrize("condition", ["failed", "missing", "unknown", "healthy", "path-down"])
def test_path_selection_defers_component_defect_to_recovery_owner(tmp_path, condition):
    runtime, _, _, _ = restart_runtime(tmp_path)
    snapshot = runtime.controller.snapshots.last_snapshot
    health = replace(
        snapshot.readiness,
        service_healthy=condition not in {"failed", "missing"},
        sample_complete=condition != "unknown",
        local_defects=("daemon-configuration-missing:strongswan",)
        if condition == "missing"
        else (),
        static_ready=condition != "path-down",
    )
    runtime.controller.snapshots.observe = lambda: replace(
        snapshot, data_plane_mode=DataPlaneMode.ACTIVE, readiness=health
    )
    assert (runtime.authority() is not None) is (condition in {"healthy", "path-down"})


@pytest.mark.parametrize("credential_failure", [False, True])
def test_native_policy_change_loads_credentials_before_initiation(
    tmp_path, monkeypatch, credential_failure
):
    from nebius_vpngw.agent.vm_ha import tunnel_runtime

    path = tmp_path / "swanctl.conf"
    monkeypatch.setattr(tunnel_runtime, "SWANCTL_CONF", path)

    def render(self, cfg, *, rendered_files):
        rendered_files[path] = "fixture"

    monkeypatch.setattr(tunnel_runtime.StrongSwanRenderer, "render_and_apply", render)
    effects = tunnel_runtime.NativeTunnelEffects(
        config("static"), tmp_path, lambda authority: True, active_revalidate=lambda authority: True
    )
    calls = []

    def run(argv):
        calls.append(tuple(argv))
        if credential_failure and "--load-all" in argv:
            raise RuntimeError("credential load incomplete")
        return ""

    effects.run = run
    if credential_failure:
        with pytest.raises(RuntimeError, match="incomplete"):
            effects._swan_config(effects.cfg)
    else:
        effects._swan_config(effects.cfg)
    assert calls == [("swanctl", "--load-all", "--noprompt")]
