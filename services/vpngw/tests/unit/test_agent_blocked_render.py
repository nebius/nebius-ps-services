from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import nebius_vpngw.agent.main as agent_main
import nebius_vpngw.agent.strongswan_renderer as strongswan_module
from nebius_vpngw.agent.frr_renderer import FRRRenderer
from nebius_vpngw.agent.strongswan_renderer import StrongSwanRenderer


def _prepare_mtls_writer_lock(state_dir: Path) -> Path:
    mtls_dir = state_dir / "mtls"
    mtls_dir.mkdir(parents=True, mode=0o700)
    mtls_dir.chmod(0o700)
    lock_path = mtls_dir / "writer.lock"
    lock_path.touch(mode=0o600)
    return lock_path


def test_vm_ha_blocked_boot_renders_without_data_plane_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    calls: list[tuple[str, bool]] = []

    class Renderer:
        def __init__(self, name: str) -> None:
            self.name = name

        def render_and_apply(self, cfg, *, activate=True):
            calls.append((self.name, activate))
            return []

    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    agent = agent_main.Agent()
    agent.ss = Renderer("strongswan")
    agent.frr = Renderer("frr")
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("current boot remains blocked")),
    )
    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda cfg: (_ for _ in ()).throw(AssertionError("firewall effect escaped guard")),
    )

    agent.reload()

    assert calls == [("strongswan", False), ("frr", False)]


def test_vm_ha_force_reconcile_without_routing_lock_has_no_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    calls: list[str] = []

    class Renderer:
        def render_and_apply(self, *_args, **_kwargs):
            calls.append("render")

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "VM_HA_STATE_DIR", tmp_path / "vm-ha")
    _prepare_mtls_writer_lock(tmp_path / "vm-ha")
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **_kwargs: None)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda *_args, **_kwargs: calls.append("firewall"),
    )
    agent = agent_main.Agent()
    agent.ss = Renderer()
    agent.frr = Renderer()

    with pytest.raises(RuntimeError, match="require the routing lock"):
        agent.reload(
            force_reconcile=True,
            expected_vm_ha_owner="node-a",
            expected_vm_ha_generation="a" * 64,
            expected_vm_ha_epoch="7",
            expected_vm_ha_allocation="allocation-a",
        )

    assert calls == []


def test_vm_ha_force_reconcile_serializes_with_rearm_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    state_dir = tmp_path / "vm-ha"
    rearm_lock_path = state_dir / "rearm.lock"
    mtls_lock_path = _prepare_mtls_writer_lock(state_dir)
    checks: list[str] = []

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "VM_HA_STATE_DIR", state_dir)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )

    def require_authority(*_args: object, **_kwargs: object) -> None:
        for label, path in (
            ("rearm", rearm_lock_path),
            ("mtls-writer", mtls_lock_path),
        ):
            probe = os.open(path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            checks.append(f"{label}-locked")
        raise RuntimeError("stop after lock proof")

    monkeypatch.setattr(
        agent_main,
        "_require_force_reconcile_authority",
        require_authority,
    )

    with pytest.raises(RuntimeError, match="stop after lock proof"):
        agent_main.Agent().reload(
            force_reconcile=True,
            expected_vm_ha_owner="node-a",
            expected_vm_ha_generation="a" * 64,
            expected_vm_ha_epoch="7",
            expected_vm_ha_allocation="allocation-a",
        )

    assert checks == ["rearm-locked", "mtls-writer-locked"]
    for path in (rearm_lock_path, mtls_lock_path):
        probe = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)


def test_vm_ha_force_reconcile_fails_closed_when_rearm_writer_is_busy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    state_dir = tmp_path / "vm-ha"
    state_dir.mkdir()
    rearm_lock = os.open(state_dir / "rearm.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(rearm_lock, fcntl.LOCK_EX)

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "VM_HA_STATE_DIR", state_dir)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("routing lock acquired before rearm writer serialization")
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="requires the rearm writer lock"):
            agent_main.Agent().reload(
                force_reconcile=True,
                expected_vm_ha_owner="node-a",
                expected_vm_ha_generation="a" * 64,
                expected_vm_ha_epoch="7",
                expected_vm_ha_allocation="allocation-a",
            )
    finally:
        os.close(rearm_lock)


