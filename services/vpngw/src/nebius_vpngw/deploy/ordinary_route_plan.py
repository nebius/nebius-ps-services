"""Pure ordinary route planning; observed routes never confer ownership."""

from __future__ import annotations

import base64
import ipaddress
import json
from typing import Any

from ..apply_approval import digest
from .ordinary_route_ownership import allocation_id, labels, owned, snapshot
from .ordinary_route_policy import AbsenceEvidence, Policy, prefix
from .replacement_cloud import message_digest


def request_types() -> dict[str, Any]:
    from nebius.api.nebius.vpc import v1 as vpc

    return {
        "CreateRouteRequest": vpc.CreateRouteRequest,
        "DeleteRouteRequest": vpc.DeleteRouteRequest,
        "CreateRouteTableRequest": vpc.CreateRouteTableRequest,
        "UpdateSubnetRequest": vpc.UpdateSubnetRequest,
    }


def request_record(request: Any, **scope: Any) -> dict:
    from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

    if type(request).__name__ not in request_types():
        raise RuntimeError("Approved route request type is not permitted")
    return {
        "type": type(request).__name__,
        "sha256": message_digest(request),
        "protobuf": base64.b64encode(
            request.__pb2_message__.SerializeToString(deterministic=True)
        ).decode("ascii"),
        "request": MessageToDict(request.__pb2_message__, preserving_proto_field_name=True),
        **scope,
    }


def intent(plan: dict) -> dict:
    """The pre-activation authority; BGP selection happens only inside it."""
    return {
        key: plan[key]
        for key in (
            "project",
            "gateway",
            "policy",
            "infrastructure",
            "allocations",
            "active_tables",
            "observed",
            "ownership",
            "conversions",
            "options",
        )
    }


