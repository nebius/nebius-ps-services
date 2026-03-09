from __future__ import annotations

import subprocess
from unittest.mock import patch

from nebius_vpngw.agent.tunnel_health_monitor import TunnelHealthMonitor


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
