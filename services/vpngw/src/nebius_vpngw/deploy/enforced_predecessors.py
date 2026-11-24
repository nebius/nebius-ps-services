"""Exact displaced-route obligations, retained across ordinary apply generations.

An absent predecessor is meaningful: removal deletes only the enforced route.
A present predecessor is restored from its full frozen request only after an
independent read proves that the original source route has not changed.
"""

from __future__ import annotations

from copy import deepcopy

from ..apply_approval import digest
from .ordinary_route_conversion import copy_inventory, slot, table_label
from .ordinary_route_ownership import snapshot
from .ordinary_route_policy import Policy, prefix


def validate(records: dict) -> None:
    if not isinstance(records, dict) or len(records) > 10000:
        raise ValueError("Invalid enforced predecessor inventory")
    for key, record in records.items():
        if (
            set(record) != {"subnet_id", "destination", "table_id", "predecessor"}
            or key != digest([record["subnet_id"], record["destination"]])
            or not record["subnet_id"]
            or not record["table_id"]
            or record["table_id"].startswith("@")
            or prefix(record["destination"]) != record["destination"]
        ):
            raise ValueError("Invalid enforced predecessor identity")
        before = record["predecessor"]
        if before is not None:
            from .ordinary_route_conversion import resolve
            from .replacement_cloud import message_digest

            request = resolve(before["request"], {}, {})
            if (
                set(before) != {"request", "snapshot"}
                or before["request"]["type"] != "CreateRouteRequest"
                or request.spec.destination.cidr != record["destination"]
                or before["snapshot"]["destination"] != record["destination"]
                or request.metadata.parent_id != before["snapshot"]["parent_id"]
                or request.metadata.name != before["snapshot"]["name"]
                or dict(request.metadata.labels) != before["snapshot"]["labels"]
                or message_digest(request.spec) != before["snapshot"]["spec_sha256"]
            ):
                raise ValueError("Invalid enforced predecessor request")


def augment(plan: dict, observation: dict, previous: dict) -> dict:
    """Freeze capture/restoration intent before approval, without cloud effects."""
    validate(previous)
    if not previous and not plan["policy"].get("enforced"):
        return plan
    policy = Policy.parse(plan["policy"])
    plan = deepcopy(plan)
    next_records = {}
    restore_evidence = {}
    for target in plan["infrastructure"]["targets"]:
        subnet, table = target["subnet_id"], target["table_id"]
        mandatory = policy.mandatory((subnet,), plan["infrastructure"].get("enforced_sources", {}))
        for destination in mandatory:
            key = digest([subnet, destination])
            record = deepcopy(previous.get(key))
            if record is not None and record["table_id"] != table:
                raise RuntimeError("Enforced source attachment changed outside its owner")
            if record is None:
                rows = [
                    r
                    for r in observation["routes"][table]
                    if r.spec.destination.cidr == destination
                ]
                if len(rows) > 1:
                    raise RuntimeError("Enforced predecessor route is ambiguous")
                before = None
                if rows and str(rows[0].metadata.id) not in plan["ownership"]:
                    route = rows[0]
                    before = {
                        "snapshot": snapshot(route),
                        "request": copy_inventory((route,))[str(route.metadata.id)],
                    }
                record = {
                    "subnet_id": subnet,
                    "destination": destination,
                    "table_id": table,
                    "predecessor": before,
                }
            copies = [
                copy_id
                for copy_id, copy in plan["conversions"].items()
                if subnet in copy["subnets"]
            ]
            if len(copies) > 1:
                raise RuntimeError("Enforced source has ambiguous private-table copies")
            if copies:
                record["table_id"] = slot(copies[0])
            next_records[key] = record
    for key, record in previous.items():
        if key in next_records:
            continue
        table, subnet, destination = record["table_id"], record["subnet_id"], record["destination"]
        observed_subnet = observation["subnet_resources"].get(subnet)
        if observed_subnet is None or str(observed_subnet.spec.route_table_id) != table:
            raise RuntimeError("Enforced source changed before predecessor restoration")
        before = record["predecessor"]
        if before is None:
            continue
        from .ordinary_route_conversion import resolve
        from .ordinary_route_plan import request_record

        request = resolve(before["request"], {}, {})
        request.metadata.parent_id = table
        rows = [r for r in observation["routes"][table] if r.spec.destination.cidr == destination]
        conversion_sources = plan.get("ha_conversion", {}).get("routes", {})
        copies = [
            copy_id for copy_id, copy in plan["conversions"].items() if subnet in copy["subnets"]
        ]
        if len(copies) > 1:
            raise RuntimeError("Enforced source has ambiguous restoration copies")
        if len(rows) > 1 or any(
            str(r.metadata.id) not in plan["ownership"]
            and str(r.metadata.id) not in conversion_sources
            for r in rows
        ):
            raise RuntimeError("Enforced route drift blocks predecessor restoration")
        replaces = str(rows[0].metadata.id) if rows else None
        if copies and replaces in conversion_sources:
            replaces = None
        if replaces and "retire-" + replaces not in plan["requests"]:
            raise RuntimeError("Predecessor restoration conflicts with current VPN intent")
        plan["requests"]["restore-" + key[:32]] = request_record(
            request,
            replaces=replaces,
            reason="enforced-restored",
            **({"table_source": copies[0]} if copies else {}),
        )
        restore_evidence[key] = before["snapshot"]
    plan["enforced_predecessors_next"] = next_records
    plan["enforced_restoration_evidence"] = restore_evidence
    return plan


def committed(plan: dict, operations: dict) -> dict:
    result = deepcopy(plan["enforced_predecessors_next"])
    for record in result.values():
        if record["table_id"].startswith("@private-table:"):
            label = table_label(record["table_id"].removeprefix("@private-table:"))
            candidates = [
                op
                for op in operations.values()
                if op["label"] == label and op["status"] == "settled"
            ]
            if len(candidates) != 1 or not candidates[0]["resource_id"]:
                raise RuntimeError("Enforced predecessor lacks a private-table creation receipt")
            record["table_id"] = candidates[0]["resource_id"]
    validate(result)
    return result


def verify_restoration(sdk, plan: dict) -> None:
    """Read only original route identities; names never confer authority."""
    from nebius.api.nebius.vpc.v1 import GetRouteRequest, RouteServiceClient

    from .vm_ha_cloud import vm_ha_request_kwargs

    for expected in plan.get("enforced_restoration_evidence", {}).values():
        actual = (
            RouteServiceClient(sdk)
            .get(GetRouteRequest(id=expected["id"]), **vm_ha_request_kwargs())
            .wait()
        )
        if snapshot(actual) != expected:
            raise RuntimeError("Displaced predecessor changed; restoration is not authorized")
