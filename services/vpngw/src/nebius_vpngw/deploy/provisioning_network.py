"""Pure network requests shared by provisioning planning and execution."""

from __future__ import annotations

from typing import Any

from .vm_ha_cloud import clone_nebius_sdk_message


def _update_parts(resource: Any, project: str) -> tuple[Any, Any]:
    metadata = clone_nebius_sdk_message(resource.metadata)
    if not metadata.id or metadata.parent_id != project or metadata.resource_version <= 0:
        raise RuntimeError("Network update requires exact project identity and positive revision")
    return metadata, clone_nebius_sdk_message(resource.spec)


def extend_pool(pool: Any, project: str, cidr: str) -> Any:
    from nebius.api.nebius.vpc.v1 import PoolCidr, UpdatePoolRequest

    metadata, spec = _update_parts(pool, project)
    spec.cidrs = [*spec.cidrs, PoolCidr(cidr=cidr)]
    return UpdatePoolRequest(metadata=metadata, spec=spec)


def attach_table(subnet: Any, project: str, table: str) -> Any:
    from nebius.api.nebius.vpc.v1 import UpdateSubnetRequest

    if not table:
        raise RuntimeError("Subnet attachment requires an exact route table")
    metadata, spec = _update_parts(subnet, project)
    spec.route_table_id = table
    return UpdateSubnetRequest(metadata=metadata, spec=spec)


def create_subnet(project: str, name: str, network: str, cidr: str) -> Any:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        CreateSubnetRequest,
        IPv4PrivateSubnetPools,
        SubnetCidr,
        SubnetPool,
        SubnetSpec,
    )

    return CreateSubnetRequest(
        metadata=ResourceMetadata(name=name, parent_id=project),
        spec=SubnetSpec(
            network_id=network,
            ipv4_private_pools=IPv4PrivateSubnetPools(
                use_network_pools=False, pools=[SubnetPool(cidrs=[SubnetCidr(cidr=cidr)])]
            ),
        ),
    )


def create_table(project: str, name: str, network: str) -> Any:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import CreateRouteTableRequest, RouteTableSpec

    return CreateRouteTableRequest(
        metadata=ResourceMetadata(name=name, parent_id=project),
        spec=RouteTableSpec(network_id=network),
    )


def create_default_route(table: str) -> Any:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import CreateRouteRequest, route_pb2

    return CreateRouteRequest(
        metadata=ResourceMetadata(name="default-egress", parent_id=table),
        spec=route_pb2.RouteSpec(
            destination=route_pb2.DestinationMatch(cidr="0.0.0.0/0"),
            next_hop=route_pb2.NextHop(default_egress_gateway=True),
        ),
    )


def create_allocation(
    project: str, name: str, subnet: str, *, public: bool, address: str | None = None
) -> Any:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        AllocationSpec,
        CreateAllocationRequest,
        IPv4PrivateAllocationSpec,
        IPv4PublicAllocationSpec,
    )

    if not public and address is not None:
        raise ValueError("Primary private allocation uses its reviewed subnet allocation policy")
    spec = (
        AllocationSpec(
            ipv4_public=IPv4PublicAllocationSpec(subnet_id=subnet, cidr=address or "/32")
        )
        if public
        else AllocationSpec(ipv4_private=IPv4PrivateAllocationSpec(subnet_id=subnet))
    )
    return CreateAllocationRequest(
        metadata=ResourceMetadata(name=name, parent_id=project), spec=spec
    )