def inspect(
    *,
    project: str,
    gateway: str,
    policy: Policy,
    learned: dict[str, tuple[str, ...]],
    allocations: dict[int, str],
    active_tables: tuple[str, ...],
    routes: dict[str, tuple[Any, ...]],
    ownership: dict[str, dict],
    infrastructure: dict,
    absence: AbsenceEvidence | None = None,
) -> dict:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        AllocationNextHop,
        CreateRouteRequest,
        DeleteRouteRequest,
        DestinationMatch,
        NextHop,
        RouteSpec,
    )

    if (
        not project
        or not gateway
        or not active_tables
        or len(set(active_tables)) != len(active_tables)
        or not set(active_tables) <= set(routes)
        or not {record["snapshot"]["parent_id"] for record in ownership.values()} <= set(routes)
    ):
        raise RuntimeError("Route planning requires complete exact target-table inventories")
    selected = policy.select(learned)
    desired = {table: dict(selected) for table in active_tables}
    observed: dict[str, dict] = {}
    by_prefix: dict[str, dict[str, Any]] = {}
    requests: dict[str, dict] = {}
    absent = []
    for table, items in sorted(routes.items()):
        by_prefix[table] = {}
        for route in items:
            item = snapshot(route)
            destination = prefix(item["destination"])
            if (
                item["parent_id"] != table
                or item["id"] in observed
                or destination in by_prefix[table]
            ):
                raise RuntimeError("Route inventory has foreign or ambiguous identities")
            observed[item["id"]] = item
            by_prefix[table][destination] = route
            owned(route, ownership, project=project, gateway=gateway)
    # Deployment observations retain admitted destinations. The runtime owner
    # may withdraw only with its complete, consecutive absence evidence.
    for identifier, record in sorted(ownership.items()):
        item = record["snapshot"]
        table, destination, index = item["parent_id"], item["destination"], record["instance_index"]
        withdrawn = absence is not None and absence.admits(policy, learned, destination)
        retained = table in desired and policy.retains(destination, index) and not withdrawn
        summary = (
            next(
                (
                    p
                    for p, owner in selected.items()
                    if owner == index
                    and p != destination
                    and ipaddress.IPv4Network(destination).subnet_of(ipaddress.IPv4Network(p))
                ),
                None,
            )
            if policy.summarize
            else None
        )
        if summary is not None and (absence is None or withdrawn):
            retained = False
        if retained:
            previous = desired[table].get(destination)
            if previous is not None and previous != index:
                raise RuntimeError(
                    "Admitted destination changed gateway owner; review its retirement"
                )
            desired[table][destination] = index
        if identifier not in observed:
            absent.append(identifier)
        elif not retained:
            # Ordinary previews supply no absence evidence, so only the runtime
            # owner can admit a learned-prefix withdrawal.
            requests["retire-" + identifier] = request_record(
                DeleteRouteRequest(id=identifier),
                before=item,
                reason="bgp-withdrawn"
                if withdrawn
                else "configuration-summarized"
                if summary
                else "configuration-retired",
                instance_index=index,
                covered_by=summary,
            )
    coverage: dict[str, dict[str, Any]] = {}
    for table, destinations in sorted(desired.items()):
        for connection in policy.connections:
            admitted = {
                p
                for p, index in destinations.items()
                if index == connection.instance_index and connection.accepts(p)
            }
            if len(admitted) > connection.max_prefixes:
                raise RuntimeError("Retained and new destinations exceed the approved prefix limit")
        for destination, index in sorted(destinations.items()):
            allocation = allocations.get(index)
            if not allocation:
                raise RuntimeError("Route destination has no exact current gateway allocation")
            current = by_prefix[table].get(destination)
            key = digest([table, destination])[:32]
            for narrower, overlapping in by_prefix[table].items():
                if (
                    narrower != destination
                    and ipaddress.IPv4Network(narrower).subnet_of(
                        ipaddress.IPv4Network(destination)
                    )
                    and allocation_id(overlapping) != allocation
                    and "retire-" + str(overlapping.metadata.id) not in requests
                ):
                    raise RuntimeError("Remote coverage conflicts with a more-specific route")
            replaces = None
            if current is not None:
                if allocation_id(current) != allocation:
                    replaces = str(current.metadata.id)
                    if "retire-" + replaces not in requests:
                        raise RuntimeError("Remote destination conflicts with an existing route")
                else:
                    coverage[key] = {
                        "table": table,
                        "destination": destination,
                        "allocation": allocation,
                        "kind": "owned" if resource_is_owned(current, ownership) else "external",
                        "route": str(current.metadata.id),
                    }
                    continue
            request = CreateRouteRequest(
                metadata=ResourceMetadata(
                    parent_id=table,
                    name="vpngw-" + key,
                    labels=labels(project, gateway, table, destination, index),
                ),
                spec=RouteSpec(
                    destination=DestinationMatch(cidr=destination),
                    next_hop=NextHop(allocation=AllocationNextHop(id=allocation)),
                ),
            )
            requests["create-" + key] = request_record(
                request, instance_index=index, replaces=replaces
            )
            coverage[key] = {
                "table": table,
                "destination": destination,
                "allocation": allocation,
                "kind": "create",
                "route": None,
            }
    result: dict = {
        "project": project,
        "gateway": gateway,
        "policy": policy.mapping(),
        "infrastructure": infrastructure,
        "allocations": {str(k): v for k, v in allocations.items()},
        "active_tables": list(active_tables),
        "observed": observed,
        "ownership": ownership,
        "absent": absent,
        "coverage": coverage,
        "requests": requests,
        "conversions": {},
        "released": [],
        "options": {"force_copy": False},
    }
    if absence is not None:
        result["options"]["absence"] = {
            "binding": absence.binding,
            "policy": absence.policy,
            "selected": absence.selected,
            "absent": sorted(absence.absent),
            "observations": absence.observations,
        }
    return json.loads(json.dumps(result))


def resource_is_owned(route: Any, ownership: dict[str, dict]) -> bool:
    # inspect() already independently validated every owned object above.
    return str(route.metadata.id) in ownership
