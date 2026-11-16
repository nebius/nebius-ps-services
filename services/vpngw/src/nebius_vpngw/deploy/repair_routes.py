"""Settle old ordinary route effects before a confirmed repair takes ownership."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ordinary_route_ownership import RouteJournal, owned
from .ordinary_runtime_handoff import Handoff, import_journal
from .replacement_cloud import Cloud


def settle(journal: RouteJournal, sdk: Any) -> dict:
    """Retire a plan, retaining successful creation authority and failed history.

    The caller has inhibited all guests and holds the gateway command lock.
    An uncertain submitted request retains its original idempotency identity.
    No unsubmitted plan item is executed to finish an obsolete desired state.
    """
    from nebius.api.nebius.vpc.v1 import GetRouteRequest, RouteServiceClient

    from .vm_ha_cloud import nebius_request_error_code_is, vm_ha_request_kwargs

    with journal:
        transaction = journal.state["transaction"]
        if transaction is not None:
            cloud = Cloud(sdk, journal)
            cloud.settle(require_success=False)
            if any(item["status"] in {"intent", "accepted"} for item in cloud.records.values()):
                raise RuntimeError("Previous route effects are still settling")
            for record in tuple(cloud.records.values()):
                if record["status"] != "settled":
                    continue
                if record["request_type"] == "CreateRouteRequest":
                    wanted = transaction["plan"]["requests"][record["label"]]
                    # Private-table conversion also copies non-gateway routes.
                    # Keep their settled effects without claiming VPN ownership,
                    # matching the canonical ordinary route executor.
                    if "instance_index" not in wanted:
                        continue
                    try:
                        route = (
                            RouteServiceClient(sdk)
                            .get(
                                GetRouteRequest(id=record["resource_id"]), **vm_ha_request_kwargs()
                            )
                            .wait()
                        )
                    except Exception as error:
                        if not nebius_request_error_code_is(error, "NOT_FOUND"):
                            raise
                        continue
                    journal.created(cloud, record["label"], route, index=wanted["instance_index"])
                elif record["request_type"] == "DeleteRouteRequest":
                    journal.state["bindings"].pop(record["request_metadata"]["id"], None)
        # A ledger is authority only while the exact resource still exists.
        # Missing objects are stale receipts, never permission to adopt others.
        for identifier in tuple(journal.state["bindings"]):
            try:
                route = (
                    RouteServiceClient(sdk)
                    .get(GetRouteRequest(id=identifier), **vm_ha_request_kwargs())
                    .wait()
                )
            except Exception as error:
                if not nebius_request_error_code_is(error, "NOT_FOUND"):
                    raise
                del journal.state["bindings"][identifier]
            else:
                owned(
                    route,
                    journal.state["bindings"],
                    project=journal.project,
                    gateway=journal.gateway,
                )
        if journal._before is not None:
            journal.archive()
        journal.state["transaction"] = None
        journal.save()
        return deepcopy(journal.state)


def prepare(owner: Any) -> None:
    """Preserve both workstation and runtime receipts before new publication."""
    from . import repair_route_metadata as metadata
    from .repair_retirement import settle_receipts

    if owner.context.get("routes_settled"):
        return
    admission = owner.admission
    local = RouteJournal(admission.intent["project"], admission.intent["gateway"])
    transports = {
        instance.hostname: Handoff(
            admission.ssh, admission.targets[instance.hostname], instance, admission.local
        )
        for instance in admission.instances
    }
    if "old_routes" not in owner.context:
        owner.context["old_routes"] = {
            "local": metadata.observe(local),
            "guests": {
                name: transport.call({"action": "repair-inspect", "operation": owner.operation})
                for name, transport in transports.items()
            },
        }
        owner.save()
    observed = owner.context["old_routes"]
    merged: dict[str, Any] = {
        "schema": local.schema,
        "project_id": local.project,
        "gateway_name": local.gateway,
        "revision": 0,
        "bindings": {},
        "transaction": None,
    }
    sources = {
        "workstation": observed["local"],
        **{name: value["metadata"] for name, value in observed["guests"].items()},
    }
    for name, source in sources.items():
        settle_receipts(owner.cloud.sdk, source["effects"])
        for ordinal, snapshot in enumerate(source["states"]):
            copy = RouteJournal(
                local.project,
                local.gateway,
                root=owner.directory / "old-routes" / name / str(ordinal),
            )
            if copy.snapshot() is None:
                import_journal(copy, snapshot)
            recovered = settle(copy, owner.cloud.sdk)
            for identifier, record in recovered["bindings"].items():
                previous = merged["bindings"].get(identifier)
                if previous is not None and previous != record:
                    raise RuntimeError("Repair route receipts disagree on an existing resource")
                merged["bindings"][identifier] = record
    metadata.retire(
        local, owner.directory / "retired" / "routes", observed["local"]["fingerprints"], merged
    )
    for name, transport in transports.items():
        transport.call(
            {
                "action": "repair-reset",
                "operation": owner.operation,
                "reset": {"observed": observed["guests"][name], "journal": merged},
            }
        )
    owner.context["routes_settled"] = True
    owner.save()
