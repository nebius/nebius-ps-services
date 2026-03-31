from __future__ import annotations

import ipaddress
import json
import typing as t
from types import SimpleNamespace

import pytest

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
)
from nebius_vpngw.deploy.route_manager import RouteManager


def _fake_subnet(
    *,
    name: str,
    network_id: str,
    explicit_cidrs: list[str] | None = None,
    status_cidrs: list[str] | None = None,
    use_network_pools: bool = False,
):
    pools = []
    if explicit_cidrs:
        pools.append(
            SimpleNamespace(
                cidrs=[SimpleNamespace(cidr=cidr) for cidr in explicit_cidrs],
            )
        )

    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, id=f"id-{name}"),
        spec=SimpleNamespace(
            network_id=network_id,
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=use_network_pools,
                pools=pools,
            ),
        ),
        status=SimpleNamespace(
            ipv4_private_cidrs=status_cidrs or explicit_cidrs or [],
            route_table=SimpleNamespace(id="", default=True),
        ),
    )


def _three_vm_plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=3,
            region="eu-north1",
            external_ips=[["203.0.113.10"], ["203.0.113.20"], ["203.0.113.30"]],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.96.0.0/13"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="version: 1\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="version: 1\n",
            ),
            InstanceResolvedConfig(
                instance_index=2,
                hostname="nebius-vpn-gw-2",
                external_ip="203.0.113.30",
                config_yaml="version: 1\n",
            ),
        ],
    )


def test_select_local_prefix_subnets_includes_inherited_and_skips_gateway_subnet() -> None:
    route_manager = RouteManager(project_id="project-test")
    subnets = [
        _fake_subnet(
            name="default-subnet",
            network_id="net-1",
            status_cidrs=["10.48.0.0/13", "172.16.10.0/24", "172.16.20.0/24"],
            use_network_pools=True,
        ),
        _fake_subnet(
            name="workload-subnet-1",
            network_id="net-1",
            explicit_cidrs=["172.16.10.0/24"],
        ),
        _fake_subnet(
            name="workload-subnet-2",
            network_id="net-1",
            explicit_cidrs=["172.16.20.0/24"],
        ),
        _fake_subnet(
            name="custom-gateway-subnet",
            network_id="net-1",
            explicit_cidrs=["172.16.30.0/24"],
        ),
        _fake_subnet(
            name="other-network-subnet",
            network_id="net-2",
            explicit_cidrs=["172.16.20.0/24"],
        ),
    ]

    selected, inherited_selected = route_manager._select_local_prefix_subnets(
        subnets,
        [
            ipaddress.IPv4Network("172.16.10.0/24"),
            ipaddress.IPv4Network("172.16.20.0/24"),
            ipaddress.IPv4Network("172.16.30.0/24"),
        ],
        target_network_id="net-1",
        gateway_subnet_name="custom-gateway-subnet",
    )

    selected_subnets = t.cast(list[SimpleNamespace], selected)
    assert [subnet.metadata.name for subnet in selected_subnets] == [
        "default-subnet",
        "workload-subnet-1",
        "workload-subnet-2",
    ]
    assert inherited_selected == [
        ("default-subnet", ["10.48.0.0/13", "172.16.10.0/24", "172.16.20.0/24"])
    ]


def test_collect_remote_prefix_targets_uses_connection_vm_allocations() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "defaults": {"routing": {"mode": "bgp"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16", "10.11.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 0, "ha_role": "active"},
                    {"gateway_instance_index": 0, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "static",
                "remote_prefixes": ["10.20.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 1, "ha_role": "active"},
                    {"gateway_instance_index": 1, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-c",
                "routing_mode": "static",
                "remote_prefixes": ["10.30.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 2, "ha_role": "active"},
                    {"gateway_instance_index": 2, "ha_role": "passive"},
                ],
            },
        ],
    }

    targets = route_manager._collect_remote_prefix_targets(
        _three_vm_plan(),
        local_cfg,
        {
            0: "alloc-vm0",
            1: "alloc-vm1",
            2: "alloc-vm2",
        },
    )

    assert targets == {
        "10.10.0.0/16": "alloc-vm0",
        "10.11.0.0/16": "alloc-vm0",
        "10.20.0.0/16": "alloc-vm1",
        "10.30.0.0/16": "alloc-vm2",
    }


def test_collect_remote_prefix_targets_rejects_same_prefix_on_different_vms() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "defaults": {"routing": {"mode": "static"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 0, "ha_role": "active"},
                    {"gateway_instance_index": 0, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 1, "ha_role": "active"},
                    {"gateway_instance_index": 1, "ha_role": "passive"},
                ],
            },
        ],
    }

    with pytest.raises(ValueError, match="same remote prefix is present on more than one gateway VM"):
        route_manager._collect_remote_prefix_targets(
            _three_vm_plan(),
            local_cfg,
            {
                0: "alloc-vm0",
                1: "alloc-vm1",
                2: "alloc-vm2",
            },
        )


def test_get_bgp_learned_routes_queries_only_connection_owner_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "gateway_group": {"vm_spec": {}},
    }
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
            },
            {
                "gateway_instance_index": 1,
                "ha_role": "passive",
            },
        ],
    }
    calls: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd[5].removeprefix("ubuntu@"))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                        "10.96.0.0/13": [
                            {"nexthops": [{"ip": "0.0.0.0"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert calls == ["203.0.113.20"]
    assert prefixes == ["10.20.0.0/16"]
