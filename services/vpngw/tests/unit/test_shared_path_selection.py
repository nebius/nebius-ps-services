"""Shared path policy with independent ordinary and HA authority boundaries."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from dataclasses import asdict

import pytest
import yaml
from tests.unit.test_ha_tunnel_selection import AUTH, Effects, config

from nebius_vpngw import ordinary_operations as operations
from nebius_vpngw.agent import ordinary_selection, strongswan_renderer
from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import (
    Health,
    OrdinaryAuthority,
    TunnelController,
    effective_config,
    read_state,
)
from nebius_vpngw.ha_tunnel_policy import connection_policies


@pytest.mark.parametrize("ha", [False, True])
def test_owner_alternative_is_loaded_without_initiation_or_workload_routes(ha):
    cfg = config()
    if not ha:
        del cfg["vm_ha"]
    cfg["_ha_tunnel_selections"] = {"site": "backup"}
    tunnels, secrets, endpoints = strongswan_renderer.collect_tunnel_state(cfg)
    text = strongswan_renderer._swanctl_content(tunnels, secrets, activate=True)
    preferred = text.split("  preferred {", 1)[1].split("  backup {", 1)[0]
    assert "start_action = none" in preferred
    assert "dpd_action = clear" in preferred
    assert "close_action = none" in preferred
    assert endpoints[0]["remote_prefixes"] == []
    assert endpoints[1]["remote_prefixes"] == ["10.1.0.0/16"]
    if ha:
        cfg["_ha_static_passive"] = True
        tunnels, secrets, _ = strongswan_renderer.collect_tunnel_state(cfg)
        fenced = strongswan_renderer._swanctl_content(tunnels, secrets, activate=True)
        assert "  preferred {" not in fenced and "  backup {" not in fenced


def test_overlap_between_connections_fails_before_effects_but_disjoint_remains():
    cfg = config()
    other = deepcopy(cfg["connections"][0])
    other["name"] = "other"
    other["remote_prefixes"] = ["10.1.1.0/24"]
    cfg["connections"].append(other)
    with pytest.raises(ValueError, match="Overlapping destination ownership"):
        connection_policies(cfg)
    other["remote_prefixes"] = ["10.2.0.0/16"]
    assert len(connection_policies(cfg)) == 2


def backup(tmp_path, *, ordinary=False):
    cfg = config()
    bound = AUTH
    if ordinary:
        del cfg["vm_ha"]
        bound = OrdinaryAuthority("boot", "a" * 64)
    effects = Effects()
    effects.current = bound
    observations = {}
    controller = TunnelController(
        cfg,
        tmp_path / "tunnel-selection.json",
        effects,
        preferred_probe=effects.probe_preferred,
        preferred_observations=observations,
    )
    controller.bind(bound)
    controller.state["selected"] = {"site": "backup"}
    controller.state["reasons"] = {"site": "automatic"}
    controller._save()
    return controller, effects, bound, observations


@pytest.mark.parametrize("ordinary", [False, True])
def test_preferred_requires_sixty_seconds_and_unknown_restarts_window(tmp_path, ordinary):
    controller, effects, bound, _ = backup(tmp_path, ordinary=ordinary)
    for now in range(0, 60, 5):
        controller.tick(bound, now)
        assert controller.state["operation"] is None
    effects.health["preferred"] = Health.UNKNOWN
    controller.tick(bound, 60)
    effects.health["preferred"] = Health.HEALTHY
    for now in range(65, 125, 5):
        controller.tick(bound, now)
        assert controller.state["operation"] is None
    controller.tick(bound, 125)
    assert controller.state["operation"]["verb"] == "automatic-failback"
    assert controller.state["selected"] == {"site": "backup"}
    assert not any(call[0] == "activate" for call in effects.calls)


def test_restart_gap_or_manual_override_cannot_reuse_preferred_health(tmp_path):
    controller, effects, bound, _ = backup(tmp_path)
    for now in range(0, 60, 5):
        controller.tick(bound, now)
    restarted = TunnelController(
        controller.cfg,
        controller.path,
        effects,
        preferred_probe=effects.probe_preferred,
    )
    restarted.tick(bound, 60)
    assert restarted.state["operation"] is None
    restarted.tick(bound, 120)
    assert restarted.state["operation"] is None
    restarted.state["reasons"]["site"] = "failover"
    for now in range(125, 195, 5):
        restarted.tick(bound, now)
    assert restarted.state["operation"] is None
    assert restarted.preferred_observations == {}


def test_ordinary_binding_rejects_old_boot_and_config(tmp_path):
    controller, _, bound, _ = backup(tmp_path, ordinary=True)
    persisted = read_state(controller.path)
    assert persisted["authority"] == asdict(bound)
    assert effective_config(controller.cfg, persisted, bound)["_ha_tunnel_selections"] == {
        "site": "backup"
    }
    for changed in (
        OrdinaryAuthority("next-boot", bound.generation),
        OrdinaryAuthority(bound.boot_id, "b" * 64),
    ):
        assert effective_config(controller.cfg, persisted, changed)["_ha_tunnel_selections"] == {}


def test_ordinary_adapter_holds_guest_writer_lock_and_revalidates_config(tmp_path, monkeypatch):
    cfg = config()
    del cfg["vm_ha"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    boot = tmp_path / "boot"
    boot.write_text("boot")
    monkeypatch.setattr(operations, "BOOT", boot)
    monkeypatch.setattr(operations, "JOURNAL", tmp_path / "operation.json")
    monkeypatch.setattr(operations, "ROUTING_LOCK", tmp_path / "routing.lock")
    seen = []

    def effects_factory(_cfg, _state, revalidate, **kwargs):
        effects = Effects()
        effects.current = ordinary_selection.authority(cfg, "boot")
        assert kwargs["routing_lock_held"]
        with pytest.raises(BlockingIOError), operations.mutation_lock():
            pass
        assert revalidate(effects.current)
        path.write_text(yaml.safe_dump({**cfg, "gateway": {"changed": True}}))
        assert not revalidate(effects.current)
        effects.revalidate = revalidate
        seen.append(effects)
        return effects

    monkeypatch.setattr(ordinary_selection, "NativeTunnelEffects", effects_factory)
    result = ordinary_selection.OrdinarySelectionRuntime(path, tmp_path / "state").step()
    assert result["authority"] is None
    assert seen[0].calls == []


@pytest.mark.parametrize(
    "result_code,expected", [(0, Health.HEALTHY), (1, Health.DOWN), (2, Health.UNKNOWN)]
)
@pytest.mark.parametrize("lose_authority", [False, True])
@pytest.mark.parametrize("ordinary", [False, True])
def test_preferred_probe_uses_configured_encrypted_target(
    tmp_path, monkeypatch, result_code, expected, lose_authority, ordinary
):
    cfg = config()
    cfg["gateway"]["local_prefixes"] = ["10.9.0.0/16"]
    cfg["gateway"]["tunnel_probes"] = {
        "preferred": {
            "source_ip": "10.9.0.1",
            "target_ip": "10.1.0.1",
            "failure_threshold": 1,
            "recovery_threshold": 1,
        }
    }
    if ordinary:
        del cfg["vm_ha"]
    loader = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", loader)
    tunnels, secrets, _ = strongswan_renderer.collect_tunnel_state(cfg)
    loader.write_text(strongswan_renderer._swanctl_content(tunnels, secrets, activate=True))
    loader.chmod(0o600)
    commands = []
    current = True

    def run(argv, timeout):
        nonlocal current
        commands.append(tuple(argv))
        if argv[0] == "ip":
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"addr_info": [{"local": "10.9.0.1"}]}]), ""
            )
        assert argv == (
            "ping",
            "-n",
            "-c",
            "1",
            "-I",
            "xfrm0",
            "-I",
            "10.9.0.1",
            "-W",
            "1",
            "10.1.0.1",
        )
        current = not lose_authority
        return subprocess.CompletedProcess(argv, result_code, "", "")

    effects = NativeTunnelEffects(
        cfg,
        tmp_path,
        lambda a: current and a == AUTH,
        active_revalidate=lambda a: a == AUTH,
        routing_lock_held=True,
        command_runner=run,
    )
    monkeypatch.setattr(effects, "_effective", lambda a: cfg)
    monkeypatch.setattr(effects, "_path_health", lambda *a, **kw: Health.HEALTHY)
    assert effects.probe_preferred(connection_policies(cfg)[0], "preferred", AUTH) is (
        Health.UNKNOWN if lose_authority else expected
    )
    assert commands[-1][-1] == "10.1.0.1"
