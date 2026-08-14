from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import nebius_vpngw.agent.main as agent_main
import nebius_vpngw.agent.strongswan_renderer as strongswan_module
from nebius_vpngw.agent.frr_renderer import FRRRenderer
from nebius_vpngw.agent.strongswan_renderer import StrongSwanRenderer


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
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **kwargs: None)
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

        def render_and_apply(self, cfg, *, activate=True):
            calls.append((self.name, activate))
            return [{"name": "xfrm0"}] if self.name == "strongswan" else None

    class XFRM:
        def setup_interfaces(self, endpoints):
            calls.append(("xfrm", endpoints))

    monkeypatch.setattr(agent_main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent_main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent_main, "VM_HA_MATERIALIZATION_PATH", tmp_path / "material.json")
    monkeypatch.setattr(agent_main, "acquire_routing_lock", lambda **kwargs: None)
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
        lambda cfg: (_ for _ in ()).throw(AssertionError("firewall effect escaped passive")),
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
        ("strongswan", True),
        ("xfrm", [{"name": "xfrm0"}]),
        ("frr", True),
    ]
    assert json.loads((tmp_path / "material.json").read_text()) == {
        "schema": "nebius-vpngw/vm-ha-materialization-v1",
        "boot_id": "boot-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
    }


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
        lambda *args, **kwargs: None,
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
