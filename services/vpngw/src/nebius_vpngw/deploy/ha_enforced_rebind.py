"""The narrow lifecycle successor permitted by a settled HA table cutover."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .. import apply_lock
from ..apply_approval import digest
from ..schema import VMHARouteTarget, VMHARuntimeBinding
from .ha_enforced_tables import resolve_request, validate_plan
from .replacement_cloud import effect_key, message_digest
from .vm_ha_lifecycle import VMHALifecycleStatus, normalize_vm_ha_observation

PLAN_KEY = "enforced-route-plan"
CHECKPOINT = "enforced-route-tables-committed"
PROOF_KEY = "enforced-route-cutover"
LOCK_PREFIX = "enforced-route-lock:"


def validate_reprovision_plan(state):
    """Admit fresh declared intent after destruction, never old cutover authority."""
    bindings = dict(state.transaction.resource_bindings)
    observation = dict(state.transaction.observation)
    raw = bindings.get(PLAN_KEY)
    observed = json.loads(observation.get("/enforced_table_plan", "null"))
    if raw is None and observed is None:
        return
    if not isinstance(raw, str) or raw != observed:
        raise ValueError("Fresh enforced route plan differs from its approved observation")
    plan = json.loads(raw)
    validate_plan(plan)
    targets = dict(normalize_vm_ha_observation({"route_targets": plan["targets"]}))
    if (
        plan["project"] != state.project_id
        or plan["gateway"] != state.gateway_name
        or plan["cluster"] != state.cluster_id
        or plan["allocation"]
        or bindings.get("route-targets-digest") != digest(plan["targets"])
        or targets != {k: v for k, v in observation.items() if k.startswith("/route_targets/")}
    ):
        raise ValueError("Fresh enforced route plan crossed reprovision authority")


def created_route_bindings(transaction):
    """Identify current-operation outputs that cannot become migration inputs.

    Cutover replaces the parent's route observation. Rebuilding a staged payload
    from that observation must not add the child's newly created routes to its
    migration manifest. The validated parent proof supplies exact request-bound
    identities; routes from earlier operations keep their normal admission path.
    """
    bindings = dict(transaction.resource_bindings)
    raw = bindings.get(PROOF_KEY)
    if raw is None:
        return {}
    proof = json.loads(raw)
    if proof["operation"] != transaction.operation_id:
        return {}
    plan, operations = proof["plan"], proof["operations"]
    validate_plan(plan)
    if plan != json.loads(bindings.get(PLAN_KEY, "null")):
        raise ValueError("HA created route proof differs from the approved table plan")
    allocation = bindings["shared-allocation-id"]
    result = {}
    for label, row in plan["requests"].items():
        if row["type"] != "CreateRouteRequest":
            continue
        op = operations[effect_key(digest(plan), 0, label)]
        request = resolve_request(plan, operations, label, allocation=allocation)
        if (
            op["label"] != label
            or op["request_type"] != row["type"]
            or op["status"] != "settled"
            or not op["resource_id"]
            or not op["cloud_operation_id"]
            or op["request_sha256"] != message_digest(request)
            or op["resource_id"] in result
        ):
            raise ValueError("HA created route proof lacks an exact settled request")
        from .ordinary_route_ownership import allocation_id

        result[op["resource_id"]] = {
            "route_id": op["resource_id"],
            "route_table_id": request.metadata.parent_id,
            "name": request.metadata.name,
            "prefix": request.spec.destination.cidr,
            "allocation_id": allocation_id(request),
        }
    return result


def successor(previous, *, proof: dict, observation):
    tx = previous.transaction
    if tx is None or previous.status not in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
    }:
        raise ValueError("HA route rebind requires its pending deployment lifecycle")
    if (
        tx.pending_effect
        or tx.accepted_cloud_operation_id
        or tx.observation_guard
        or tx.replacement_host_recovery
    ):
        raise ValueError("HA route rebind has competing lifecycle effects")
    if not isinstance(proof, dict) or set(proof) != {"plan", "operations", "operation", "barrier"}:
        raise ValueError("HA route rebind proof is malformed")
    plan, operations = proof["plan"], proof["operations"]
    validate_plan(plan)
    bindings = dict(tx.resource_bindings)
    if (
        json.loads(bindings.get(PLAN_KEY, "null")) != plan
        or plan["project"] != previous.project_id
        or plan["gateway"] != previous.gateway_name
        or plan["cluster"] != previous.cluster_id
        or proof["operation"] != tx.operation_id
    ):
        raise ValueError("HA route rebind is outside the exact approved lifecycle plan")
    allocation = bindings.get("shared-allocation-id")
    if not allocation or previous.allocation_id not in {"", allocation}:
        raise ValueError("HA route rebind changed shared allocation")
    if set(operations) != {effect_key(digest(plan), 0, label) for label in plan["requests"]}:
        raise ValueError("HA route rebind lacks the complete operation set")
    for label in plan["requests"]:
        op = operations[effect_key(digest(plan), 0, label)]
        request = resolve_request(plan, operations, label, allocation=allocation)
        if (
            op["label"] != label
            or op["status"] != "settled"
            or not op["cloud_operation_id"]
            or not op["resource_id"]
            or op["request_sha256"] != message_digest(request)
        ):
            raise ValueError("HA route rebind contains an unsettled or changed cloud receipt")
    before_targets = tuple(
        sorted(json.dumps(t, sort_keys=True, separators=(",", ":")) for t in plan["targets"])
    )
    if proof["barrier"]["kind"] == "stopped-pair":
        if set(proof["barrier"]["nodes"]) != {m.node_id for m in previous.members}:
            raise ValueError("HA route cutover lacks both stopped members")
        for member in previous.members:
            row = proof["barrier"]["nodes"][member.node_id]
            lock = row.get("lock")
            if (
                not member.compute_id
                or row["compute_id"] != member.compute_id
                or row["operation"] != tx.operation_id
                or not row["boot_id"]
                or not isinstance(lock, dict)
                or set(lock)
                != {
                    "schema",
                    "apply_locked",
                    "cluster_id",
                    "node_id",
                    "generation_id",
                    "operation_id",
                }
                or lock["schema"] != "nebius-vpngw/vm-ha-apply-lock-v2"
                or lock["apply_locked"] is not True
                or lock["cluster_id"] != previous.cluster_id
                or lock["node_id"] != member.node_id
                or hashlib.sha256(apply_lock.encode(lock)).hexdigest()
                != bindings.get(LOCK_PREFIX + member.node_id)
            ):
                raise ValueError("HA route cutover stopped-member proof changed")
    else:
        raise ValueError("HA route cutover barrier is unsupported")
    targets = tuple(
        VMHARouteTarget.model_validate(t).model_copy(
            update={
                "route_table_id": operations[
                    effect_key(digest(plan), 0, "table-" + t["workload_subnet_id"])
                ]["resource_id"]
            }
        )
        if t["workload_subnet_id"] in plan["sources"]
        else VMHARouteTarget.model_validate(t)
        for t in plan["targets"]
    )
    after_targets = tuple(
        sorted(
            json.dumps(t.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for t in targets
        )
    )
    if previous.route_targets and previous.route_targets not in {before_targets, after_targets}:
        raise ValueError("HA route rebind predecessor targets differ")
    observed = normalize_vm_ha_observation(observation)

    def route_leaf(key):
        return key in {"/routes", "/route_targets"} or key.startswith(
            ("/routes/", "/route_targets/")
        )

    if {k: v for k, v in observed if not route_leaf(k)} != {
        k: v for k, v in tx.observation if not route_leaf(k)
    }:
        raise ValueError("HA route cutover changed unrelated cloud evidence")
    target_observation = normalize_vm_ha_observation(
        {"route_targets": [t.model_dump(mode="json") for t in targets]}
    )
    if {k: v for k, v in observed if k.startswith("/route_targets/")} != dict(target_observation):
        raise ValueError("HA route cutover target readback differs from its receipts")
    runtime_id = VMHARuntimeBinding.derive_route_runtime_id(
        previous.cluster_id, allocation, targets
    )
    bindings.update(
        {
            "route-runtime-id": runtime_id,
            "route-targets-digest": digest([t.model_dump(mode="json") for t in targets]),
            PROOF_KEY: json.dumps(proof, sort_keys=True, separators=(",", ":")),
        }
    )
    after = replace(
        tx,
        predecessor_sha256=previous.record_sha256,
        revision=tx.revision + 1,
        checkpoint=CHECKPOINT,
        observation=observed,
        resource_bindings=tuple(sorted(bindings.items())),
    )
    return replace(
        previous,
        allocation_id=allocation,
        route_runtime_id=runtime_id,
        route_targets=tuple(
            sorted(
                json.dumps(t.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                for t in targets
            )
        ),
        transaction=after,
    )


def validate_successor(previous, after) -> bool:
    tx = after.transaction
    if tx is None or tx.checkpoint != CHECKPOINT or previous.transaction is None:
        return False
    bindings = dict(tx.resource_bindings)
    proof = json.loads(bindings.get(PROOF_KEY, "null"))
    # Rebuild the expected state, including every unchanged lifecycle field.
    # The observation is already canonical; reconstruct only its JSON tree.
    from .vm_ha_prehost import _cloud_observation

    expected = successor(previous, proof=proof, observation=_cloud_observation(tx.observation))
    if after != expected:
        raise ValueError("HA route rebind expanded its exact successor authority")
    return True
