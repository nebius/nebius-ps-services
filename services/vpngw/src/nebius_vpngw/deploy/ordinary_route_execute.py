"""Bounded execution of reviewed ordinary routes through durable cloud receipts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..apply_approval import digest
from .ordinary_route_conversion import expected_infrastructure, readback_matches, resolve
from .ordinary_route_ownership import RouteJournal, allocation_id, snapshot
from .replacement_cloud import Cloud


def request_from_record(record: dict) -> Any:
    return resolve(record, {}, {})


def _inventory(observation: dict, plan: dict, infrastructure: dict | None = None) -> dict[str, Any]:
    infrastructure = infrastructure if infrastructure is not None else plan["infrastructure"]
    if (
        observation["infrastructure"] != infrastructure
        or {str(k): v for k, v in observation["allocations"].items()} != plan["allocations"]
        or set(observation["routes"]) != set(infrastructure["table_ids"])
    ):
        raise RuntimeError("Approved route infrastructure or gateway authority changed")
    result = {}
    for table, routes in observation["routes"].items():
        destinations = set()
        for route in routes:
            item = snapshot(route)
            if (
                item["parent_id"] != table
                or item["id"] in result
                or item["destination"] in destinations
            ):
                raise RuntimeError("Route inventory contains ambiguous identities")
            destinations.add(item["destination"])
            result[item["id"]] = route
    return result


def _admit(
    cloud: Cloud, plan: dict, observe: Callable[[], dict], *, uncertain: bool = False
) -> dict[str, Any]:
    from copy import deepcopy

    observation = observe()
    operations = deepcopy(cloud.records)
    # An uncertain subnet CAS may already be visible. It still requires its
    # authoritative receipt; this projection only permits exact replay after
    # the complete destination table and all untouched source routes verify.
    for operation in operations.values():
        if operation["status"] != "settled":
            if not uncertain or operation["status"] in {"rejected", "failed"}:
                raise RuntimeError("Unsettled route operation blocks a new effect")
            if operation["request_type"] == "UpdateSubnetRequest":
                request = resolve(plan["requests"][operation["label"]], plan, operations)
                subnet = observation["subnet_resources"][request.metadata.id]
                if readback_matches(subnet, request):
                    operation["status"] = "settled"
    infrastructure = expected_infrastructure(plan, operations)
    actual = _inventory(observation, plan, infrastructure)
    expected = dict(plan["observed"])
    for operation in operations.values():
        if operation["status"] != "settled":
            if uncertain:
                wanted = plan["requests"][operation["label"]]
                request = resolve(wanted, plan, operations)
                if wanted["type"] == "CreateRouteRequest":
                    matches = [
                        route for route in actual.values() if readback_matches(route, request)
                    ]
                    if len(matches) > 1:
                        raise RuntimeError("Uncertain route creation has ambiguous effects")
                    if matches:
                        item = snapshot(matches[0])
                        expected[item["id"]] = item
                elif wanted["type"] == "DeleteRouteRequest" and request.id not in actual:
                    expected.pop(request.id, None)
            continue
        wanted = plan["requests"][operation["label"]]
        request = resolve(wanted, plan, operations)
        if wanted["type"] == "DeleteRouteRequest":
            expected.pop(request.id, None)
        elif wanted["type"] == "CreateRouteRequest":
            identifier = operation["resource_id"]
            route = actual.get(identifier)
            if route is None or not readback_matches(route, request):
                raise RuntimeError("Created route has no exact independent readback")
            expected[identifier] = snapshot(route)
        elif wanted["type"] == "CreateRouteTableRequest":
            table = observation["table_resources"].get(operation["resource_id"])
            if table is None or not readback_matches(table, request):
                raise RuntimeError("Created route table has no exact independent readback")
        elif wanted["type"] == "UpdateSubnetRequest":
            subnet = observation["subnet_resources"].get(request.metadata.id)
            if subnet is None or not readback_matches(subnet, request):
                raise RuntimeError("Subnet attachment differs from its exact approved request")
    if {identifier: snapshot(route) for identifier, route in actual.items()} != expected:
        raise RuntimeError("Route state changed outside the approved operations")
    return actual


def execute(
    sdk: Any,
    journal: RouteJournal,
    plan: dict,
    *,
    approval: str,
    observe: Callable[[], dict],
) -> None:
    from nebius.api.nebius.vpc.v1 import (
        RouteServiceClient,
        RouteTableServiceClient,
        SubnetServiceClient,
    )

    previous = journal.snapshot()
    transaction = previous["transaction"] if previous else None
    if (
        transaction is not None
        and transaction["phase"] == "complete"
        and transaction["plan"] == plan
        and transaction["approval"] == approval
    ):
        # Lost final response: independently recheck the terminal receipt without
        # republishing it or creating a second cloud operation namespace.
        assert previous is not None
        journal.state = previous
        _admit(Cloud(sdk, journal), plan, observe)
        return
    if not plan["requests"] and not plan["absent"]:
        # No lock directory, journal publication or cloud mutation for no-op.
        actual = _inventory(observe(), plan)
        if {identifier: snapshot(route) for identifier, route in actual.items()} != plan[
            "observed"
        ]:
            raise RuntimeError("Route state changed after unchanged planning")
        if transaction is not None and transaction["phase"] == "pending":
            with journal:
                journal.begin(plan, approval=approval)
                journal.state["transaction"]["phase"] = "complete"
                journal.save()
        return
    with journal:
        journal.begin(plan, approval=approval)
        cloud = Cloud(sdk, journal)
        # JSON operation summaries omit unknown protobuf fields. Reconstruct
        # uncertain requests from the full approved template and exact receipts;
        # Cloud still checks the original request hash before replaying its key.
        cloud.restore_request = lambda operation: resolve(
            plan["requests"][operation["label"]], plan, cloud.records
        )
        current = _admit(cloud, plan, observe, uncertain=True)
        # A replayed delete has no provider CAS field. Recheck its exact original
        # object before resubmission; an absent object still needs the receipt.
        for operation in cloud.records.values():
            if (
                operation["status"] == "intent"
                and operation["request_type"] == "DeleteRouteRequest"
            ):
                wanted = plan["requests"][operation["label"]]
                route = current.get(wanted["request"]["id"])
                if route is not None and snapshot(route) != wanted["before"]:
                    raise RuntimeError("Pending route deletion lost its exact ownership proof")
        cloud.settle()
        _admit(cloud, plan, observe)
        service = RouteServiceClient(sdk)
        table_service = RouteTableServiceClient(sdk)
        subnet_service = SubnetServiceClient(sdk)
        # Next-hop and destination fields are immutable in the current VPC API.
        # A reviewed ownership change deletes the old exact route before create.
        replacements = {
            record["replaces"]
            for record in plan["requests"].values()
            if record["type"] == "CreateRouteRequest" and record.get("replaces")
        }
        priorities = {
            "CreateRouteTableRequest": 0,
            "DeleteRouteRequest": 3,
            "CreateRouteRequest": 2,
            "UpdateSubnetRequest": 4,
        }
        ordered = sorted(
            plan["requests"],
            key=lambda key: (
                1
                if plan["requests"][key]["type"] == "DeleteRouteRequest"
                and plan["requests"][key]["request"]["id"] in replacements
                else priorities[plan["requests"][key]["type"]],
                key,
            ),
        )
        for label in ordered:
            wanted = plan["requests"][label]
            actual = _admit(cloud, plan, observe)
            key = digest([approval, 0, label])
            operation = cloud.records.get(key)
            request = resolve(wanted, plan, cloud.records)
            if operation is None:
                if wanted["type"] == "DeleteRouteRequest":
                    route = actual.get(request.id)
                    if route is None or snapshot(route) != wanted["before"]:
                        raise RuntimeError("Route retirement requires its exact current object")
                    if wanted.get("covered_by") and not any(
                        current.metadata.parent_id == wanted["before"]["parent_id"]
                        and current.spec.destination.cidr == wanted["covered_by"]
                        and allocation_id(current)
                        == plan["allocations"][str(wanted["instance_index"])]
                        for current in actual.values()
                    ):
                        raise RuntimeError(
                            "Working specifics cannot retire before summary coverage verifies"
                        )
                    submit = service.delete
                elif wanted["type"] == "CreateRouteTableRequest":
                    submit = table_service.create
                elif wanted["type"] == "UpdateSubnetRequest":
                    observed_subnet = observe()["subnet_resources"][request.metadata.id]
                    if (
                        observed_subnet.metadata.resource_version
                        != request.metadata.resource_version
                    ):
                        raise RuntimeError("Subnet changed before private-table attachment")
                    submit = subnet_service.update
                else:
                    submit = service.create
                cloud.mutate(label, request, submit)
            actual = _admit(cloud, plan, observe)
            if wanted["type"] == "CreateRouteRequest" and "instance_index" in wanted:
                identifier = cloud.records[key]["resource_id"]
                journal.created(cloud, label, actual[identifier], index=wanted["instance_index"])
            elif wanted["type"] == "DeleteRouteRequest":
                if request.id in actual:
                    raise RuntimeError("Retired route is still present")
                journal.state["bindings"].pop(request.id, None)
            if label not in journal.state["transaction"]["verified"]:
                journal.state["transaction"]["verified"].append(label)
                journal.save()
        actual = _admit(cloud, plan, observe)
        for identifier in plan["absent"]:
            if identifier in actual:
                raise RuntimeError("A retired ledger identity reappeared")
            journal.state["bindings"].pop(identifier, None)
        for identifier in plan["released"]:
            journal.state["bindings"].pop(identifier, None)
        journal.state["transaction"]["phase"] = "complete"
        journal.save()
