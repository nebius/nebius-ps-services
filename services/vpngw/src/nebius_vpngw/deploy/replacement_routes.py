"""Ordinary replacement consumes the canonical exact route ownership ledger."""

from __future__ import annotations

from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .ordinary_route_conversion import readback_matches
from .ordinary_route_ownership import RouteJournal, allocation_id, owned, snapshot
from .ordinary_route_plan import request_record
from .replacement_cloud import Cloud, message_digest
from .vm_ha_cloud import (
    clone_nebius_sdk_message,
    nebius_request_error_code_is,
    vm_ha_request_kwargs,
)


def inventory(sdk: Any, project: str, allocations: set[str], *, gateway: str) -> tuple[dict, ...]:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc import v1 as vpc

    ledger = RouteJournal(project, gateway)
    transaction = ledger.transaction()
    if transaction and transaction["phase"] != "complete":
        raise RuntimeError("Resume ordinary route apply before planning replacement")
    ownership = ledger.inventory()
    tables: Any = collect_nebius_pages(
        lambda token: vpc.RouteTableServiceClient(sdk).list(
            vpc.ListRouteTablesRequest(parent_id=project, page_token=token, page_size=100),
            **vm_ha_request_kwargs(),
        ),
        context="Replacement route table",
        item_identity=nebius_resource_id,
    )

    def collect_routes(table_id: str) -> Any:
        return collect_nebius_pages(
            lambda token: vpc.RouteServiceClient(sdk).list(
                vpc.ListRoutesRequest(parent_id=table_id, page_token=token, page_size=100),
                **vm_ha_request_kwargs(),
            ),
            context="Replacement route",
            item_identity=nebius_resource_id,
        )

    result = []
    for table in tables:
        if table.metadata.parent_id != project:
            raise RuntimeError("Replacement route inventory crossed project authority")
        routes = collect_routes(table.metadata.id)
        for route in routes:
            if route.metadata.parent_id != table.metadata.id:
                raise RuntimeError("Replacement route inventory crossed table authority")
            if allocation_id(route) not in allocations:
                continue
            if not owned(route, ownership, project=project, gateway=gateway):
                raise RuntimeError(
                    "An externally owned route depends on the source gateway; its route owner "
                    "must move it before VM replacement. Route names do not grant ownership."
                )
            result.append(
                {
                    **snapshot(route),
                    "revision": route.metadata.resource_version,
                    "ownership": ownership[route.metadata.id],
                    "create": request_record(
                        vpc.CreateRouteRequest(
                            metadata=ResourceMetadata(
                                parent_id=route.metadata.parent_id,
                                name=route.metadata.name,
                                labels=dict(route.metadata.labels),
                            ),
                            spec=clone_nebius_sdk_message(route.spec),
                        )
                    ),
                }
            )
    return tuple(sorted(result, key=lambda item: item["id"]))


def _get(service: Any, identifier: str) -> Any:
    from nebius.api.nebius.vpc.v1 import GetRouteRequest

    try:
        return service.get(GetRouteRequest(id=identifier), **vm_ha_request_kwargs()).wait()
    except Exception as error:
        if nebius_request_error_code_is(error, "NOT_FOUND"):
            return None
        raise


def _original(route: Any, item: dict) -> bool:
    return (
        route is not None
        and snapshot(route) == item["ownership"]["snapshot"]
        and route.metadata.resource_version == item["revision"]
    )


def admit_replay(cloud: Cloud, approved: tuple[dict, ...]) -> None:
    """The delete API has no CAS; never replay against a changed source object."""
    from nebius.api.nebius.vpc.v1 import RouteServiceClient

    indexed = {item["id"]: item for item in approved}
    for operation in cloud.records.values():
        if (
            operation["status"] == "intent"
            and operation.get("request_type") == "DeleteRouteRequest"
        ):
            identifier = operation["request_metadata"]["id"]
            item = indexed.get(identifier)
            if item is None or operation["label"] != "retire-route-" + identifier:
                raise RuntimeError("Route deletion has no exact replacement authority")
            route = _get(RouteServiceClient(cloud.sdk), identifier)
            if route is not None and not _original(route, item):
                raise RuntimeError("Approved route changed before replacement deletion replay")


def rebind(
    cloud: Cloud, approved: tuple[dict, ...], old: str, new: str, *, verify_only: bool = False
) -> None:
    from nebius.api.nebius.vpc.v1 import DeleteRouteRequest, RouteServiceClient

    from .ordinary_route_execute import request_from_record

    service = RouteServiceClient(cloud.sdk)
    ledger = RouteJournal(cloud.journal.project, cloud.journal.gateway)
    admit_replay(cloud, approved)
    cloud.settle()
    for item in approved:
        if item["allocation_id"] != old:
            continue
        retire, create = "retire-route-" + item["id"], "replace-route-" + item["id"]
        request = request_from_record(item["create"])
        if message_digest(request.spec) != item["spec_sha256"]:
            raise RuntimeError("Approved replacement route template changed")
        request.spec.next_hop.allocation.id = new
        current = _get(service, item["id"])
        if cloud.completed(retire) is None:
            if verify_only:
                raise RuntimeError("Replacement route retirement has no completed receipt")
            if (
                not _original(current, item)
                or ledger.inventory().get(item["id"]) != item["ownership"]
            ):
                raise RuntimeError("Approved route changed before replacement cutover")
            cloud.mutate(retire, DeleteRouteRequest(id=item["id"]), service.delete)
        if _get(service, item["id"]) is not None:
            raise RuntimeError("Original route remains after approved retirement")
        identifier = cloud.completed(create)
        if identifier is None:
            if verify_only:
                raise RuntimeError("Replacement route creation has no completed receipt")
            identifier = cloud.mutate(create, request, service.create)
        observed = _get(service, identifier)
        if observed is None or not readback_matches(observed, request):
            raise RuntimeError("Replacement route differs from its approved creation request")
        ledger.replaced(cloud, create, observed, predecessor=item["ownership"])