def test_vm_ha_force_reconcile_fails_closed_when_mtls_writer_is_busy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    state_dir = tmp_path / "vm-ha"
    mtls_lock_path = _prepare_mtls_writer_lock(state_dir)
    mtls_lock = os.open(mtls_lock_path, os.O_RDWR)
    fcntl.flock(mtls_lock, fcntl.LOCK_EX)

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "VM_HA_STATE_DIR", state_dir)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("routing lock acquired before mTLS writer serialization")
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="requires the mTLS writer lock"):
            agent_main.Agent().reload(
                force_reconcile=True,
                expected_vm_ha_owner="node-a",
                expected_vm_ha_generation="a" * 64,
                expected_vm_ha_epoch="7",
                expected_vm_ha_allocation="allocation-a",
            )
    finally:
        os.close(mtls_lock)

    rearm_probe = os.open(state_dir / "rearm.lock", os.O_RDWR)
    try:
        fcntl.flock(rearm_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(rearm_probe)


def test_vm_ha_force_reconcile_requires_complete_authority_tuple(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: calls.append("readiness"),
    )

    with pytest.raises(RuntimeError, match="requires complete authority"):
        agent_main.Agent().reload(force_reconcile=True)

    assert calls == []


def test_passive_authority_materializes_locally_without_active_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    vm_ha = {
        "node": {"node_id": "node-a"},
        "generation": {"generation_id": "a" * 64},
    }
    calls: list[tuple[str, object]] = []

    class Renderer:
        def __init__(self, name: str) -> None:
            self.name = name

        def render_and_apply(
            self,
            cfg,
            *,
            activate=True,
            advertise_local_prefixes=True,
            require_reload=False,
            prepare_vm_ha_controller_access=False,
        ):
            calls.append(
                (
                    self.name,
                    activate,
                    advertise_local_prefixes,
                    require_reload,
                    prepare_vm_ha_controller_access,
                )
            )
            return [{"name": "xfrm0"}] if self.name == "strongswan" else None

    class XFRM:
        def setup_interfaces(self, endpoints):
            calls.append(("xfrm", endpoints))

    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    materialization_path = tmp_path / "material.json"
    lock_fd = os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(agent_main, "VM_HA_MATERIALIZATION_PATH", materialization_path)
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **kwargs: lock_fd)
    monkeypatch.setattr(agent_main, "_boot_id", lambda: "boot-a")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda path: vm_ha)
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("passive")),
    )
    monkeypatch.setattr(agent_main, "_vm_ha_materialization_authorized", lambda: vm_ha)
    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda cfg, **kwargs: calls.append(("firewall", cfg, kwargs)),
    )

    def enforce_hygiene(cfg):
        assert not materialization_path.exists()
        calls.append(("hygiene", cfg))

    monkeypatch.setattr(
        agent_main,
        "enforce_vm_ha_passive_routing_hygiene_locked",
        enforce_hygiene,
    )
    monkeypatch.setattr(
        agent_main,
        "enforce_routing_invariants",
        lambda cfg: (_ for _ in ()).throw(AssertionError("active route effect escaped passive")),
    )

    agent = agent_main.Agent()
    agent.ss = Renderer("strongswan")
    agent.frr = Renderer("frr")
    agent.xfrm = XFRM()
    agent.reload()

    assert calls == [
        ("firewall", {"version": 1, "connections": []}, {"require_reload": True}),
        ("strongswan", True, True, False, False),
        ("xfrm", [{"name": "xfrm0"}]),
        ("frr", True, False, True, True),
        ("hygiene", {"version": 1, "connections": []}),
    ]
    assert json.loads((tmp_path / "material.json").read_text()) == {
        "schema": "nebius-vpngw/vm-ha-materialization-v1",
        "boot_id": "boot-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
    }


