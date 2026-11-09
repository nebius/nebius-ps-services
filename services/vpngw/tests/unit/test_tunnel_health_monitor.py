from __future__ import annotations

import contextlib
import json
import subprocess
from unittest.mock import patch

import pytest

from nebius_vpngw.agent import tunnel_health_monitor
from nebius_vpngw.agent.tunnel_health_monitor import (
    TunnelHealth,
    TunnelHealthMonitor,
    TunnelStats,
)
from nebius_vpngw.ipsec_observation import ConfigurationInventory


@pytest.fixture(autouse=True)
def installed_topology(tmp_path, monkeypatch):
    path = tmp_path / "config-resolved.yaml"
    path.write_text("gateway_group: {}\n")
    monkeypatch.setattr(tunnel_health_monitor, "CONFIG_PATH", path)
    return path


def test_component_repair_is_durable_bounded_and_requires_positive_failure(tmp_path, monkeypatch):
    from nebius_vpngw import ordinary_operations as ops

    path = tmp_path / "recovery.json"
    monkeypatch.setattr(tunnel_health_monitor, "COMPONENT_RECOVERY_PATH", path)
    monkeypatch.setattr(ops, "mutation_lock", contextlib.nullcontext)
    monkeypatch.setattr(
        tunnel_health_monitor, "authorized_configuration", lambda cfg: ConfigurationInventory()
    )
    now, state, effects = [100.0], ["failed"], []
    monkeypatch.setattr(tunnel_health_monitor.time, "time", lambda: now[0])

    def command(args, **kwargs):
        if args[:2] == ["systemctl", "show"]:
            return _completed_process(
                args, stdout=state[0] if args[2] == "strongswan.service" else "active"
            )
        # The durable consumed attempt precedes the potentially uncertain effect.
        assert "strongswan.service" in json.loads(path.read_text())["attempted"]
        effects.append(args)
        return _completed_process(args)

    monkeypatch.setattr(ops, "bounded", command)
    monitor = TunnelHealthMonitor(max_failures_before_restart=2)
    monitor._repair_failed_components(False)
    assert effects == []
    monitor = TunnelHealthMonitor(max_failures_before_restart=2)  # process restart
    monitor._repair_failed_components(False)
    assert [call[:2] for call in effects] == [["systemctl", "restart"], ["swanctl", "--load-all"]]
    monitor._repair_failed_components(False)
    assert len(effects) == 2
    state[0] = "active"
    monitor._repair_failed_components(True)
    now[0] += 60
    monitor._repair_failed_components(True)
    state[0] = "inactive"  # administrative stop is not a local defect
    monitor._repair_failed_components(False)
    assert len(effects) == 2
    state[0] = "failed"
    monitor._repair_failed_components(False)
    monitor._repair_failed_components(False)
    assert len(effects) == 4


@pytest.mark.parametrize(
    "inventory", ["[]", '["edge"]', "missing-key", "", "{}", '["edge", "edge"]']
)
def test_active_daemon_reload_requires_complete_missing_configuration(
    tmp_path, monkeypatch, inventory
):
    from nebius_vpngw import ordinary_operations as ops

    path = tmp_path / "recovery.json"
    monkeypatch.setattr(tunnel_health_monitor, "COMPONENT_RECOVERY_PATH", path)
    monkeypatch.setattr(ops, "mutation_lock", contextlib.nullcontext)
    monkeypatch.setattr(
        tunnel_health_monitor,
        "authorized_configuration",
        lambda cfg: ConfigurationInventory(frozenset({"edge"}), frozenset({"ike-psk-1"})),
    )
    effects = []

    def command(args, **kwargs):
        if args[:2] == ["systemctl", "show"]:
            return _completed_process(args, stdout="active")
        if args[-1] == "--configuration":
            return _completed_process(args, stdout=inventory_payload(inventory))
        assert "strongswan.service" in json.loads(path.read_text())["attempted"]
        effects.append(args)
        return _completed_process(args)

    monkeypatch.setattr(ops, "bounded", command)
    monitor = TunnelHealthMonitor(max_failures_before_restart=2)
    monitor._repair_failed_components(False)
    assert effects == []
    monitor._repair_failed_components(False)
    # Restarting the monitor never renews a consumed attempt.
    TunnelHealthMonitor(max_failures_before_restart=2)._repair_failed_components(False)
    assert effects == (
        [["swanctl", "--load-all", "--noprompt"]] if inventory in {"[]", "missing-key"} else []
    )


