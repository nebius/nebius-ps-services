"""Read-only planning of the gateway network foundation.

Existing objects become frozen preconditions. New identities are typed slots;
only the resource executor can fill them from successful operation receipts.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from . import provisioning_network as requests
from .provisioning_inventory import inventory, read, snapshot
from .provisioning_plan import Reference, Request, validate_requests


@dataclass(frozen=True)
class Foundation:
    network_id: str
    subnet: str
    requests: tuple[Request, ...]
    observed: dict
    inventories: list[dict]


def foundation(manager: Any, sdk: Any, spec: Any) -> Foundation:
    from nebius.api.nebius.vpc.v1 import RouteTableServiceClient

    network, network_id, _, subnet_service = manager._resolve_gateway_network(
        sdk, spec, strict=True
    )
    observed = {network_id: snapshot("network", network)}
    subnets = inventory(sdk, "subnet", network_id)
    observed.update({item.metadata.id: snapshot("subnet", item) for item in subnets})
    inventories = [
        {
            "kind": "subnet",
            "parent": network_id,
            "ids": sorted(item.metadata.id for item in subnets),
        }
    ]
    settings = manager._gateway_subnet_settings(spec)
    desired = ipaddress.IPv4Network(settings["cidr"], strict=True) if settings["cidr"] else None
    subnet = manager._find_gateway_subnet(subnet_service, network_id, settings["name"], strict=True)
    if subnet is not None and observed.get(subnet.metadata.id) != snapshot("subnet", subnet):
        raise RuntimeError("Gateway subnet changed during provisioning discovery")
    planned: list[Request] = []
    cidr = None
    if subnet is not None:
        manager._validate_existing_gateway_subnet(
            subnet, settings["name"], desired, settings["prefix_length"]
        )
        subnet_id = subnet.metadata.id
    else:
        existing = [
            cidr for item in subnets for cidr in manager._extract_explicit_subnet_networks(item)
        ]
        pools = manager._get_network_private_pools(sdk, network)
        for identifier, pool in pools:
            if pool.metadata.id != identifier or pool.metadata.parent_id != manager.project_id:
                raise RuntimeError("Gateway network pool identity changed")
            observed[identifier] = snapshot("pool", pool)
        pool_cidrs = [
            ipaddress.IPv4Network(item.cidr, strict=True)
            for _, pool in pools
            for item in pool.spec.cidrs
        ]
        if desired is not None:
            if any(desired.overlaps(item) for item in existing):
                raise RuntimeError("Gateway subnet overlaps an existing network subnet")
            cidr = str(desired)
            if not any(desired.subnet_of(item) for item in pool_cidrs):
                if len(pools) != 1:
                    raise RuntimeError("Automatic pool extension requires exactly one private pool")
                planned.append(
                    Request.freeze(
                        "extend-pool", requests.extend_pool(pools[0][1], manager.project_id, cidr)
                    )
                )
        else:
            cidr = manager._find_first_free_subnet_cidr(
                pool_cidrs, existing, prefix_length=settings["prefix_length"]
            )
            if not cidr:
                raise RuntimeError("The selected network has no available gateway subnet")
        subnet_id = "@gateway-subnet"

    table_service = RouteTableServiceClient(sdk)
    attached_table = subnet.spec.route_table_id if subnet is not None else ""
    table = (
        manager._strict_route_table_by_id(table_service, attached_table)
        if attached_table
        else manager._strict_route_table_by_name(
            table_service, manager._gateway_route_table_name(settings["name"])
        )
    )
    routes: tuple[Any, ...]
    if table is None:
        table_id = "@gateway-table"
        planned.append(
            Request.freeze(
                "gateway-table",
                requests.create_table(
                    manager.project_id,
                    manager._gateway_route_table_name(settings["name"]),
                    network_id,
                ),
            )
        )
        routes = ()
    else:
        table_id = manager._validate_preparation_route_table(table, network_id=network_id)
        observed[table_id] = snapshot("table", table)
        attached = {item.metadata.id for item in subnets if item.spec.route_table_id == table_id}
        if attached - {subnet_id} or attached_table and attached != {subnet_id}:
            raise RuntimeError("Gateway route table is not exclusively assigned to its subnet")
        routes = inventory(sdk, "route", table_id)
        observed.update({item.metadata.id: snapshot("route", item) for item in routes})
    inventories.append(
        {"kind": "route", "parent": table_id, "ids": sorted(item.metadata.id for item in routes)}
    )
    defaults = [item for item in routes if manager._route_destination_cidr(item) == "0.0.0.0/0"]
    if len(defaults) > 1 or defaults and not manager._route_uses_default_egress(defaults[0]):
        raise RuntimeError("Gateway default route differs from the supported egress policy")
    table_reference = (
        (Reference("metadata.parent_id", "gateway-table", "table"),)
        if table_id.startswith("@")
        else ()
    )
    if not defaults:
        planned.append(
            Request.freeze(
                "default-egress",
                requests.create_default_route(table_id),
                references=table_reference,
            )
        )
    subnet_reference = (
        (Reference("spec.route_table_id", "gateway-table", "table"),)
        if table_id.startswith("@")
        else ()
    )
    if subnet is None:
        assert cidr is not None
        request = requests.create_subnet(manager.project_id, settings["name"], network_id, cidr)
        request.spec.route_table_id = table_id
        planned.append(Request.freeze("gateway-subnet", request, references=subnet_reference))
    elif not attached_table:
        planned.append(
            Request.freeze(
                "attach-gateway-table",
                requests.attach_table(subnet, manager.project_id, table_id),
                references=subnet_reference,
            )
        )
    validate_requests(tuple(planned))
    return Foundation(network_id, subnet_id, tuple(planned), observed, inventories)


def resources(
    manager: Any,
    sdk: Any,
    spec: Any,
    indices: tuple[int, ...],
    *,
    configuration_sha256: str,
    artifact_sha256: str,
    bootstraps: dict,
) -> dict:
    """Freeze all new ordinary members, without resolving any creation by name."""
    from nebius.api.nebius.common.v1 import GetByNameRequest
    from nebius.api.nebius.compute.v1 import DiskServiceClient
    from nebius.api.nebius.vpc.v1 import AllocationServiceClient

    from .provisioning_journal import requests_from_plan
    from .security_groups import effective_ids
    from .vm_ha_cloud import nebius_request_error_code_is, vm_ha_request_kwargs

    if (
        not indices
        or indices != tuple(sorted(set(indices)))
        or any(type(index) is not int or not 0 <= index < spec.instance_count for index in indices)
    ):
        raise ValueError("Provisioning requires exact new member indices")
    guests = {f"{spec.name}-{index}" for index in indices}
    if set(bootstraps) != guests:
        raise ValueError("Provisioning bootstrap intent differs from the new member scope")
    for guest in sorted(guests):
        if manager._get_vm_by_name(sdk, guest) is not None:
            raise RuntimeError("Fresh provisioning cannot replace an existing Compute member")
    network = foundation(manager, sdk, spec)
    planned = list(network.requests)
    observed = dict(network.observed)
    provisioning = manager._resolved_vm_provisioning_config(spec, network.subnet, "")
    if provisioning.num_nics != 1:
        raise RuntimeError("Provisioning requires the supported single-NIC gateway topology")
    image_id = manager._resolve_boot_image_id(sdk, spec, provisioning)
    if not image_id:
        raise RuntimeError("Provisioning requires an exact resolved source image")
    observed[image_id] = snapshot("image", read(sdk, "image", image_id))
    for identifier in effective_ids(sdk, network.network_id, tuple(spec.security_group_ids)):
        group = read(sdk, "security-group", identifier)
        if (
            group.spec.network_id != network.network_id
            or getattr(group.status.state, "name", "") != "READY"
            or not spec.security_group_ids
            and not group.status.default
        ):
            raise RuntimeError("Security Group policy changed during provisioning planning")
        observed[identifier] = snapshot("security-group", group)

    disk_service, allocation_service = DiskServiceClient(sdk), AllocationServiceClient(sdk)
    reused_allocations = set()
    for index in indices:
        guest = f"{spec.name}-{index}"
        disk_name = guest + "-boot"
        try:
            disk_service.get_by_name(
                GetByNameRequest(parent_id=manager.project_id, name=disk_name),
                **vm_ha_request_kwargs(),
            ).wait()
        except Exception as error:
            if not nebius_request_error_code_is(error, "NOT_FOUND"):
                raise RuntimeError("Boot disk absence could not be proven") from error
        else:
            raise RuntimeError("A new guest requires its own recorded boot disk creation")
        disk_key = "disk:" + guest
        planned.append(
            Request.freeze(
                disk_key, manager._build_boot_disk_create_request(disk_name, provisioning, image_id)
            )
        )
        addresses = {}
        references = [Reference("spec.boot_disk.existing_disk.id", disk_key, "disk")]
        if network.subnet.startswith("@"):
            references.append(
                Reference("spec.network_interfaces.0.subnet_id", "gateway-subnet", "subnet")
            )
        requested_ips = manager._desired_public_ips(spec, index, 1)
        for public in (True, False):
            label = "public" if public else "private"
            name = guest + "-eth0-" + ("ip" if public else "private-ip")
            desired_ip = requested_ips[0] if public and requested_ips else None
            if desired_ip:
                allocation, _ = manager._find_requested_public_allocation(
                    allocation_service, None, name, desired_ip, strict=True
                )
            else:
                allocation = manager._get_allocation_by_name(allocation_service, name, strict=True)
            if allocation is not None:
                wanted = allocation.spec.ipv4_public if public else allocation.spec.ipv4_private
                if (
                    allocation.metadata.parent_id != manager.project_id
                    or wanted is None
                    or wanted.subnet_id != network.subnet
                    or not manager._allocation_has_stable_assignment_state(allocation)
                    or manager._allocation_is_attached(allocation)
                    or allocation.metadata.id in reused_allocations
                ):
                    raise RuntimeError(
                        "Existing allocation cannot satisfy the exact new member policy"
                    )
                identifier = allocation.metadata.id
                reused_allocations.add(identifier)
                observed[identifier] = snapshot("allocation", allocation)
            else:
                allocation_key = label + ":" + guest
                allocation_reference = (
                    (Reference("spec.ipv4_" + label + ".subnet_id", "gateway-subnet", "subnet"),)
                    if network.subnet.startswith("@")
                    else ()
                )
                planned.append(
                    Request.freeze(
                        allocation_key,
                        requests.create_allocation(
                            manager.project_id,
                            name,
                            network.subnet,
                            public=public,
                            address=desired_ip,
                        ),
                        references=allocation_reference,
                    )
                )
                identifier = "@" + allocation_key
                references.append(
                    Reference(
                        "spec.network_interfaces.0."
                        + ("public_ip_address" if public else "ip_address")
                        + ".allocation_id",
                        allocation_key,
                        "allocation",
                    )
                )
            addresses[label] = identifier
        request = manager._build_instance_create_request(
            guest, provisioning, "@" + disk_key, [addresses["public"]], [addresses["private"]]
        )
        planned.append(
            Request.freeze("compute:" + guest, request, references=tuple(references), guest=guest)
        )
    plan = {
        "project_id": manager.project_id,
        "gateway_name": spec.name,
        "configuration_sha256": configuration_sha256,
        "artifact_sha256": artifact_sha256,
        "requests": [item.mapping() for item in planned],
        "observed": observed,
        "inventories": network.inventories,
        "bootstraps": bootstraps,
    }
    requests_from_plan(plan)
    return plan
