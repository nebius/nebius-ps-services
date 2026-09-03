from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
    assert " set metric 0" in rendered
    assert " set metric 100" in rendered
    assert "neighbor 169.254.18.226 bfd" in rendered
    assert "ip prefix-list ADVERTISE-LOCAL seq 10 permit 10.0.0.0/16" in rendered
    assert run_mock.call_args[0][0] == ["systemctl", "restart", "frr"]


def test_configured_passive_vm_advertises_standby_med_tier(
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

    config = _bgp_config(sample_config)
    config["vm_ha"] = {"node": {"role": "passive"}}
    FRRRenderer().render_and_apply(config)

    rendered = frr_conf_path.read_text(encoding="utf-8")
    assert "route-map ADVERTISE-ACTIVE permit 10" in rendered
    assert " set metric 1000" in rendered
    assert "route-map ADVERTISE-PASSIVE permit 10" in rendered
    assert " set metric 1100" in rendered


def test_vm_ha_standby_withdraws_local_prefixes_but_keeps_bgp_sessions_hot(
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

    FRRRenderer().render_and_apply(
        _bgp_config(sample_config),
        advertise_local_prefixes=False,
        require_reload=True,
    )

    rendered = frr_conf_path.read_text(encoding="utf-8")
    assert "neighbor 169.254.18.226 activate" in rendered
    assert "neighbor 169.254.19.226 activate" in rendered
    assert "network 10.0.0.0/16" not in rendered
    assert "ADVERTISE-LOCAL" not in rendered
    assert "route-map ADVERTISE-ACTIVE" not in rendered
    assert "route-map ADVERTISE-PASSIVE" not in rendered
    assert "route-map ADVERTISE-NONE deny 10" in rendered
    assert "neighbor 169.254.18.226 route-map ADVERTISE-NONE out" in rendered
    assert "neighbor 169.254.19.226 route-map ADVERTISE-NONE out" in rendered
    assert run_mock.call_args[0][0] == ["systemctl", "restart", "frr"]


def test_required_frr_reconcile_recovers_an_existing_stopped_service(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    daemons_file = tmp_path / "frr" / "daemons"
    daemons_file.parent.mkdir(parents=True)
    daemons_file.write_text(
        'bgpd=yes\nbgpd_options="   -A 0.0.0.0"\nbfdd=yes\n',
        encoding="utf-8",
    )
    run_mock = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", daemons_file)
    monkeypatch.setattr(frr_renderer, "FRR_CONF", tmp_path / "frr" / "frr.conf")
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(frr_renderer.subprocess, "run", run_mock)

    FRRRenderer().render_and_apply(
        _bgp_config(sample_config),
        advertise_local_prefixes=False,
        require_reload=True,
    )

    assert run_mock.call_args[0][0] == ["systemctl", "reload-or-restart", "frr"]


def test_mixed_connections_apply_export_policy_per_peer(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    frr_conf_path = tmp_path / "frr" / "frr.conf"
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", frr_conf_path)
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(
        frr_renderer.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
    )

    config = _bgp_config(sample_config)
    config["connections"].append(
        {
            "name": "no-export",
            "routing_mode": "bgp",
            "bgp": {
                "enabled": True,
                "remote_asn": 65030,
                "advertise_local_prefixes": False,
            },
            "tunnels": [
                {
                    "name": "tunnel-no-export",
                    "ha_role": "active",
                    "inner_local_ip": "169.254.20.1",
                    "inner_remote_ip": "169.254.20.2",
                    "remote_public_ip": "198.51.100.12",
                }
            ],
        }
    )

    FRRRenderer().render_and_apply(config)

    rendered = frr_conf_path.read_text(encoding="utf-8")
    assert "network 10.0.0.0/16" in rendered
    assert "neighbor 169.254.18.226 route-map ADVERTISE-ACTIVE out" in rendered
    assert "neighbor 169.254.19.226 route-map ADVERTISE-PASSIVE out" in rendered
    assert "neighbor 169.254.20.2 route-map ADVERTISE-NONE out" in rendered
    assert "route-map ADVERTISE-NONE deny 10" in rendered


def test_vm_ha_required_frr_reload_fails_closed(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", tmp_path / "frr" / "frr.conf")
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(
        frr_renderer.subprocess,
        "run",
        Mock(return_value=Mock(returncode=1, stdout="", stderr="reload failed")),
    )

    with pytest.raises(RuntimeError, match="required FRR configuration reload failed"):
        FRRRenderer().render_and_apply(
            _bgp_config(sample_config),
            advertise_local_prefixes=False,
            require_reload=True,
        )


def test_ordinary_frr_reload_failure_is_not_reported_as_applied(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", tmp_path / "frr" / "frr.conf")
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(
        frr_renderer.subprocess,
        "run",
        Mock(return_value=Mock(returncode=1, stdout="", stderr="reload failed")),
    )

    with pytest.raises(RuntimeError, match="FRR configuration reload failed"):
        FRRRenderer().render_and_apply(_bgp_config(sample_config))


def test_vm_ha_materialization_grants_only_group_write_to_integrated_config(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    frr_conf_path = tmp_path / "frr" / "frr.conf"
    chown = Mock()
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", frr_conf_path)
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(
        frr_renderer.grp,
        "getgrnam",
        Mock(return_value=SimpleNamespace(gr_gid=1234)),
    )
    monkeypatch.setattr(frr_renderer.os, "chown", chown)
    monkeypatch.setattr(
        frr_renderer.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
    )

    FRRRenderer().render_and_apply(
        _bgp_config(sample_config),
        advertise_local_prefixes=False,
        require_reload=True,
        prepare_vm_ha_controller_access=True,
    )

    chown.assert_called_once_with(frr_conf_path, -1, 1234)
    assert frr_conf_path.stat().st_mode & 0o777 == 0o660


def test_vm_ha_materialization_fails_closed_without_frr_group(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", tmp_path / "frr" / "frr.conf")
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(
        frr_renderer.grp,
        "getgrnam",
        Mock(side_effect=KeyError("frr")),
    )

    with pytest.raises(RuntimeError, match="failed to establish VM-HA FRR"):
        FRRRenderer().render_and_apply(
            _bgp_config(sample_config),
            advertise_local_prefixes=False,
            prepare_vm_ha_controller_access=True,
        )


def test_ordinary_render_does_not_change_integrated_config_permissions(
    tmp_path,
    monkeypatch,
    sample_config: dict,
) -> None:
    frr_conf_path = tmp_path / "frr" / "frr.conf"
    frr_conf_path.parent.mkdir(parents=True)
    frr_conf_path.write_text("previous\n", encoding="utf-8")
    os.chmod(frr_conf_path, 0o640)
    chown = Mock(side_effect=AssertionError("ordinary render changed FRR ownership"))
    monkeypatch.setattr(frr_renderer, "DAEMONS_FILE", tmp_path / "frr" / "daemons")
    monkeypatch.setattr(frr_renderer, "FRR_CONF", frr_conf_path)
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "frr" / "bgpd.conf")
    monkeypatch.setattr(frr_renderer.os, "chown", chown)
    monkeypatch.setattr(
        frr_renderer.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
    )

    FRRRenderer().render_and_apply(_bgp_config(sample_config))

    chown.assert_not_called()
    assert frr_conf_path.stat().st_mode & 0o777 == 0o640
