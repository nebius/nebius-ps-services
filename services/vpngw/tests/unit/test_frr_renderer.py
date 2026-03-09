from __future__ import annotations

from unittest.mock import Mock

from nebius_vpngw.agent import frr_renderer
from nebius_vpngw.agent.frr_renderer import FRRRenderer


def _bgp_config(sample_config: dict) -> dict:
    sample_config["defaults"]["routing"]["mode"] = "bgp"
    sample_config["defaults"]["routing"]["bgp"]["bfd"]["enabled"] = True
    sample_config["connections"][0]["routing_mode"] = "bgp"
    sample_config["connections"][0]["bgp"]["enabled"] = True
    sample_config["connections"][0]["bgp"]["remote_asn"] = 65020
    sample_config["connections"][0]["bgp"]["advertise_local_prefixes"] = True
    sample_config["connections"][0]["tunnels"] = [
        {
            "name": "tunnel-active",
            "ha_role": "active",
            "inner_local_ip": "169.254.18.225",
            "inner_remote_ip": "169.254.18.226",
            "remote_public_ip": "198.51.100.10",
        },
        {
            "name": "tunnel-passive",
            "ha_role": "passive",
            "inner_local_ip": "169.254.19.225",
            "inner_remote_ip": "169.254.19.226",
            "remote_public_ip": "198.51.100.11",
        },
    ]
    return sample_config


def test_ensure_bgpd_enabled_creates_daemons_file(tmp_path, monkeypatch) -> None:
    daemons_file = tmp_path / "frr" / "daemons"
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", daemons_file)

    changed = FRRRenderer()._ensure_bgpd_enabled(enable_bfd=True)

    assert changed is True
    rendered = daemons_file.read_text(encoding="utf-8")
    assert "bgpd=yes" in rendered
    assert 'bgpd_options="   -A 0.0.0.0"' in rendered
    assert "bfdd=yes" in rendered


def test_render_and_apply_writes_expected_bgp_config(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    daemons_file = tmp_path / "frr" / "daemons"
    frr_conf_path = tmp_path / "frr" / "frr.conf"
    bgpd_conf_path = tmp_path / "frr" / "bgpd.conf"
    run_mock = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))

    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", daemons_file)
    monkeypatch.setattr(frr_renderer, "FRR_CONF", frr_conf_path)
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", bgpd_conf_path)
    monkeypatch.setattr(frr_renderer.subprocess, "run", run_mock)

    FRRRenderer().render_and_apply(_bgp_config(sample_config))

    rendered = frr_conf_path.read_text(encoding="utf-8")
    assert "router bgp 65010" in rendered
    assert "neighbor 169.254.18.226 remote-as 65020" in rendered
    assert "neighbor 169.254.19.226 remote-as 65020" in rendered
    assert "route-map SET-LOCAL-PREF-200 permit 10" in rendered
    assert "route-map SET-LOCAL-PREF-100 permit 10" in rendered
    assert "neighbor 169.254.18.226 route-map ADVERTISE-ACTIVE out" in rendered
    assert "neighbor 169.254.19.226 route-map ADVERTISE-PASSIVE out" in rendered
    assert "neighbor 169.254.18.226 bfd" in rendered
    assert "ip prefix-list ADVERTISE-LOCAL seq 10 permit 10.0.0.0/16" in rendered
    assert run_mock.call_args[0][0] == ["systemctl", "restart", "frr"]
