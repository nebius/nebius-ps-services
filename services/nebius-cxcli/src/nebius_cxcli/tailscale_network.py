"""Resolve and constrain advertised destinations to the selected Nebius VPC."""

from __future__ import annotations

import ipaddress
from typing import Any


def contained(routes: list[str], ranges: list[str]) -> bool:
    pools = list(ipaddress.collapse_addresses(ipaddress.IPv4Network(v) for v in ranges))
    return bool(pools) and all(
        any(ipaddress.IPv4Network(route).subnet_of(pool) for pool in pools) for route in routes
    )


def validate_live(sdk: Any, network_id: str, subnet_id: str, routes: list[str]) -> None:
    from nebius.api.nebius.vpc.v1 import (
        GetNetworkRequest,
        GetSubnetRequest,
        ListSubnetsByNetworkRequest,
        NetworkServiceClient,
        SubnetServiceClient,
    )

    network = sdk.call(NetworkServiceClient(sdk.sdk).get, GetNetworkRequest(id=network_id))
    if network.metadata.id != network_id or network.metadata.parent_id != sdk.project_id:
        raise RuntimeError("Tailscale network differs from the selected Nebius project")
    client = SubnetServiceClient(sdk.sdk)
    placement = sdk.call(client.get, GetSubnetRequest(id=subnet_id))
    if (
        placement.metadata.id != subnet_id
        or placement.metadata.parent_id != sdk.project_id
        or placement.spec.network_id != network_id
        or not placement.status.ipv4_public_pools
    ):
        raise RuntimeError(
            "Tailscale placement requires a public-IP-capable subnet in its selected VPC"
        )
    token, seen = "", set()
    ranges: list[str] = []
    for _ in range(100):
        page = sdk.call(
            client.list_by_network,
            ListSubnetsByNetworkRequest(network_id=network_id, page_size=500, page_token=token),
        )
        for subnet in page.items:
            if subnet.spec.network_id != network_id or subnet.metadata.parent_id != sdk.project_id:
                raise RuntimeError("Subnet inventory returned an unrelated VPC or project")
            ranges.extend(cidr for pool in subnet.status.ipv4_private_pools for cidr in pool.cidrs)
        token = page.next_page_token
        if not token:
            break
        if token in seen:
            raise RuntimeError("Repeated subnet inventory continuation token")
        seen.add(token)
    else:
        raise RuntimeError("Subnet inventory exceeded its page bound")
    if not contained(routes, ranges):
        raise RuntimeError(
            "Advertised prefixes must be contained in the selected VPC's resolved subnet ranges"
        )