def test_active_vm_ha_preparation_restores_bgp_origination_before_forwarding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("gateway:\n  local_prefixes: [10.0.0.0/24]\n", encoding="utf-8")
    calls: list[tuple[str, object]] = []

    class Renderer:
        def render_and_apply(self, cfg, **kwargs):
            calls.append(("frr", {"cfg": cfg, **kwargs}))

    monkeypatch.setattr(agent_main, "FRRRenderer", Renderer)
    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda cfg, **kwargs: calls.append(("firewall", {"cfg": cfg, **kwargs})),
    )

    agent_main._prepare_vm_ha_active_data_plane(config_path)

    assert calls[0] == (
        "firewall",
        {
            "cfg": {"gateway": {"local_prefixes": ["10.0.0.0/24"]}},
            "require_reload": True,
        },
    )
    assert calls[1] == (
        "frr",
        {
            "cfg": {"gateway": {"local_prefixes": ["10.0.0.0/24"]}},
            "advertise_local_prefixes": True,
            "require_reload": True,
        },
    )


def test_blocked_vm_ha_preparation_installs_required_deny_all_exports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("gateway:\n  local_prefixes: [10.0.0.0/24]\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    class Renderer:
        def render_and_apply(self, cfg, **kwargs):
            calls.append({"cfg": cfg, **kwargs})

    monkeypatch.setattr(agent_main, "FRRRenderer", Renderer)

    agent_main._prepare_vm_ha_blocked_bgp(config_path)

    assert calls == [
        {
            "cfg": {"gateway": {"local_prefixes": ["10.0.0.0/24"]}},
            "advertise_local_prefixes": False,
            "require_reload": True,
            "prepare_vm_ha_controller_access": True,
        }
    ]


def test_force_reconcile_bypasses_unchanged_config_short_circuit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    calls: list[str] = []

    class StrongSwan:
        def build_interface_endpoints(self, _cfg):
            return []

        def render_and_apply(self, _cfg):
            calls.append("strongswan")
            return []

    class FRR:
        def ensure_local_prefix_routes(self, _cfg):
            return None

        def render_and_apply(self, _cfg):
            calls.append("frr")

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: None)
    monkeypatch.setattr(agent_main, "update_firewall_from_config", lambda _cfg: None)
    monkeypatch.setattr(agent_main, "enforce_routing_invariants_locked", lambda _cfg: None)
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )

    agent = agent_main.Agent()
    agent.ss = StrongSwan()
    agent.frr = FRR()
    agent.state.is_changed = lambda _cfg: False
    agent.reload(force_reconcile=True)

    assert calls == ["strongswan", "frr"]


def test_force_reconcile_authority_rejects_stale_installed_generation(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    digests = {
        "configuration": "b" * 64,
        "static_routes": "c" * 64,
        "bgp_policy": "d" * 64,
    }
    status_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "observed_owner_node_id": "node-1",
                "generation_id": "b" * 64,
                "ownership_epoch": "8",
                "allocation_id": "allocation-b",
                "digests": digests,
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ),
        encoding="utf-8",
    )
    config = {
        "vm_ha": {
            "generation": {
                "generation_id": "b" * 64,
                "digests": digests,
            }
        }
    }

    with pytest.raises(RuntimeError, match="force-reconcile authority changed"):
        agent_main._require_force_reconcile_authority(
            config,
            expected_owner_node_id="node-1",
            expected_generation_id="a" * 64,
            expected_ownership_epoch="7",
            expected_allocation_id="allocation-a",
            status_path=status_path,
        )