@pytest.mark.parametrize("content", ["vm_ha: {cluster_id: cluster-a}\n", "[invalid\n", "null\n"])
def test_direct_restart_cannot_bypass_installed_ha_or_invalid_topology(
    ordinary_operation_guest,
    installed_topology,
    content,
):
    installed_topology.write_text(content)
    monitor = TunnelHealthMonitor(observer_only=False)
    with patch.object(monitor, "_restart_tunnel_locked") as restart:
        assert monitor.restart_tunnel("tunnel-1") is False
    restart.assert_not_called()


def test_vm_ha_observer_mode_follows_supported_gateway_group_shape() -> None:
    assert tunnel_health_monitor._vm_ha_observer_only(  # noqa: SLF001
        {"vm_ha": {"cluster_id": "cluster-a"}}
    )
    assert tunnel_health_monitor._vm_ha_observer_only(  # noqa: SLF001
        {"gateway_group": {"vm_ha": {"enabled": True}}}
    )
    assert not tunnel_health_monitor._vm_ha_observer_only(  # noqa: SLF001
        {"gateway_group": {"vm_ha": {"enabled": False}}}
    )
    assert not tunnel_health_monitor._vm_ha_observer_only(  # noqa: SLF001
        {"gateway_group": {}}
    )


def _completed_process(
    args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize("recovered", [False, True])
def test_monitor_uses_shared_restart_under_its_existing_lock(
    ordinary_operation_guest, installed_topology, recovered
):
    import fcntl

    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw.tunnel_restart import Restart

    installed_topology.write_text(
        "connections: [{routing_mode: static, tunnels: [{name: edge}]}]\n"
    )
    monitor = TunnelHealthMonitor()

    def restart(_self, target):
        assert target["if_id"] == 100
        with ops.ROUTING_LOCK.open() as stream, pytest.raises(BlockingIOError):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return recovered

    with (
        patch.object(Restart, "validate_targets"),
        patch.object(Restart, "restart_ipsec", restart),
        patch.object(Restart, "wait", return_value=True) as final,
        patch("nebius_vpngw.agent.tunnel_health_monitor.shutil.which", return_value="/swanctl"),
    ):
        assert monitor.restart_tunnel("edge") is recovered
        assert final.call_count == int(recovered)


def test_vm_ha_observer_only_monitor_never_restarts_unhealthy_tunnel() -> None:
    monitor = TunnelHealthMonitor(max_failures_before_restart=1, observer_only=True)
    unhealthy = TunnelHealth(
        name="tunnel-1",
        interface="xfrm0",
        ipsec_status="DOWN",
        bgp_peer="169.254.1.2",
        bgp_status="Idle",
        stats=TunnelStats(
            interface="xfrm0",
            rx_bytes=0,
            rx_packets=0,
            tx_bytes=0,
            tx_packets=0,
            tx_dropped=0,
            tx_errors=0,
            rx_errors=0,
        ),
        xfrm_present=False,
        assessment="unhealthy",
        failure_reasons=["IPsec tunnel is down"],
        last_check_time=1.0,
    )

    with (
        patch.object(
            monitor,
            "get_configured_tunnels",
            return_value=[("tunnel-1", "xfrm0", "169.254.1.2")],
        ),
        patch.object(monitor, "check_tunnel_health", return_value=unhealthy),
        patch.object(monitor, "restart_tunnel") as restart,
    ):
        result = monitor.run_health_check()

    assert result["tunnel-1"] is unhealthy
    restart.assert_not_called()


@pytest.mark.parametrize("ipsec,bgp", [("ESTABLISHED", None), ("UNKNOWN", "Established")])
def test_unknown_observation_holds_recovery_and_failure_history(ipsec, bgp):
    monitor = TunnelHealthMonitor(enable_proactive_refresh=True)
    monitor.consecutive_failures["edge"] = 1
    with (
        patch.object(
            monitor, "get_configured_tunnels", return_value=[("edge", "xfrm0", "192.0.2.2")]
        ),
        patch.object(
            monitor,
            "get_xfrm_interface_stats",
            return_value=TunnelStats("xfrm0", 0, 0, 0, 0, 0, 0, 0),
        ),
        patch.object(monitor, "get_ipsec_tunnel_status", return_value=ipsec),
        patch.object(monitor, "get_bgp_peer_status", return_value=bgp),
        patch.object(monitor, "restart_tunnel") as restart,
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep"),
    ):
        result = monitor.run_health_check()["edge"]
    assert result.assessment == "unknown"
    assert not result.is_healthy
    assert monitor.consecutive_failures["edge"] == 1
    restart.assert_not_called()


def test_ike_header_without_child_is_not_health():
    from nebius_vpngw.ipsec_observation import Inventory

    monitor = TunnelHealthMonitor()
    with patch(
        "nebius_vpngw.agent.tunnel_health_monitor.snapshot",
        return_value=Inventory.from_events([{"edge": {"uniqueid": "1", "state": "ESTABLISHED"}}]),
    ):
        assert monitor.get_ipsec_tunnel_status("edge", 100) == "DOWN"


@pytest.mark.parametrize(
    "stdout,code,expected",
    [
        ("{}", 0, "Missing"),
        ("{}", 1, None),
        ("{", 0, None),
        ("[]", 0, None),
        ('{"192.0.2.2":{"bgpState":"Idle"}}', 0, "Idle"),
    ],
)
def test_bgp_missing_is_distinct_from_unavailable(stdout, code, expected):
    with patch(
        "nebius_vpngw.agent.tunnel_health_monitor.subprocess.run",
        return_value=_completed_process([], returncode=code, stdout=stdout),
    ):
        assert TunnelHealthMonitor().get_bgp_peer_status("192.0.2.2") == expected


def test_known_down_dominates_other_unknown_observation():
    monitor = TunnelHealthMonitor()
    with (
        patch.object(monitor, "get_ipsec_tunnel_status", return_value="DOWN"),
        patch.object(monitor, "get_bgp_peer_status", return_value=None),
        patch.object(monitor, "get_xfrm_interface_stats", return_value=None),
    ):
        assert monitor.check_tunnel_health("edge", "xfrm0", "192.0.2.2").assessment == "unhealthy"


def test_immediate_recheck_unknown_preserves_first_failure():
    from dataclasses import replace

    monitor = TunnelHealthMonitor()
    failed = TunnelHealth(
        "edge",
        "xfrm0",
        "DOWN",
        None,
        None,
        TunnelStats("xfrm0", 0, 0, 0, 0, 0, 0, 0),
        True,
        "unhealthy",
        ["down"],
        0,
    )
    unknown = replace(failed, assessment="unknown")
    with (
        patch.object(monitor, "get_configured_tunnels", return_value=[("edge", "xfrm0", None)]),
        patch.object(monitor, "check_tunnel_health", side_effect=[failed, unknown]),
        patch.object(monitor, "restart_tunnel") as restart,
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep"),
    ):
        assert monitor.run_health_check()["edge"].assessment == "unhealthy"
        assert monitor.run_health_check()["edge"].assessment == "unknown"
    assert monitor.consecutive_failures["edge"] == 1
    restart.assert_not_called()


def test_proactive_healthy_sample_clears_history_and_publishes_recheck():
    from dataclasses import replace

    monitor = TunnelHealthMonitor(enable_proactive_refresh=True)
    monitor.consecutive_failures["edge"] = 1
    monitor.last_proactive_refresh["edge"] = 0
    healthy = TunnelHealth(
        "edge",
        "xfrm0",
        "ESTABLISHED",
        None,
        None,
        TunnelStats("xfrm0", 0, 0, 0, 0, 0, 0, 0),
        True,
        "healthy",
        [],
        0,
    )
    unknown = replace(healthy, assessment="unknown")
    with (
        patch.object(monitor, "get_configured_tunnels", return_value=[("edge", "xfrm0", None)]),
        patch.object(monitor, "check_tunnel_health", side_effect=[healthy, unknown]),
        patch.object(monitor, "restart_tunnel", return_value=True),
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep"),
    ):
        assert monitor.run_health_check()["edge"] is unknown
    assert monitor.consecutive_failures == {}
    assert monitor.tunnel_states["edge"] is unknown
    assert monitor.get_health_summary()["unknown_tunnels"] == 1


def test_once_text_distinguishes_unknown_from_failure_and_unmeasured_counters(monkeypatch, capsys):
    monitor = TunnelHealthMonitor()
    with (
        patch.object(monitor, "get_ipsec_tunnel_status", return_value="UNKNOWN"),
        patch.object(monitor, "get_bgp_peer_status", return_value=None),
        patch.object(monitor, "get_xfrm_interface_stats", return_value=None),
    ):
        health = monitor.check_tunnel_health("edge", "xfrm0", "192.0.2.2")
    monitor.tunnel_states = {"edge": health}
    monkeypatch.setattr("sys.argv", ["monitor", "--once"])
    monkeypatch.setattr(tunnel_health_monitor, "TunnelHealthMonitor", lambda **_: monitor)
    monkeypatch.setattr(monitor, "run_health_check", lambda: monitor.tunnel_states)
    tunnel_health_monitor.main()
    output = capsys.readouterr().out
    assert "Unknown: 1" in output
    assert "Unhealthy: 0" in output
    assert "? edge (xfrm0) [unknown]" in output
    assert "BGP: UNKNOWN" in output
    assert "RX/TX: unavailable" in output
    assert "RX: 0 packets" not in output
    assert "TX: 0 packets" not in output
    assert "Failures:" not in output


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("ping", 5)]
)
def test_unavailable_enabled_ping_is_unknown_and_preserves_failure_history(error):
    monitor = TunnelHealthMonitor(ping_enabled=True, enable_proactive_refresh=True)
    monitor.consecutive_failures["edge"] = 1
    with (
        patch.object(
            monitor, "get_configured_tunnels", return_value=[("edge", "xfrm0", "192.0.2.2")]
        ),
        patch.object(monitor, "get_ipsec_tunnel_status", return_value="ESTABLISHED"),
        patch.object(monitor, "get_bgp_peer_status", return_value="Established"),
        patch.object(
            monitor,
            "get_xfrm_interface_stats",
            return_value=TunnelStats("xfrm0", 0, 0, 0, 0, 0, 0, 0),
        ),
        patch.object(tunnel_health_monitor.subprocess, "run", side_effect=error),
        patch.object(monitor, "restart_tunnel") as restart,
        patch.object(tunnel_health_monitor.time, "sleep"),
    ):
        assert monitor.run_health_check()["edge"].assessment == "unknown"
    assert monitor.consecutive_failures["edge"] == 1
    restart.assert_not_called()


def inventory_payload(value):
    if value == "[]":
        return ConfigurationInventory().json()
    if value == '["edge"]':
        return ConfigurationInventory(frozenset({"edge"}), frozenset({"ike-psk-1"})).json()
    if value == "missing-key":
        return ConfigurationInventory(frozenset({"edge"})).json()
    return value
