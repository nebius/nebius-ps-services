from __future__ import annotations

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
)
from nebius_vpngw.deploy.route_manager import RouteManager


def _plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="us-central1",
            external_ips=[["203.0.113.10"]],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.96.0.0/13"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="version: 1\n",
            )
        ],
    )


def test_expected_advertised_prefixes_follow_gateway_local_prefixes() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "defaults": {"routing": {"mode": "bgp"}},
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": "bgp",
                "bgp": {"advertise_local_prefixes": True},
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "tunnel-2",
                        "gateway_instance_index": 0,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            }
        ],
    }

    expected = route_manager._expected_advertised_prefixes(_plan(), local_cfg)

    assert expected == {
        "nebius-vpn-gw-0": {
            "169.254.10.2": {"10.96.0.0/13"},
            "169.254.11.2": {"10.96.0.0/13"},
        }
    }


def test_bgp_advertisements_need_refresh_for_prefix_drift() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._bgp_advertisements_need_refresh(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.0.0.0/16"}},
    )


def test_bgp_advertisements_need_refresh_for_peer_set_drift() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._bgp_advertisements_need_refresh(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2", "169.254.20.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
    )


def test_bgp_advertisements_do_not_need_refresh_when_live_state_matches() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert not route_manager._bgp_advertisements_need_refresh(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
    )
