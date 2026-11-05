"""A different IKE connection must never authorize a tunnel repair."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from tests.unit.test_ipsec_observation import sas

from nebius_vpngw.agent.tunnel_health_monitor import TunnelHealthMonitor, TunnelStats


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (sas(), "ESTABLISHED"),
        (sas(name="edge-backup"), "DOWN"),
        (sas(state="INSTALLING"), "DOWN"),
        (sas(ifid="00000065"), "DOWN"),
        (sas().replace("state=ESTABLISHED", "state=CONNECTING"), "DOWN"),
        (
            sas().replace(" list-sas reply {}", "") + sas().replace("uniqueid=1", "uniqueid=2"),
            "ESTABLISHED",
        ),
        ("list-sa event {edge {uniqueid=1 state=ESTABLISHED}} list-sas reply {}", "DOWN"),
        ("edge: #21, ESTABLISHED, IKEv2", "UNKNOWN"),
        ("", "UNKNOWN"),
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
        assert TunnelHealthMonitor().get_ipsec_tunnel_status("edge", 100) == expected


def test_neighbor_connection_failure_cannot_restart_healthy_tunnel() -> None:
    monitor = TunnelHealthMonitor(max_failures_before_restart=2)
    output = sas()
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
