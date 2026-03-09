from __future__ import annotations

import ipaddress
import typing as t
from types import SimpleNamespace

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
