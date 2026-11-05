"""Exact approved ordinary route rebinding during a gateway replacement."""

from __future__ import annotations

from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .replacement_cloud import Cloud, message_digest
from .vm_ha_cloud import clone_nebius_sdk_message, vm_ha_request_kwargs


def inventory(sdk: Any, project: str, allocations: set[str]) -> tuple[dict, ...]:
    from nebius.api.nebius.vpc.v1 import (
        ListRoutesRequest,
        ListRouteTablesRequest,
        RouteServiceClient,
        RouteTableServiceClient,
    )

    tables: Any = collect_nebius_pages(
        lambda token: RouteTableServiceClient(sdk).list(
            ListRouteTablesRequest(parent_id=project, page_token=token, page_size=100)
        ),
        context="Replacement route table",
        item_identity=nebius_resource_id,
    )

    def collect_routes(table_id: str) -> Any:
        return collect_nebius_pages(
            lambda token: RouteServiceClient(sdk).list(
                ListRoutesRequest(parent_id=table_id, page_token=token, page_size=100)
            ),
            context="Replacement route",
            item_identity=nebius_resource_id,
        )

    result = []
    for table in tables:
        routes = collect_routes(table.metadata.id)
        for route in routes:
            allocation = str(route.spec.next_hop.allocation.id)
            if allocation not in allocations:
                continue
            if route.metadata.resource_version <= 0:
                raise RuntimeError("Replacement route has no authoritative revision")
            result.append(
                {
                    "id": route.metadata.id,
                    "parent_id": route.metadata.parent_id,
                    "name": route.metadata.name,
                    "revision": route.metadata.resource_version,
                    "labels": dict(route.metadata.labels),
                    "spec_sha256": message_digest(route.spec),
                    "destination": route.spec.destination.cidr,
                    "allocation_id": allocation,
                    "managed": route.metadata.name
                    == ("vpngw-" + route.spec.destination.cidr.replace("/", "-"))[:63],
                }
            )
    return tuple(sorted(result, key=lambda item: item["id"]))


def rebind(
    cloud: Cloud, approved: tuple[dict, ...], old: str, new: str, *, verify_only: bool = False
) -> None:
    from nebius.api.nebius.vpc.v1 import GetRouteRequest, RouteServiceClient, UpdateRouteRequest

    service = RouteServiceClient(cloud.sdk)
    for item in approved:
        if item["allocation_id"] != old or not item["managed"]:
            continue
        label = "rebind-route-" + item["id"]
        route = service.get(GetRouteRequest(id=item["id"]), **vm_ha_request_kwargs()).wait()
        if (
            route.metadata.id != item["id"]
            or route.metadata.parent_id != item["parent_id"]
            or route.metadata.name != item["name"]
            or dict(route.metadata.labels) != item["labels"]
        ):
            raise RuntimeError("Approved route ownership changed")
        if cloud.completed(label) is None:
            if verify_only:
                raise RuntimeError("Approved replacement route update has no completed receipt")
            if (
                route.metadata.resource_version != item["revision"]
                or message_digest(route.spec) != item["spec_sha256"]
            ):
                raise RuntimeError("Approved route changed before replacement cutover")
            metadata = clone_nebius_sdk_message(route.metadata)
            spec = clone_nebius_sdk_message(route.spec)
            spec.next_hop.allocation.id = new
            cloud.mutate(label, UpdateRouteRequest(metadata=metadata, spec=spec), service.update)
        observed = service.get(GetRouteRequest(id=item["id"]), **vm_ha_request_kwargs()).wait()
        # Compare every spec field after substituting only the approved old target.
        predecessor = clone_nebius_sdk_message(observed.spec)
        if predecessor.next_hop.allocation.id != new:
            raise RuntimeError("Replacement route next hop did not verify")
        predecessor.next_hop.allocation.id = old
        if message_digest(predecessor) != item["spec_sha256"]:
            raise RuntimeError("Replacement route changed beyond its approved next hop")
