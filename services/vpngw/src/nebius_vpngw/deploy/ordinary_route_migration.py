"""Freeze ordinary creation receipts inside the existing HA migration approval."""

from __future__ import annotations

import json

from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from .ordinary_route_ownership import RouteJournal
from .ordinary_runtime_handoff import ObservedJournal

KEY = "ordinary-route-source"
SCHEMA = "nebius-vpngw.ordinary-route-migration.v1"


def capture(project: str, gateway: str, admission: dict[str, dict]) -> dict | None:
    if not admission:
        return None
    sources = {}
    for node, evidence in admission.items():
        runtime = evidence.get("route_runtime")
        if runtime and runtime["authority"] is not None:
            installed = binding.validate(runtime["authority"]["binding"])
            if (installed["project"], installed["gateway"], installed["node"]) != (
                project,
                gateway,
                node,
            ):
                raise RuntimeError("Migration route source crossed deployment authority")
            sources[node] = runtime["journal"]
        else:
            sources[node] = RouteJournal(project, gateway).snapshot()
    value = {"schema": SCHEMA, "sources": sources}
    for node in sources:
        records(value, project, gateway, node)
    return value


def records(value: dict, project: str, gateway: str, node: str) -> dict:
    if set(value) != {"schema", "sources"} or (
        value["schema"] != SCHEMA or node not in value["sources"]
    ):
        raise RuntimeError("Migration route source identity changed")
    journal = ObservedJournal(project, gateway, value["sources"][node])
    transaction = journal.transaction()
    if transaction and transaction["phase"] != "complete":
        raise RuntimeError("Migration route source contains unsettled operations")
    return journal.inventory()


def persisted(bindings: dict[str, str], project: str, gateway: str, node: str) -> dict:
    raw = bindings.get(KEY)
    return records(json.loads(raw), project, gateway, node) if raw is not None else {}


def matches(record: dict | None, observed: dict[str, str], snapshot_digest: str | None) -> bool:
    if record is None or snapshot_digest != digest(record["snapshot"]):
        return False
    snapshot = record["snapshot"]
    return all(
        snapshot[key] == observed[field]
        for key, field in (
            ("id", "route_id"),
            ("parent_id", "route_table_id"),
            ("name", "name"),
            ("destination", "prefix"),
            ("allocation_id", "allocation_id"),
        )
    )
