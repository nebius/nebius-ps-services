from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_agent_blocked_render import _bgp_config
from tests.unit.test_vm_ha_controller import _owned_cloud, _peer, _snapshot
from tests.unit.test_vm_ha_runtime_ports import (
    FakeCredentialBundle,
    FakeDataPlane,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _runtime_config,
)

import nebius_vpngw.agent.frr_renderer as frr_module
import nebius_vpngw.agent.main as agent_main
import nebius_vpngw.agent.strongswan_renderer as strongswan_module
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import build_runtime_ports
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    DataPlaneMode,
    LocalReadiness,
    RouteReconciliationContext,
    VMHAController,
)

pytestmark = pytest.mark.integration


def test_two_node_takeover_effects_are_ordered_and_crash_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=lambda _binding, _node: FakeCredentialBundle(),
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    trace: list[str] = []
    completed: set[str] = set()
    crashed: set[str] = set()
    state = {"stopped": False, "detached": False, "attached": False, "routes": False}

    def once(name: str, effect) -> None:
        if name not in completed:
            effect()
            completed.add(name)
            trace.append(name)
        if name not in crashed:
            crashed.add(name)
            raise RuntimeError(f"crash-after-{name}")

    def observation() -> CloudObservation:
        return CloudObservation(
            authoritative=True,
            allocation_id="allocation-a",
            observed_owner_node_id="node-a" if state["attached"] else "node-b",
            former_owner_node_id="node-b",
            former_owner_compute_state=(
                ComputeState.STOPPED if state["stopped"] else ComputeState.RUNNING
            ),
            former_attachment_absent=state["detached"],
            candidate_attachment_exact=state["attached"],
            ownership_re_read_exact=state["attached"],
            ownership_epoch="7",
        )

    ports.cloud.observe = observation
    ports.cloud.stop_former = lambda _action: once(
        "stop", lambda: state.__setitem__("stopped", True)
    )
    ports.cloud.detach_former = lambda _action: once(
        "detach", lambda: state.__setitem__("detached", True)
    )
    ports.cloud.attach_candidate = lambda _action: once(
        "attach", lambda: state.__setitem__("attached", True)
    )
    ports.cloud.confirm_candidate = lambda _action: once("confirm", lambda: None)
    ports.routes.reconcile = lambda _action: once(
        "routes", lambda: state.__setitem__("routes", True)
    )
    ports.routes.readiness = lambda: LocalReadiness(True, True, True, True)
    ports.routes.receipt_context = lambda: RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=DigestSet(
            "a" * 64,
            ports.binding.static_routes_digest,
            ports.binding.bgp_policy_digest,
        ),
        route_runtime_id=ports.binding.route_runtime_id,
        ownership_incarnation=1,
        operation_id="routes-op",
    )
    original_enable = ports.data_plane.enable_active
    ports.data_plane.enable_active = lambda action: once("forward", lambda: original_enable(action))
    digests = DigestSet(
        "a" * 64,
        ports.binding.static_routes_digest,
        ports.binding.bgp_policy_digest,
    )
    actions = (
        ControllerAction(
            ActionKind.STOP_FORMER_OWNER,
            "stop-op",
            "boot",
            "node-b",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.DETACH_FORMER_ATTACHMENT,
            "detach-op",
            "boot",
            "node-b",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.ATTACH_CANDIDATE,
            "attach-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            "confirm-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.RECONCILE_ROUTES,
            "routes-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.ENABLE_ACTIVE,
            "enable-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
    )

    handlers = ports.handlers()
    for action in actions:
        with pytest.raises(RuntimeError, match="crash-after"):
            handlers[action.kind](action)
        handlers[action.kind](action)

    assert trace == ["stop", "detach", "attach", "confirm", "routes", "forward"]
    assert ports.data_plane.mode().value == "active"
    ports.close()


def test_clean_owner_bootstrap_materializes_passive_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production renderers cannot skip the passive bootstrap boundary."""

    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    blocked = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.BLOCKED,
        readiness=LocalReadiness(False, False, False, False),
    )
    first = policy.decide(blocked, ControllerCheckpoint())
    assert first.action is not None
    assert first.action.kind is ActionKind.ENTER_PASSIVE
    assert not first.forwarding_enabled

    config = _bgp_config()
    tunnel = config["connections"][0]["tunnels"][0]
    tunnel.update(remote_public_ip="192.0.2.10", psk="fixture-secret")
    vm_ha = {
        "node": {"node_id": "node-a"},
        "generation": {"generation_id": "a" * 64},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state_dir = tmp_path / "vm-ha"
    state_dir.mkdir()
    (state_dir / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        agent_main, "VM_HA_MATERIALIZATION_PATH", state_dir / "materialization.json"
    )
    lock_fd = os.open(state_dir / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **kwargs: lock_fd)
    monkeypatch.setattr(agent_main, "_boot_id", lambda: "boot-a")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda path=config_path: vm_ha)
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("passive")),
    )
    monkeypatch.setattr(
        agent_main,
        "_vm_ha_materialization_authorized",
        lambda: vm_ha,
    )
    passive_effects: list[str] = []
    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda cfg, **kwargs: passive_effects.append(
            f"firewall:{kwargs.get('require_reload')}"
        ),
    )

    def enforce_passive_hygiene(cfg):
        assert not (state_dir / "materialization.json").exists()
        passive_effects.append("hygiene")

    monkeypatch.setattr(
        agent_main,
        "enforce_vm_ha_passive_routing_hygiene_locked",
        enforce_passive_hygiene,
    )
    monkeypatch.setattr(
        agent_main,
        "enforce_routing_invariants",
        lambda cfg: (_ for _ in ()).throw(AssertionError("active routes escaped passive mode")),
    )

    monkeypatch.setattr(strongswan_module, "STRONGSWAN_CONF_DIR", tmp_path / "strongswan.d")
    monkeypatch.setattr(strongswan_module, "IPSEC_CONF", tmp_path / "ipsec.conf")
    monkeypatch.setattr(strongswan_module, "SWANCTL_CONF", tmp_path / "swanctl.conf")
    monkeypatch.setattr(strongswan_module, "NETPLAN_DIR", tmp_path / "netplan")
    monkeypatch.setattr(strongswan_module, "_wait_for_vici_socket", lambda: True)
    monkeypatch.setattr(
        strongswan_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(frr_module, "BGPD_CONF", tmp_path / "bgpd.conf")
    monkeypatch.setattr(frr_module, "FRR_CONF", tmp_path / "frr.conf")
    monkeypatch.setattr(
        frr_module.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=1234),
    )
    monkeypatch.setattr(frr_module.os, "chown", lambda *args: None)
    monkeypatch.setattr(
        frr_module.FRRRenderer, "_ensure_bgpd_enabled", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(frr_module.FRRRenderer, "ensure_local_prefix_routes", lambda *args: None)
    monkeypatch.setattr(
        frr_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    materialized_endpoints: list[dict[str, object]] = []
    agent = agent_main.Agent()
    agent.xfrm = SimpleNamespace(
        setup_interfaces=lambda endpoints: materialized_endpoints.extend(endpoints)
    )
    agent.reload()

    assert materialized_endpoints
    assert "start_action = start" in (tmp_path / "swanctl.conf").read_text()
    assert "unique = replace" in (tmp_path / "swanctl.conf").read_text()
    assert " neighbor 169.254.10.2 activate" in (tmp_path / "frr.conf").read_text()
    record = json.loads((state_dir / "materialization.json").read_text())
    assert record["boot_id"] == "boot-a"
    assert record["generation_id"] == "a" * 64
    assert passive_effects == ["firewall:True", "hygiene"]

    ready_passive = policy.decide(
        _snapshot(
            configured_role=ConfiguredRole.ACTIVE,
            cloud=_owned_cloud(),
            data_plane_mode=DataPlaneMode.PASSIVE,
            peer_heartbeat=_peer(configured_role="passive"),
            readiness=LocalReadiness(True, True, True, True),
        ),
        first.checkpoint,
    )
    assert ready_passive.action is not None
    assert ready_passive.action.kind is ActionKind.RECONCILE_ROUTES
    assert not ready_passive.forwarding_enabled
