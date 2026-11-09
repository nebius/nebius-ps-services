"""Exact resource observations for an approved provisioning transaction.

Names are discovery inputs only. Every execution observation uses an ID from
the frozen predecessor or a successful creation receipt.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .replacement_cloud import message_digest
from .vm_ha_cloud import vm_ha_request_kwargs


def resource_types() -> dict[str, tuple[Any, Any]]:
    import nebius.api.nebius.compute.v1 as compute
    import nebius.api.nebius.vpc.v1 as vpc

    return {
        "network": (vpc.GetNetworkRequest, vpc.NetworkServiceClient),
        "pool": (vpc.GetPoolRequest, vpc.PoolServiceClient),
        "subnet": (vpc.GetSubnetRequest, vpc.SubnetServiceClient),
        "table": (vpc.GetRouteTableRequest, vpc.RouteTableServiceClient),
        "route": (vpc.GetRouteRequest, vpc.RouteServiceClient),
        "allocation": (vpc.GetAllocationRequest, vpc.AllocationServiceClient),
        "security-group": (vpc.GetSecurityGroupRequest, vpc.SecurityGroupServiceClient),
        "disk": (compute.GetDiskRequest, compute.DiskServiceClient),
        "compute": (compute.GetInstanceRequest, compute.InstanceServiceClient),
        "image": (compute.GetImageRequest, compute.ImageServiceClient),
    }


def read(sdk: Any, kind: str, identifier: str) -> Any:
    request, service = resource_types()[kind]
    resource = service(sdk).get(request(id=identifier), **vm_ha_request_kwargs()).wait()
    if not identifier or resource.metadata.id != identifier:
        raise RuntimeError("Provisioning observation returned a different resource")
    return resource


def snapshot(kind: str, resource: Any) -> dict:
    from .vm_ha_cloud import clone_nebius_sdk_message

    if (
        kind not in resource_types()
        or not resource.metadata.id
        or not resource.metadata.parent_id
        or resource.metadata.resource_version <= 0
    ):
        raise RuntimeError("Provisioning requires an exact versioned resource observation")
    # These output fields can advance without changing reviewed intent. Updates
    # still submit the exact positive revision frozen in the original request.
    metadata = clone_nebius_sdk_message(resource.metadata)
    metadata.__pb2_message__.ClearField("resource_version")
    metadata.__pb2_message__.ClearField("updated_at")
    value = {
        "kind": kind,
        "id": resource.metadata.id,
        "parent_id": resource.metadata.parent_id,
        "metadata_sha256": message_digest(metadata),
        "spec_sha256": message_digest(resource.spec),
    }
    if kind in {"allocation", "security-group"}:
        value["status_sha256"] = message_digest(resource.status)
    return value


def inventory(sdk: Any, kind: str, parent: str) -> tuple[Any, ...]:
    import nebius.api.nebius.vpc.v1 as vpc

    resources: tuple[Any, ...]
    if kind == "subnet":
        resources = tuple(
            collect_nebius_pages(
                lambda token: vpc.SubnetServiceClient(sdk).list_by_network(
                    vpc.ListSubnetsByNetworkRequest(
                        network_id=parent, page_size=1000, page_token=token
                    ),
                    **vm_ha_request_kwargs(),
                ),
                context="Provisioning inventory",
                item_identity=nebius_resource_id,
            )
        )
    elif kind == "route":
        resources = tuple(
            collect_nebius_pages(
                lambda token: vpc.RouteServiceClient(sdk).list(
                    vpc.ListRoutesRequest(parent_id=parent, page_size=1000, page_token=token),
                    **vm_ha_request_kwargs(),
                ),
                context="Provisioning inventory",
                item_identity=nebius_resource_id,
            )
        )
    else:
        raise ValueError("Unknown provisioning inventory")
    if any(
        (resource.spec.network_id if kind == "subnet" else resource.metadata.parent_id) != parent
        for resource in resources
    ):
        raise RuntimeError("Provisioning inventory contains a foreign resource")
    return tuple(resources)


def _semantic_digest(message: Any) -> str:
    """Compare intent with documented defaults, preserving unknown fields.

    Subnet create/readback expands omitted pool inheritance and CIDR defaults:
    https://docs.nebius.com/terraform-provider/reference/resources/vpc_v1_subnet
    Allocation CIDRs default to /32:
    https://docs.nebius.com/rest-api/vpc/v1/allocations/create
    Normalize copies only; the original request remains the replay authority.
    """
    from copy import deepcopy

    protobuf = deepcopy(message.__pb2_message__)

    def normalize(value: Any) -> None:
        if value.DESCRIPTOR.full_name == "nebius.vpc.v1.SubnetSpec":
            for name in ("ipv4_private_pools", "ipv4_public_pools"):
                if not value.HasField(name):
                    getattr(value, name).use_network_pools = True
        elif value.DESCRIPTOR.full_name == "nebius.vpc.v1.SubnetCidr":
            if value.max_mask_length == 0:
                value.max_mask_length = 32
            if value.state == 0:
                value.state = value.DESCRIPTOR.fields_by_name["state"].enum_type.values_by_name[
                    "AVAILABLE"
                ].number
        elif value.DESCRIPTOR.full_name in {
            "nebius.vpc.v1.IPv4PrivateAllocationSpec",
            "nebius.vpc.v1.IPv4PublicAllocationSpec",
        }:
            if not value.cidr:
                value.cidr = "/32"
        for field, content in value.ListFields():
            if field.message_type is None:
                continue
            if field.is_repeated:
                if field.message_type.GetOptions().map_entry:
                    continue
                for item in content:
                    normalize(item)
            else:
                normalize(content)
                if content.ByteSize() == 0:
                    value.ClearField(field.name)

    normalize(protobuf)
    return hashlib.sha256(protobuf.SerializeToString(deterministic=True)).hexdigest()


def matches(resource: Any, request: Any) -> bool:
    """Verify submitted metadata/spec intent; operation settlement remains separate."""
    return bool(
        resource.metadata.id
        and resource.metadata.parent_id == request.metadata.parent_id
        and resource.metadata.name == request.metadata.name
        and dict(resource.metadata.labels) == dict(request.metadata.labels)
        and resource.metadata.resource_version > 0
        and (not request.metadata.id or resource.metadata.id == request.metadata.id)
        and _semantic_digest(resource.spec) == _semantic_digest(request.spec)
        and not getattr(resource.status, "reconciling", False)
    )
