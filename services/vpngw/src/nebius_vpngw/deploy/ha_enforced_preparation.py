"""Admit the allocation revision caused by receipt-owned route preparation.

Nebius updates the next-hop allocation as route references are created.
The unattached child tables remain the sole effect owner. This narrow successor
records their proof without relaxing generic allocation or lifecycle guards.
"""

from __future__ import annotations

import json
from dataclasses import replace

from ..apply_approval import digest
from .ha_enforced_rebind import PLAN_KEY
from .ha_enforced_tables import TableJournal, Transition, resolve_request, validate_plan
from .ordinary_route_conversion import readback_matches
from .replacement_cloud import effect_key, message_digest
from .vm_ha_lifecycle import VMHALifecycleStatus, normalize_vm_ha_observation

CHECKPOINT = "enforced-route-allocation-revision"
PROOF_KEY = "enforced-route-preparation"
REVISION = "/shared_allocation/resource_revision"


def successor(previous, *, proof, observation):
    tx = previous.transaction
    if (
        tx is None
        or previous.status not in {VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING}
        or tx.pending_effect
        or tx.observation_guard
        or tx.accepted_cloud_operation_id
        or tx.replacement_host_recovery
    ):
        raise ValueError("HA preparation revision has competing lifecycle authority")
    if not isinstance(proof, dict) or set(proof) != {
        "plan",
        "operations",
        "operation",
        "references",
    }:
        raise ValueError("HA preparation revision proof is malformed")
    plan, records = proof["plan"], proof["operations"]
    validate_plan(plan)
    bindings = dict(tx.resource_bindings)
    allocation = bindings.get("shared-allocation-id")
    if (
        not allocation
        or plan != json.loads(bindings.get(PLAN_KEY, "null"))
        or proof["operation"] != tx.operation_id
        or plan["project"] != previous.project_id
        or plan["gateway"] != previous.gateway_name
        or plan["cluster"] != previous.cluster_id
        or plan["allocation"] not in {"", allocation}
        or previous.allocation_id not in {"", allocation}
    ):
        raise ValueError("HA preparation revision changed its approved identity")
    expected = {effect_key(digest(plan), 0, label) for label in plan["requests"]}
    if not set(records) <= expected:
        raise ValueError("HA preparation revision contains unrelated effects")
    references = proof["references"]
    if (
        not isinstance(references, dict)
        or not references
        or not references.keys() <= records.keys()
    ):
        raise ValueError("HA preparation revision lacks exact reference readback")
    referenced = set()
    for key, op in records.items():
        row = plan["requests"].get(op["label"])
        if row is None or row["type"] == "UpdateSubnetRequest":
            raise ValueError("HA preparation revision cannot admit subnet attachment")
        request = resolve_request(plan, records, op["label"], allocation=allocation)
        if (
            key != effect_key(digest(plan), 0, op["label"])
            or op["request_type"] != row["type"]
            or op["request_sha256"] != message_digest(request)
            or op["status"] not in {"intent", "accepted", "settled"}
            or op["status"] == "settled"
            and (not op["resource_id"] or not op["cloud_operation_id"])
        ):
            raise ValueError("HA preparation revision lacks exact creation receipts")
        if key in references:
            if (
                row["type"] != "CreateRouteRequest"
                or getattr(request.spec.next_hop.allocation, "id", None) != allocation
                or not isinstance(references[key], str)
                or not references[key]
                or op["status"] == "settled"
                and references[key] != op["resource_id"]
            ):
                raise ValueError("HA preparation revision reference differs from its request")
            referenced.add(key)
    before, after = dict(tx.observation), dict(normalize_vm_ha_observation(observation))
    old_revision, new_revision = (
        json.loads(before.get(REVISION, "null")),
        json.loads(after.get(REVISION, "null")),
    )
    if (
        not referenced
        or {k: v for k, v in before.items() if k != REVISION}
        != {k: v for k, v in after.items() if k != REVISION}
        or not str(old_revision).isdecimal()
        or not str(new_revision).isdecimal()
        or not 0 < int(old_revision) < int(new_revision)
        or json.loads(after.get("/shared_allocation/allocation_id", "null")) != allocation
    ):
        raise ValueError("HA preparation revision changed unrelated cloud evidence")
    bindings[PROOF_KEY] = json.dumps(proof, sort_keys=True, separators=(",", ":"))
    return replace(
        previous,
        transaction=replace(
            tx,
            predecessor_sha256=previous.record_sha256,
            revision=tx.revision + 1,
            checkpoint=CHECKPOINT,
            observation=tuple(sorted(after.items())),
            resource_bindings=tuple(sorted(bindings.items())),
        ),
    )


def reconcile(manager, observation) -> bool:
    """Read back the complete child footprint before publishing the successor."""
    lifecycle = manager._vm_ha_journal
    state, tx = lifecycle.state, lifecycle.state.transaction
    raw = dict(tx.resource_bindings).get(PLAN_KEY) if tx else None
    if raw is None:
        return False
    journal = TableJournal(state.project_id, state.gateway_name)
    saved = journal.snapshot()
    child = saved.get("transaction") if saved else None
    if child is None or child["phase"] not in {"preparing", "prepared"}:
        return False
    assert saved is not None
    journal.state = saved  # Verified snapshot for read-only candidate validation.
    owner = Transition(
        manager._get_client(),
        journal,
        json.loads(raw),
        allocation=dict(tx.resource_bindings)["shared-allocation-id"],
        ordinary_bindings=dict(tx.resource_bindings),
    )
    # Only detached, exactly receipted candidates and unchanged source objects
    # qualify. Verification is read-only; settling remains with Transition.
    owner.verify(attached=False, uncertain=True)
    from .route_manager import NebiusSDKRouteBackend

    references = {}
    backend = NebiusSDKRouteBackend(manager._get_client())
    for key, op in child["operations"].items():
        if op["request_type"] != "CreateRouteRequest":
            continue
        request = resolve_request(
            child["plan"], child["operations"], op["label"], allocation=owner.allocation
        )
        if getattr(request.spec.next_hop.allocation, "id", None) != owner.allocation:
            continue
        matches = [
            r
            for r in backend._raw_routes(request.metadata.parent_id)
            if readback_matches(r, request)
        ]
        if len(matches) == 1:
            references[key] = str(matches[0].metadata.id)
    proof = {
        "plan": child["plan"],
        "operations": child["operations"],
        "operation": tx.operation_id,
        "references": references,
    }
    after = successor(state, proof=proof, observation=observation)
    if manager._stable_vm_ha_effect_observation() != observation:
        raise RuntimeError("HA preparation revision changed during independent readback")
    lifecycle.transition(after)
    return True


def validate_successor(previous, after) -> bool:
    tx = after.transaction
    if tx is None or tx.checkpoint != CHECKPOINT:
        return False
    from .vm_ha_prehost import _cloud_observation

    proof = json.loads(dict(tx.resource_bindings).get(PROOF_KEY, "null"))
    if after != successor(previous, proof=proof, observation=_cloud_observation(tx.observation)):
        raise ValueError("HA preparation revision expanded its successor authority")
    return True
