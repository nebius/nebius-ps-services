"""Exact creation authority for ordinary routes, shared with replacement.

Names and matching next hops are never ownership. A route is writable only when
the cloud object agrees with a durable successful creation receipt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..apply_approval import digest
from .replacement import Journal, metadata_only
from .replacement_cloud import Cloud, message_digest, resource_id

MANAGED = "nebius-vpngw-managed"
GATEWAY = "nebius-vpngw-gateway"
INTENT = "nebius-vpngw-route-intent"
SCHEMA = "nebius-vpngw.ordinary-routes.v2"


def labels(project: str, gateway: str, table: str, destination: str, index: int) -> dict[str, str]:
    return {
        MANAGED: "ordinary-v1",
        GATEWAY: digest([project, gateway])[:32],
        INTENT: digest([table, destination, index])[:32],
    }


def allocation_id(route: Any) -> str:
    """An egress route has no allocation and never proves VPN coverage."""
    next_hop = route.spec.next_hop
    allocation = getattr(next_hop, "allocation", None)
    if allocation is not None and allocation.id:
        return str(allocation.id)
    if getattr(next_hop, "default_egress_gateway", False):
        return ""
    raise RuntimeError("Route observation has an incomplete or unsupported next hop")


def snapshot(route: Any) -> dict:
    if (
        not resource_id(route)
        or not route.metadata.parent_id
        or route.metadata.resource_version <= 0
    ):
        raise RuntimeError("Route observation has incomplete identity or revision")
    return {
        "id": resource_id(route),
        "parent_id": str(route.metadata.parent_id),
        "name": str(route.metadata.name),
        "labels": dict(route.metadata.labels),
        "spec_sha256": message_digest(route.spec),
        "destination": str(route.spec.destination.cidr),
        "allocation_id": allocation_id(route),
    }


def owned(route: Any, records: dict[str, dict], *, project: str, gateway: str) -> bool:
    record = records.get(resource_id(route))
    if record is None:
        return False
    expected = record["snapshot"]
    if snapshot(route) != expected or expected["labels"] != labels(
        project,
        gateway,
        expected["parent_id"],
        expected["destination"],
        record["instance_index"],
    ):
        raise RuntimeError("Owned route differs from its exact creation authority")
    return True


class RouteJournal(Journal):
    schema = SCHEMA

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root or Path.home() / ".local/state/nebius-vpngw/routes" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        try:
            if (
                set(state)
                != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
                or state["schema"] != self.schema
                or state["project_id"] != self.project
                or state["gateway_name"] != self.gateway
                or type(state["revision"]) is not int
                or state["revision"] < 0
                or not isinstance(state["bindings"], dict)
            ):
                raise ValueError("Invalid route journal identity")
            for identifier, record in state["bindings"].items():
                observed = record["snapshot"]
                if (
                    set(record)
                    != {"snapshot", "instance_index", "creation_operation", "request_sha256"}
                    or set(observed)
                    != {
                        "id",
                        "parent_id",
                        "name",
                        "labels",
                        "spec_sha256",
                        "destination",
                        "allocation_id",
                    }
                    or identifier != observed["id"]
                    or not identifier
                    or not observed["parent_id"]
                    or not observed["allocation_id"]
                    or type(record["instance_index"]) is not int
                    or record["instance_index"] < 0
                    or not record["creation_operation"]
                    or any(
                        re.fullmatch(r"[a-f0-9]{64}", value) is None
                        for value in (record["request_sha256"], observed["spec_sha256"])
                    )
                    or observed["labels"]
                    != labels(
                        self.project,
                        self.gateway,
                        observed["parent_id"],
                        observed["destination"],
                        record["instance_index"],
                    )
                ):
                    raise ValueError("Invalid route creation authority")
            transaction = state["transaction"]
            if transaction is None:
                return
            if (
                set(transaction)
                != {"intent", "plan", "approval", "phase", "member", "operations", "verified"}
                or transaction["phase"] not in {"pending", "complete"}
                or transaction["member"] != 0
                or not isinstance(transaction["intent"], dict)
                or not isinstance(transaction["operations"], dict)
                or not isinstance(transaction["verified"], list)
                or re.fullmatch(r"[a-f0-9]{64}", transaction["approval"]) is None
            ):
                raise ValueError("Invalid route transaction")
            plan = transaction["plan"]
            if plan is None:
                if (
                    transaction["phase"] != "pending"
                    or transaction["operations"]
                    or transaction["verified"]
                ):
                    raise ValueError("Unselected route intent cannot contain effects")
                return
            from .ordinary_route_plan import intent

            if not isinstance(plan, dict) or intent(plan) != transaction["intent"]:
                raise ValueError("Route selection expanded its frozen authority")
            requests = transaction["plan"]["requests"]
            if not isinstance(requests, dict) or not set(transaction["verified"]) <= set(requests):
                raise ValueError("Invalid verified route effects")
            for key, operation in transaction["operations"].items():
                wanted = requests[operation["label"]]
                from .ordinary_route_conversion import resolve
                from .ordinary_route_plan import request_record

                resolved = request_record(resolve(wanted, plan, transaction["operations"]))
                if (
                    key != digest([transaction["approval"], 0, operation["label"]])
                    or operation["request_type"]
                    not in {
                        "CreateRouteRequest",
                        "DeleteRouteRequest",
                        "CreateRouteTableRequest",
                        "UpdateSubnetRequest",
                    }
                    or operation["request_type"] != wanted["type"]
                    or operation["request_metadata"] != resolved["request"]
                    or operation["request_sha256"] != resolved["sha256"]
                    or operation["status"]
                    not in {"intent", "accepted", "settled", "rejected", "failed"}
                    or operation["status"] in {"accepted", "settled", "failed"}
                    and not operation["cloud_operation_id"]
                    or operation["status"] == "settled"
                    and operation["request_type"]
                    in {"CreateRouteRequest", "CreateRouteTableRequest"}
                    and not operation["resource_id"]
                ):
                    raise ValueError("Route operation exceeds approved authority")
            if transaction["phase"] == "complete" and (
                set(transaction["verified"]) != set(requests)
                or {operation["label"] for operation in transaction["operations"].values()}
                != set(requests)
                or len(transaction["operations"]) != len(requests)
                or any(
                    operation["status"] != "settled"
                    for operation in transaction["operations"].values()
                )
            ):
                raise ValueError("Unsettled route effects cannot complete")
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise ValueError("Ordinary route journal is invalid") from error

    def inventory(self) -> dict[str, dict]:
        state = self.snapshot()
        return state["bindings"] if state else {}

    def transaction(self) -> dict | None:
        state = self.snapshot()
        return state["transaction"] if state else None

    def begin(self, plan: dict, *, approval: str) -> None:
        from .ordinary_route_plan import intent

        self.begin_intent(intent(plan), approval=approval)
        self.select(plan)

    def begin_intent(self, intent: dict, *, approval: str) -> None:
        current = self.state["transaction"]
        if current and current["phase"] == "pending":
            if current["approval"] != approval or current["intent"] != intent:
                raise RuntimeError("Resume the original approved ordinary route plan")
            return
        if current is not None:
            self.archive()
        self.state["transaction"] = {
            "intent": json.loads(json.dumps(intent)),
            "plan": None,
            "approval": approval,
            "phase": "pending",
            "member": 0,
            "operations": {},
            "verified": [],
        }
        self.save()

    def select(self, plan: dict) -> None:
        from .ordinary_route_plan import intent

        transaction = self.state["transaction"]
        if intent(plan) != transaction["intent"]:
            raise RuntimeError("Route selection expanded its original approved scope")
        if transaction["plan"] is not None:
            if transaction["plan"] != plan:
                raise RuntimeError("Resume the original selected route operation")
            return
        transaction["plan"] = json.loads(json.dumps(plan))
        self.save()

    def created(self, cloud: Cloud, label: str, route: Any, *, index: int) -> None:
        record = creation_binding(
            cloud, label, route, project=self.project, gateway=self.gateway, index=index
        )
        previous = self.state["bindings"].get(resource_id(route))
        if previous is not None and previous != record:
            raise RuntimeError("A route creation receipt cannot adopt an existing resource")
        if previous is None:
            self.state["bindings"][resource_id(route)] = record
            self.save()

    def replaced(self, cloud: Cloud, label: str, route: Any, *, predecessor: dict) -> None:
        """Publish replacement authority only from its exact settled child receipt."""
        record = creation_binding(
            cloud,
            label,
            route,
            project=self.project,
            gateway=self.gateway,
            index=predecessor["instance_index"],
        )
        old_id, new_id = predecessor["snapshot"]["id"], resource_id(route)
        with self:
            pending = self.state["transaction"]
            if pending and pending["phase"] != "complete":
                raise RuntimeError("An ordinary route transaction blocks replacement ownership")
            previous = self.state["bindings"].get(old_id)
            if previous is None and self.state["bindings"].get(new_id) == record:
                return
            if previous != predecessor or new_id in self.state["bindings"]:
                raise RuntimeError("Replacement route ledger authority changed")
            self.archive()
            del self.state["bindings"][old_id]
            self.state["bindings"][new_id] = record
            self.save()


def creation_binding(
    cloud: Cloud, label: str, route: Any, *, project: str, gateway: str, index: int
) -> dict:
    from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]
    from nebius.api.nebius.vpc.v1 import CreateRouteRequest

    operation = cloud.records.get(
        digest([cloud.journal.state["transaction"]["approval"], cloud.member_index, label])
    )
    if (
        cloud.journal.project != project
        or cloud.journal.gateway != gateway
        or operation is None
        or operation["status"] != "settled"
        or operation["request_type"] != "CreateRouteRequest"
        or operation["resource_id"] != resource_id(route)
    ):
        raise RuntimeError("Route ownership requires its successful exact creation receipt")
    request = CreateRouteRequest()
    ParseDict(operation["request_metadata"], request.__pb2_message__)
    if (
        message_digest(request) != operation["request_sha256"]
        or message_digest(request.spec) != message_digest(route.spec)
        or request.metadata.parent_id != route.metadata.parent_id
        or request.metadata.name != route.metadata.name
        or dict(request.metadata.labels) != dict(route.metadata.labels)
        or dict(route.metadata.labels)
        != labels(project, gateway, route.metadata.parent_id, route.spec.destination.cidr, index)
    ):
        raise RuntimeError("Created route does not match the approved request")
    return {
        "snapshot": snapshot(route),
        "instance_index": index,
        "creation_operation": operation["cloud_operation_id"],
        "request_sha256": operation["request_sha256"],
    }


def read_ownership(project: str, gateway: str, *, root: Path | None = None) -> dict[str, dict]:
    """Read-only ledger access; absent state grants no route authority."""
    return RouteJournal(project, gateway, root=root).inventory()
