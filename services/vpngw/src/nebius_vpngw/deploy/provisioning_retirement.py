"""Preserve settled creation ownership until an approved successor reuses it.

The pending journal is never relabeled complete. Its owner publishes a separate
retirement receipt, archives the original, and allows only a successor that
accounts for every retained creation in its current observations.
"""

from __future__ import annotations

import json
import os

from ..apply_approval import digest
from .operator_guest import publish, read_file
from .operator_keys import protected_directory
from .provisioning_inventory import matches, read
from .provisioning_journal import requests_from_plan
from .recovery import ReplanRejected
from .replacement import metadata_only
from .replacement_cloud import message_digest

SCHEMA = "nebius-vpngw.provisioning-retirement.v1"


def _path(journal, state):
    return journal.root / "retirements" / (digest(state) + ".json")


def receipt(journal, state=None) -> dict | None:
    state = journal.snapshot() if state is None else state
    if state is None or state["transaction"] is None:
        return None
    raw = read_file(_path(journal, state))
    if raw is None:
        return None
    value = json.loads(raw["content"])
    metadata_only(value)
    if (
        raw["uid"] != os.getuid()
        or raw["mode"] != 0o600
        or set(value) != {"schema", "child", "proof"}
        or value["schema"] != SCHEMA
        or value["child"] != digest(state)
        or value["proof"].get("action") != "archive-provisioning-prefix-predecessor"
    ):
        raise ReplanRejected("Provisioning retirement receipt changed")
    return value


def require_prefix(state) -> None:
    transaction = state["transaction"]
    if transaction is None or transaction["phase"] != "pending":
        raise ReplanRejected("Provisioning retirement requires its pending original prefix")
    records = tuple(transaction["operations"].values())
    submitted = {record["label"] for record in records}
    if (
        len(submitted) != len(records)
        or submitted != set(transaction["verified"])
        or any(record["status"] != "settled" for record in records)
        or any(
            recipe.guest is not None and recipe.key in submitted
            for recipe in requests_from_plan(transaction["plan"])
        )
    ):
        raise ReplanRejected("Provisioning prefix is not settled", code="predecessor-not-settled")


def seal(journal, expected, proof) -> None:
    if journal._lock is None or journal.state != expected or journal.snapshot() != expected:
        raise ReplanRejected("Provisioning owner changed before retirement")
    require_prefix(expected)
    metadata_only(proof)
    if proof.get("action") != "archive-provisioning-prefix-predecessor":
        raise ReplanRejected("Provisioning retirement has no coordinator authority")
    old = receipt(journal, expected)
    value = {"schema": SCHEMA, "child": digest(expected), "proof": proof}
    if old is not None:
        if old != value:
            raise ReplanRejected("Provisioning retirement changed its original approval")
        return
    journal.archive()
    path = _path(journal, expected)
    protected_directory(path.parent)
    publish(
        path,
        None,
        dict(
            content=json.dumps(value, sort_keys=True), uid=os.getuid(), gid=os.getgid(), mode=0o600
        ),
    )


def require_successor(journal, plan: dict, *, state=None) -> None:
    state = journal.state if state is None else state
    if receipt(journal, state) is None:
        raise ReplanRejected("Provisioning needs its original command or approved retirement")
    require_prefix(state)
    for binding in state["bindings"].values():
        observed = plan["observed"].get(binding["resource_id"])
        if observed is None or observed["kind"] != binding["kind"]:
            raise ReplanRejected(
                "Successor does not account for an owned provisioning resource",
                code="predecessor-infrastructure-changed",
            )


def retained_disks(journal, sdk) -> dict:
    """Read exact settled disk receipts; never acquire ownership from a name."""
    state = journal.snapshot()
    if state is None or receipt(journal, state) is None:
        return {}
    require_prefix(state)
    disks = {}
    for recipe in requests_from_plan(state["transaction"]["plan"]):
        binding = state["bindings"].get(recipe.key)
        if binding is None or binding["kind"] != "disk":
            continue
        request = recipe.resolve(state["bindings"])
        resource = read(sdk, "disk", binding["resource_id"])
        if (
            message_digest(request) != binding["request_sha256"]
            or not matches(resource, request)
            or resource.status.read_write_attachment
            or resource.status.read_only_attachments
        ):
            raise ReplanRejected(
                "Retained boot disk differs from its exact creation receipt",
                code="predecessor-infrastructure-changed",
            )
        if resource.metadata.name in disks:
            raise ReplanRejected("Retained boot disk ownership is ambiguous")
        disks[resource.metadata.name] = resource
    return disks