def test_force_reconcile_authority_rejects_live_writer_inhibition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_path = tmp_path / "status.json"
    digests = {
        "configuration": "b" * 64,
        "static_routes": "c" * 64,
        "bgp_policy": "d" * 64,
    }
    status_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-1",
                "observed_owner_node_id": "node-1",
                "generation_id": "b" * 64,
                "ownership_epoch": "8",
                "allocation_id": "allocation-b",
                "digests": digests,
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ),
        encoding="utf-8",
    )
    config = {
        "vm_ha": {
            "cluster_id": "cluster-a",
            "node": {"node_id": "node-1"},
            "generation": {
                "generation_id": "b" * 64,
                "digests": digests,
            },
        }
    }
    calls: list[dict[str, object]] = []

    def live_inhibition(**kwargs: object) -> str:
        calls.append(kwargs)
        return "f" * 64

    monkeypatch.setattr(
        agent_main,
        "_vm_ha_writer_inhibition_operation_id",
        live_inhibition,
    )

    with pytest.raises(RuntimeError, match="force-reconcile authority changed"):
        agent_main._require_force_reconcile_authority(
            config,
            expected_owner_node_id="node-1",
            expected_generation_id="b" * 64,
            expected_ownership_epoch="8",
            expected_allocation_id="allocation-b",
            status_path=status_path,
        )

    assert calls == [
        {
            "state_dir": tmp_path,
            "cluster_id": "cluster-a",
            "node_id": "node-1",
            "generation_id": "b" * 64,
        }
    ]

    monkeypatch.setattr(
        agent_main,
        "_vm_ha_writer_inhibition_operation_id",
        lambda **_kwargs: None,
    )
    agent_main._require_force_reconcile_authority(
        config,
        expected_owner_node_id="node-1",
        expected_generation_id="b" * 64,
        expected_ownership_epoch="8",
        expected_allocation_id="allocation-b",
        status_path=status_path,
    )


def test_failed_frr_activation_does_not_advance_last_applied_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    state_path = tmp_path / "last-applied.json"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    state_path.write_text('{"config_hash":"previous"}\n', encoding="utf-8")

    class StrongSwan:
        def build_interface_endpoints(self, _cfg):
            return []

        def render_and_apply(self, _cfg):
            return []

    class FRR:
        def ensure_local_prefix_routes(self, _cfg):
            return None

        def render_and_apply(self, _cfg):
            raise RuntimeError("FRR configuration reload failed")

    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "STATE_PATH", state_path)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: None)
    monkeypatch.setattr(agent_main, "update_firewall_from_config", lambda _cfg: None)
    monkeypatch.setattr(
        agent_main,
        "acquire_routing_lock",
        lambda **_kwargs: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    agent = agent_main.Agent()
    agent.ss = StrongSwan()
    agent.frr = FRR()

    with pytest.raises(RuntimeError, match="FRR configuration reload failed"):
        agent.reload()

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"config_hash": "previous"}
    assert agent.state.is_changed({"version": 1, "connections": []})


def test_passive_materialization_hygiene_failure_does_not_write_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\nconnections: []\n", encoding="utf-8")
    materialization_path = tmp_path / "material.json"
    lock_fd = os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600)

    class Renderer:
        def render_and_apply(self, cfg, **kwargs):
            return []

    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "VM_HA_MATERIALIZATION_PATH", materialization_path)
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **kwargs: lock_fd)
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda path: {"node": {}})
    monkeypatch.setattr(
        agent_main,
        "require_vm_ha_current_boot_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("passive")),
    )
    monkeypatch.setattr(
        agent_main,
        "_vm_ha_materialization_authorized",
        lambda: {"node": {}},
    )

    monkeypatch.setattr(
        agent_main,
        "update_firewall_from_config",
        lambda cfg, **kwargs: None,
    )
    monkeypatch.setattr(
        agent_main,
        "enforce_vm_ha_passive_routing_hygiene_locked",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("hygiene failed")),
    )

    agent = agent_main.Agent()
    agent.ss = Renderer()
    agent.frr = Renderer()

    with pytest.raises(RuntimeError, match="hygiene failed"):
        agent.reload()

    assert not materialization_path.exists()


