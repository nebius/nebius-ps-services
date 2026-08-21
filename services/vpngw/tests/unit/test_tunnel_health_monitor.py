from __future__ import annotations

import subprocess
from unittest.mock import patch

from nebius_vpngw.agent import tunnel_health_monitor
from nebius_vpngw.agent.tunnel_health_monitor import (
    TunnelHealth,
    TunnelHealthMonitor,
    TunnelStats,
)


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


def test_restart_tunnel_reloads_swanctl_and_falls_back_to_ike_terminate() -> None:
    monitor = TunnelHealthMonitor()
    tunnel_name = "tunnel-1"

    with (
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.shutil.which",
            return_value="/usr/sbin/swanctl",
        ),
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.subprocess.run",
            side_effect=[
                _completed_process(["swanctl", "--load-all"], returncode=1, stderr="load warning"),
                _completed_process(
                    ["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"],
                    returncode=1,
                    stderr="child not found",
                ),
                _completed_process(
                    ["swanctl", "--terminate", "--ike", tunnel_name, "--timeout", "5"]
                ),
                _completed_process(
                    ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"]
                ),
            ],
        ) as run_mock,
        patch.object(monitor, "get_ipsec_tunnel_status", return_value="ESTABLISHED"),
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep", return_value=None),
    ):
        assert monitor.restart_tunnel(tunnel_name) is True

    assert [call.args[0] for call in run_mock.call_args_list] == [
        ["swanctl", "--load-all"],
        ["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"],
        ["swanctl", "--terminate", "--ike", tunnel_name, "--timeout", "5"],
        ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
    ]


def test_restart_tunnel_accepts_recovered_tunnel_after_failed_initiate() -> None:
    monitor = TunnelHealthMonitor()
    tunnel_name = "tunnel-1"

    with (
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.shutil.which",
            return_value="/usr/sbin/swanctl",
        ),
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.subprocess.run",
            side_effect=[
                _completed_process(["swanctl", "--load-all"]),
                _completed_process(
                    ["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"]
                ),
                _completed_process(
                    ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
                    returncode=1,
                    stderr="initiate failed",
                ),
            ],
        ) as run_mock,
        patch.object(monitor, "get_ipsec_tunnel_status", return_value="ESTABLISHED"),
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep", return_value=None),
    ):
        assert monitor.restart_tunnel(tunnel_name) is True

    assert [call.args[0] for call in run_mock.call_args_list] == [
        ["swanctl", "--load-all"],
        ["swanctl", "--terminate", "--child", tunnel_name, "--timeout", "5"],
        ["swanctl", "--initiate", "--child", tunnel_name, "--timeout", "20"],
    ]


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
        is_healthy=False,
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
