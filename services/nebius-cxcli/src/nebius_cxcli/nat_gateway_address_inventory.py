"""Scoped SDK evidence for private address capacity and realized gateway ranges."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from .nat_gateway_address_space import (
    AddressBlock,
    AddressPool,
    AddressSpaceUnresolved,
    IPv4,
    Reservation,
)


def _token(value: Any) -> str:
    return str(getattr(value, "name", value) or "")


def pool_blocks(pool: Any) -> tuple[AddressBlock, ...]:
    """Associate resolved ranges with spec constraints without guessing list order."""
    if (
        _token(pool.spec.visibility) != "PRIVATE"
        or _token(pool.spec.version) != "IPV4"
        or _token(pool.status.state) != "READY"
        or not pool.status.cidrs
    ):
        raise AddressSpaceUnresolved("Private IPv4 source pool is not ready or resolved")
    blocks = []
    for value in pool.status.cidrs:
        cidr = IPv4(value, strict=True)
        constraints = set()
        for spec in pool.spec.cidrs:
            request = str(spec.cidr)
            matches = (
                request == f"/{cidr.prefixlen}"
                if request.startswith("/")
                else IPv4(request, strict=True) == cidr
            )
            if matches:
                state = _token(spec.state)
                # The API defaults omitted block controls to AVAILABLE and /32.
                state = "AVAILABLE" if state in {"", "STATE_UNSPECIFIED"} else state
                constraints.add((state, int(spec.max_mask_length) or 32))
        if len(constraints) != 1:
            raise AddressSpaceUnresolved("Resolved pool block constraints are ambiguous")
        state, maximum = constraints.pop()
        if state not in {"AVAILABLE", "DISABLED"} or not cidr.prefixlen <= maximum <= 32:
            raise AddressSpaceUnresolved("Invalid private pool block controls")
        if state == "AVAILABLE":
            blocks.append(AddressBlock(cidr, maximum))
    return tuple(blocks)


def read_address_pool(sdk: Any, pool_id: str, *, deadline: float | None = None) -> AddressPool:
    """Count immediate child reservations and direct allocations exactly once.

    Child-pool contents need not be traversed: their whole reservation is already
    unavailable in this parent. A separately selected child is assessed in its
    own namespace. This also handles inherited subnets without treating their
    displayed parent CIDRs as ownership.
    """
    from nebius.api.nebius.vpc.v1 import (
        AllocationServiceClient,
        GetPoolRequest,
        ListAllocationsByPoolRequest,
        ListPoolsBySourcePoolRequest,
        PoolServiceClient,
    )

    pool_client = PoolServiceClient(sdk.sdk)
    pool = sdk.call(pool_client.get, GetPoolRequest(id=pool_id))
    if pool.metadata.id != pool_id:
        raise AddressSpaceUnresolved("Source-pool identity differs from the requested pool")
    blocks = pool_blocks(pool)
    parents = tuple(IPv4(value, strict=True) for value in pool.status.cidrs)
    children = sdk.pages(
        pool_client.list_by_source_pool,
        lambda token: ListPoolsBySourcePoolRequest(
            pool_id=pool_id, page_size=500, page_token=token
        ),
        deadline=deadline,
    )
    reservations = []

    def reserve(identity: str, cidrs: tuple[IPv4, ...]) -> None:
        if not cidrs or any(not any(c.subnet_of(p) for p in parents) for c in cidrs):
            raise AddressSpaceUnresolved("Occupied range is unresolved or outside its source pool")
        reservations.append(Reservation(identity, cidrs))

    for child in children:
        if (
            child.spec.source_pool_id != pool_id
            or _token(child.spec.version) != "IPV4"
            or _token(child.spec.visibility) != "PRIVATE"
            or child.status.scope_id != pool.status.scope_id
        ):
            raise AddressSpaceUnresolved(
                "Child pool has inconsistent source or address-tree identity"
            )
        # Deleting and creating resources still occupy space. An unresolved
        # automatic reservation cannot be treated as an empty pool.
        observed = {IPv4(c, strict=True) for c in child.status.cidrs}
        concrete = {
            IPv4(item.cidr, strict=True)
            for item in child.spec.cidrs
            if not item.cidr.startswith("/")
        }
        prefixes = Counter(
            int(item.cidr[1:]) for item in child.spec.cidrs if item.cidr.startswith("/")
        )
        resolved = Counter(c.prefixlen for c in observed - concrete)
        if any(resolved[prefix] < count for prefix, count in prefixes.items()):
            raise AddressSpaceUnresolved("Child pool has unresolved pending prefix reservations")
        reserve("pool:" + child.metadata.id, tuple(observed | concrete))
    allocations = sdk.pages(
        AllocationServiceClient(sdk.sdk).list_by_pool,
        lambda token: ListAllocationsByPoolRequest(
            pool_id=pool_id, page_size=500, page_token=token
        ),
        deadline=deadline,
    )
    for allocation in allocations:
        details = allocation.status.details
        if details.pool_id != pool_id or _token(details.version) != "IPV4":
            raise AddressSpaceUnresolved(
                "Private allocation source or address version is unresolved"
            )
        value = details.allocated_cidr
        if not value:
            raise AddressSpaceUnresolved("An allocation has no resolved occupied CIDR")
        reserve("allocation:" + allocation.metadata.id, (IPv4(value, strict=True),))
    return AddressPool(pool_id, blocks, tuple(reservations))


def verify_gateway_address(
    sdk: Any, subnet: Any, inputs: Mapping[str, Any], private_allocation: Any
) -> None:
    """Check realized ownership, not new capacity; disabled pools still own addresses."""
    from nebius.api.nebius.vpc.v1 import GetPoolRequest, PoolServiceClient

    network = sdk.network(inputs["network_id"])
    parents = {reference.id for reference in network.spec.ipv4_private_pools.pools}
    ranges = [
        (pool.pool_id, IPv4(cidr, strict=True))
        for pool in subnet.status.ipv4_private_pools
        for cidr in pool.cidrs
    ]
    if inputs.get("gateway_subnet_mode", "create") == "reuse":
        details = private_allocation.status.details
        if (
            details.subnet_id != subnet.metadata.id
            or _token(details.version) != "IPV4"
            or not details.allocated_cidr
            or IPv4(details.allocated_cidr).prefixlen != 32
            or not any(
                details.pool_id == identity and IPv4(details.allocated_cidr).subnet_of(cidr)
                for identity, cidr in ranges
            )
        ):
            raise RuntimeError("NAT private allocation is outside the reused subnet pools")
        return
    if len(ranges) != 1:
        raise RuntimeError("NAT gateway must have one resolved dedicated IPv4 range")
    pool_id, cidr = ranges[0]
    request = inputs["gateway_subnet_cidr"]
    if (
        cidr.prefixlen != int(request[1:])
        if request.startswith("/")
        else cidr != IPv4(request, strict=True)
    ):
        raise RuntimeError("NAT realized subnet CIDR differs from its requested CIDR or prefix")
    pool = sdk.call(PoolServiceClient(sdk.sdk).get, GetPoolRequest(id=pool_id))
    if (
        pool.metadata.id != pool_id
        or pool.spec.source_pool_id not in parents
        or _token(pool.spec.visibility) != "PRIVATE"
        or _token(pool.spec.version) != "IPV4"
        or _token(pool.status.state) != "READY"
        or subnet.metadata.id not in pool.status.assignment.subnets
        or cidr not in {IPv4(value, strict=True) for value in pool.status.cidrs}
    ):
        raise RuntimeError("NAT realized subnet pool ownership differs from the selected network")
    parent = sdk.call(PoolServiceClient(sdk.sdk).get, GetPoolRequest(id=pool.spec.source_pool_id))
    if (
        parent.metadata.id != pool.spec.source_pool_id
        or _token(parent.spec.visibility) != "PRIVATE"
        or _token(parent.spec.version) != "IPV4"
        or parent.status.scope_id != pool.status.scope_id
        or not any(cidr.subnet_of(IPv4(value, strict=True)) for value in parent.status.cidrs)
    ):
        raise RuntimeError("NAT realized subnet is outside its parent pool")
    details = private_allocation.status.details
    if (
        details.subnet_id != subnet.metadata.id
        or details.pool_id != pool_id
        or _token(details.version) != "IPV4"
        or not details.allocated_cidr
        or IPv4(details.allocated_cidr).prefixlen != 32
        or not IPv4(details.allocated_cidr).subnet_of(cidr)
    ):
        raise RuntimeError("NAT private allocation is outside its dedicated subnet pool")