def test_ordinary_unit_lane_runs_production_composed_bootstrap_oracle(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.integration.test_vm_ha_runtime_composed import (
        test_clean_owner_bootstrap_materializes_passive_before_promotion,
    )

    test_clean_owner_bootstrap_materializes_passive_before_promotion(tmp_path, monkeypatch)


def test_blocked_frr_render_does_not_install_kernel_routes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    renderer = FRRRenderer()
    ensure_routes = Mock()
    monkeypatch.setattr(renderer, "ensure_local_prefix_routes", ensure_routes)
    monkeypatch.setattr(
        "nebius_vpngw.agent.frr_renderer.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("FRR subprocess effect escaped guard")
        ),
    )
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.BGPD_CONF", tmp_path / "bgpd.conf")
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.FRR_CONF", tmp_path / "frr.conf")

    renderer.render_and_apply(
        {"gateway": {"local_prefixes": ["10.0.0.0/24"]}, "connections": []},
        activate=False,
    )

    ensure_routes.assert_not_called()


def test_blocked_frr_render_disables_neighbor_and_bfd_activation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.BGPD_CONF", tmp_path / "bgpd.conf")
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.FRR_CONF", tmp_path / "frr.conf")

    FRRRenderer().render_and_apply(_bgp_config(), activate=False)

    rendered = (tmp_path / "frr.conf").read_text(encoding="utf-8")
    assert " no bgp default ipv4-unicast\n" in rendered
    assert " neighbor 169.254.10.2 remote-as 65020\n" in rendered
    assert " neighbor 169.254.10.2 activate\n" not in rendered
    assert " neighbor 169.254.10.2 bfd\n" not in rendered
    assert "\nbfd\n" not in rendered
    assert " peer 169.254.10.2\n" not in rendered


def test_active_frr_render_explicitly_activates_neighbor_and_bfd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.BGPD_CONF", tmp_path / "bgpd.conf")
    monkeypatch.setattr("nebius_vpngw.agent.frr_renderer.FRR_CONF", tmp_path / "frr.conf")
    monkeypatch.setattr(
        FRRRenderer,
        "_ensure_bgpd_enabled",
        lambda self, enable_bfd=False: False,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.frr_renderer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    FRRRenderer().render_and_apply(_bgp_config(), activate=True)

    rendered = (tmp_path / "frr.conf").read_text(encoding="utf-8")
    assert " no bgp default ipv4-unicast\n" in rendered
    assert " neighbor 169.254.10.2 activate\n" in rendered
    assert " neighbor 169.254.10.2 bfd\n" in rendered
    assert "\nbfd\n" in rendered
    assert " peer 169.254.10.2\n" in rendered


def _bgp_config() -> dict:
    return {
        "defaults": {
            "routing": {
                "mode": "bgp",
                "bgp": {
                    "bfd": {
                        "enabled": True,
                        "transmit_interval_ms": 300,
                        "receive_interval_ms": 300,
                        "detect_multiplier": 3,
                    }
                },
            }
        },
        "gateway": {"local_asn": 65010, "local_prefixes": ["10.0.0.0/24"]},
        "connections": [
            {
                "name": "peer",
                "routing_mode": "bgp",
                "bgp": {"remote_asn": 65020},
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "inner_local_ip": "169.254.10.1",
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }


def test_blocked_strongswan_render_is_inert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(strongswan_module, "STRONGSWAN_CONF_DIR", tmp_path / "strongswan.d")
    monkeypatch.setattr(strongswan_module, "IPSEC_CONF", tmp_path / "ipsec.conf")
    monkeypatch.setattr(strongswan_module, "SWANCTL_CONF", tmp_path / "swanctl.conf")
    monkeypatch.setattr(strongswan_module, "NETPLAN_DIR", tmp_path / "netplan")
    monkeypatch.setattr(
        strongswan_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("strongSwan subprocess effect escaped guard")
        ),
    )

    StrongSwanRenderer().render_and_apply(
        {
            "connections": [
                {
                    "name": "peer",
                    "tunnels": [
                        {
                            "name": "tunnel-1",
                            "remote_public_ip": "192.0.2.10",
                            "psk": "fixture-secret",
                        }
                    ],
                }
            ]
        },
        activate=False,
    )

    rendered = (tmp_path / "swanctl.conf").read_text(encoding="utf-8")
    assert "start_action = none" in rendered
    assert "close_action = none" in rendered
    assert "start_action = start" not in rendered
