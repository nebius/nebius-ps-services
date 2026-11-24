"""Restore proven displaced routes under the existing fenced destroy workflow."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..apply_approval import digest
from .enforced_predecessors import validate as validate_predecessors
from .ha_enforced_tables import TableJournal
from .ordinary_route_conversion import readback_matches, resolve
from .ordinary_route_ownership import RouteJournal, allocation_id, snapshot
from .ordinary_route_plan import request_record
from .replacement import Journal, metadata_only
from .replacement_cloud import Cloud, effect_key, message_digest
from .vm_ha_cloud import vm_ha_request_kwargs


def owner(project, gateway, ha):
    return TableJournal(project, gateway) if ha else RouteJournal(project, gateway)


def freeze(backend, routes, *, ha: bool) -> dict | None:
    source = owner(backend.project_id, backend.spec.name, ha)
    saved = source.snapshot()
    if saved is None:
        return None
    tx = saved["transaction"]
    records = {}
    if ha:
        targets = {
            t["workload_subnet_id"]: t["route_table_id"]
            for t in saved["bindings"].get("targets", [])
        }
        for subnet, values in saved["bindings"].get("predecessors", {}).items():
            for destination, before in values.items():
                records[digest([subnet, destination])] = {
                    "subnet_id": subnet,
                    "destination": destination,
                    "table_id": targets[subnet],
                    "predecessor": before,
                }
    else:
        records = saved.get("enforced_predecessors", {})
    if not records:
        if ha and tx is not None and tx["phase"] != "complete" and tx["plan"]["desired"]:
            raise RuntimeError("Finish the pending enforced route transition before destroy")
        return None
    if tx is not None and tx["phase"] != "complete":
        raise RuntimeError("Finish the pending enforced route transition before destroy")
    validate_predecessors(records)
    observed = [backend._route_by_id(r) for r in routes]
    requests = {}
    for key, record in records.items():
        exact = [
            r
            for r in observed
            if r is not None
            and r.metadata.parent_id == record["table_id"]
            and r.spec.destination.cidr == record["destination"]
        ]
        if len(exact) != 1:
            raise RuntimeError("Destroy enforcement lacks its exact planned route deletion")
        if record["predecessor"] is not None:
            request = resolve(record["predecessor"]["request"], {}, {})
            request.metadata.parent_id = record["table_id"]
            if allocation_id(request) in {r.allocation_id for r in routes}:
                raise RuntimeError(
                    "Destroy cannot restore a predecessor using a removed allocation"
                )
            requests[key] = request_record(request)
    return {
        "ha": ha,
        "source_sha256": digest(saved),
        "records": records,
        "requests": requests,
        "sources": source_evidence(backend, records),
    }


def validate(value: dict) -> None:
    if (
        set(value) != {"ha", "source_sha256", "records", "requests", "sources"}
        or type(value["ha"]) is not bool
    ):
        raise ValueError("Invalid destroy enforcement restoration")
    validate_predecessors(value["records"])
    if set(value["sources"]) != {r["subnet_id"] for r in value["records"].values()}:
        raise ValueError("Destroy restoration source scope is incomplete")
    expected = {k for k, r in value["records"].items() if r["predecessor"] is not None}
    if set(value["requests"]) != expected:
        raise ValueError("Incomplete destroy enforcement restoration")
    for key, row in value["requests"].items():
        record = value["records"][key]
        before = resolve(record["predecessor"]["request"], {}, {})
        before.metadata.parent_id = record["table_id"]
        if request_record(before) != row:
            raise ValueError("Destroy restoration changed the original route request")


class RestoreJournal(Journal):
    schema = "nebius-vpngw.enforced-destroy.v1"

    def __init__(self, project, gateway, *, root=None):
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home() / ".local/state/nebius-vpngw/enforced-destroy" / project / gateway,
        )

    def validate(self, state):
        metadata_only(state)
        if (
            state["schema"] != self.schema
            or state["project_id"] != self.project
            or state["gateway_name"] != self.gateway
        ):
            raise ValueError("Destroy restoration journal crossed its authority")
        tx = state["transaction"]
        if tx is not None:
            validate(tx["plan"])
            if tx["member"] != 0 or tx["phase"] not in {"pending", "complete"}:
                raise ValueError("Invalid destroy restoration phase")
            for key, op in tx["operations"].items():
                if op["label"] not in tx["plan"]["requests"] or key != effect_key(
                    tx["approval"], 0, op["label"]
                ):
                    raise ValueError("Invalid destroy restoration receipt")


def source_evidence(backend, records):
    from nebius.api.nebius.vpc.v1 import (
        GetRouteTableRequest,
        GetSubnetRequest,
        RouteTableServiceClient,
        SubnetServiceClient,
    )

    result = {}
    for record in records.values():
        subnet = (
            SubnetServiceClient(backend.client)
            .get(GetSubnetRequest(id=record["subnet_id"]), **vm_ha_request_kwargs())
            .wait()
        )
        if (
            subnet.metadata.parent_id != backend.project_id
            or subnet.spec.route_table_id != record["table_id"]
            or subnet.status.route_table.id != record["table_id"]
        ):
            raise RuntimeError("Destroy enforced source attachment drifted")
        table = (
            RouteTableServiceClient(backend.client)
            .get(GetRouteTableRequest(id=record["table_id"]), **vm_ha_request_kwargs())
            .wait()
        )
        consumers = {
            r["subnet_id"] for r in records.values() if r["table_id"] == record["table_id"]
        }
        if (
            table.metadata.id != record["table_id"]
            or table.metadata.parent_id != backend.project_id
            or table.spec.network_id != subnet.spec.network_id
            or set(table.status.assignment.subnets) != consumers
        ):
            raise RuntimeError("Destroy enforced table consumer scope changed")
        result[record["subnet_id"]] = {
            "spec_sha256": message_digest(subnet.spec),
            "revision": str(subnet.metadata.resource_version),
            "consumers": sorted(consumers),
            "table": record["table_id"],
        }
    return result


def verify_sources(backend, declaration):
    from nebius.api.nebius.vpc.v1 import GetRouteRequest

    validate(declaration)
    if source_evidence(backend, declaration["records"]) != declaration["sources"]:
        raise RuntimeError("Destroy enforced source specification changed")
    for record in declaration["records"].values():
        before = record["predecessor"]
        if before is not None:
            route = backend.routes.get(
                GetRouteRequest(id=before["snapshot"]["id"]), **vm_ha_request_kwargs()
            ).wait()
            if snapshot(route) != before["snapshot"]:
                raise RuntimeError("Destroy displaced predecessor drifted")


def execute(backend, plan):
    declaration = plan.enforced_restore
    if declaration is None:
        return
    journal = RestoreJournal(plan.project_id, plan.gateway_name)
    with journal:
        tx = journal.state["transaction"]
        if tx is None or tx["approval"] != plan.digest:
            if tx is not None and tx["phase"] != "complete":
                raise RuntimeError("Another destroy restoration is pending")
            if (
                digest(owner(plan.project_id, plan.gateway_name, declaration["ha"]).snapshot())
                != declaration["source_sha256"]
            ):
                raise RuntimeError("Destroy enforcement owner changed after planning")
            if tx is not None:
                journal.archive()
            tx = {
                "plan": deepcopy(declaration),
                "approval": plan.digest,
                "member": 0,
                "phase": "pending",
                "operations": {},
            }
            journal.state["transaction"] = tx
            journal.save()
        if tx["plan"] != declaration:
            raise RuntimeError("Destroy restoration intent changed")

        def admit(_request=None):
            if not all(backend.instance_fenced(r) for r in plan.compute):
                raise RuntimeError("Destroy restoration lost its Compute fencing")
            if not all(backend.route_absent(r) for r in plan.routes):
                raise RuntimeError("Destroy restoration preceded owned route removal")
            verify_sources(backend, declaration)

        cloud = Cloud(
            backend.client,
            journal,
            member_index=0,
            admit_request=admit,
            restore_request=lambda op: resolve(declaration["requests"][op["label"]], {}, {}),
        )
        admit()
        cloud.settle()
        for key, row in declaration["requests"].items():
            request = resolve(row, {}, {})
            cloud.mutate(key, request, backend.routes.create)
        verify(backend, plan, saved=journal.state)
        tx["phase"] = "complete"
        journal.save()


def verify(backend, plan, *, saved=None):
    from nebius.api.nebius.vpc.v1 import GetRouteRequest

    declaration = plan.enforced_restore
    if declaration is None:
        return
    verify_sources(backend, declaration)
    saved = saved or RestoreJournal(plan.project_id, plan.gateway_name).snapshot()
    tx = saved["transaction"] if saved else None
    if tx is None or tx["approval"] != plan.digest or tx["plan"] != declaration:
        raise RuntimeError("Destroy restoration lacks its exact receipt")
    for key, row in declaration["requests"].items():
        op = tx["operations"].get(effect_key(plan.digest, 0, key))
        if op is None or op["status"] != "settled" or not op["resource_id"]:
            raise RuntimeError("Destroy restoration is not settled")
        actual = backend.routes.get(
            GetRouteRequest(id=op["resource_id"]), **vm_ha_request_kwargs()
        ).wait()
        if not readback_matches(actual, resolve(row, {}, {})):
            raise RuntimeError("Destroy restored predecessor differs")


def retire(backend, plan):
    declaration = plan.enforced_restore
    if declaration is None:
        return
    verify(backend, plan)
    with owner(plan.project_id, plan.gateway_name, declaration["ha"]) as source:
        retired = (
            source.state.get("bindings", {}).get("destroyed_enforcement")
            if declaration["ha"]
            else source.state.get("enforced_destroyed")
        )
        if retired == plan.digest:
            return
        if digest(source.state) != declaration["source_sha256"]:
            raise RuntimeError("Destroy enforcement owner changed before retirement")
        source.archive()
        if declaration["ha"]:
            source.state["bindings"] = {"destroyed_enforcement": plan.digest}
            source.state["transaction"] = None
        else:
            source.state["enforced_predecessors"] = {}
            source.state["enforced_destroyed"] = plan.digest
        source.save()
