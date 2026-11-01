"""A different IKE connection must never authorize a tunnel repair."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from nebius_vpngw.agent.tunnel_health_monitor import TunnelHealthMonitor, TunnelStats


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "edge-backup: #25, CONNECTING, IKEv2, 0000_i 0000_r\n"
            "edge: #21, ESTABLISHED, IKEv2, 1111_i 2222_r\n",
            "ESTABLISHED",
        ),
        (
            "edge-backup: #25, ESTABLISHED, IKEv2, 0000_i 0000_r\n"
            "edge: #21, CONNECTING, IKEv2, 1111_i 2222_r\n",
            "CONNECTING",
        ),
        ("edge-backup: #25, ESTABLISHED, IKEv2, 0000_i 0000_r\n", "DOWN"),
        (
            "edge: #25, CONNECTING, IKEv2, 0000_i 0000_r\n"
            "edge: #21, ESTABLISHED, IKEv2, 1111_i 2222_r\n",
            "ESTABLISHED",
        ),
        (
            "edge: #21, ESTABLISHED, IKEv2, 1111_i 2222_r\n"
            "edge: #25, CONNECTING, IKEv2, 0000_i 0000_r\n",
            "ESTABLISHED",
        ),
        ("edge: #21, ESTABLISHED, IKEv1, 1111_i 2222_r\n", "ESTABLISHED"),
        ("  edge: #21, reqid 1, INSTALLED, TUNNEL, ESP:AES_GCM_16-128\n", "DOWN"),
        ("  remote 'edge ESTABLISHED' @ 192.0.2.1\n", "DOWN"),
        ("edge: #21, REKEYING, IKEv2, 1111_i 2222_r\n", "DOWN"),
        ("", "DOWN"),
    ],
)
def test_ike_health_uses_exact_connection_and_all_overlapping_sas(
    output: str, expected: str
) -> None:
    with (
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.shutil.which",
            return_value="/usr/sbin/swanctl",
        ),
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ),
    ):
        assert TunnelHealthMonitor().get_ipsec_tunnel_status("edge") == expected


def test_neighbor_connection_failure_cannot_restart_healthy_tunnel() -> None:
    monitor = TunnelHealthMonitor(max_failures_before_restart=2)
    output = (
        "edge-backup: #25, CONNECTING, IKEv2, 0000_i 0000_r\n"
        "edge: #21, ESTABLISHED, IKEv2, 1111_i 2222_r\n"
    )
    with (
        patch.object(monitor, "get_configured_tunnels", return_value=[("edge", "xfrm0", None)]),
        patch.object(
            monitor,
            "get_xfrm_interface_stats",
            return_value=TunnelStats("xfrm0", 200, 2, 200, 2, 0, 0, 0),
        ),
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.shutil.which",
            return_value="/usr/sbin/swanctl",
        ),
        patch(
            "nebius_vpngw.agent.tunnel_health_monitor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ),
        patch("nebius_vpngw.agent.tunnel_health_monitor.time.sleep"),
        patch.object(monitor, "restart_tunnel", return_value=True) as restart,
    ):
        for _ in range(2):
            health = monitor.run_health_check()["edge"]
            assert health.is_healthy
        restart.assert_not_called()
