"""Read-only admission of a partially published, receipt-bound HA table plan.

This admits recovery to the stopped-writer owner; it grants no runtime route
authority and never settles an operation. The owner rereads full cloud objects
and reacquires both writer fences before replaying any subnet CAS.
"""

from __future__ import annotations

import json
from collections import Counter

from ..apply_approval import digest
from .ha_enforced_rebind import PLAN_KEY
from .ha_enforced_tables import TableJournal, resolve_request, validate_plan
from .ordinary_route_ownership import allocation_id
from .replacement_cloud import effect_key, message_digest
from .vm_ha_lifecycle import (
    VMHALifecycleStatus,
    normalize_vm_ha_observation,
    vm_ha_activation_effect_is_host_only,
)


def admit(state, observation, *, saved: dict | None = None) -> bool:
    tx = state.transaction
    raw = dict(tx.resource_bindings).get(PLAN_KEY) if tx else None
    if raw is None:
        return False
    if saved is None:
        saved = TableJournal(state.project_id, state.gateway_name).snapshot()
    child = saved.get("transaction") if saved else None
    if child is None or child["phase"] == "preparing":
        return False
    plan = json.loads(raw)
    validate_plan(plan)
    unchanged_prepared_host = bool(
        child["phase"] == "prepared"
        and not any(
            op["request_type"] == "UpdateSubnetRequest" for op in child["operations"].values()
        )
        and normalize_vm_ha_observation(observation) == tx.observation
        and tx.pending_effect is not None
        and vm_ha_activation_effect_is_host_only(tx.pending_effect)
    )
    if (
        state.status not in {VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING}
        or tx.pending_effect
        and not unchanged_prepared_host
        or tx.observation_guard
        or tx.accepted_cloud_operation_id
        or child["plan"] != plan
        or child["phase"] not in {"prepared", "attached", "complete"}
        or plan["project"] != state.project_id
        or plan["gateway"] != state.gateway_name
        or plan["cluster"] != state.cluster_id
        or json.loads(observation.get("enforced_table_plan", "null")) != plan
    ):
        raise ValueError("HA partial table recovery differs from its approved lifecycle")
    allocation = dict(tx.resource_bindings).get("shared-allocation-id")
    if not allocation or state.allocation_id not in {"", allocation}:
        raise ValueError("HA partial table recovery changed allocation authority")
    records = child["operations"]
    expected_keys = {effect_key(digest(plan), 0, k) for k in plan["requests"]}
    if not set(records) <= expected_keys:
        raise ValueError("HA partial table recovery contains an unrelated operation")
    for label, row in plan["requests"].items():
        op = records.get(effect_key(digest(plan), 0, label))
        if row["type"] == "UpdateSubnetRequest" and op is None:
            continue
        request = resolve_request(plan, records, label, allocation=allocation)
        if (
            op is None
            or op["label"] != label
            or op["request_type"] != row["type"]
            or op["request_sha256"] != message_digest(request)
            or op["status"] not in {"intent", "accepted", "settled"}
            or row["type"] != "UpdateSubnetRequest"
            and op["status"] != "settled"
            or op["status"] == "settled"
            and (not op["resource_id"] or not op["cloud_operation_id"])
        ):
            raise ValueError("HA partial table recovery lacks exact preparation receipts")

    def non_route(leaves):
        return {
            k: v
            for k, v in leaves
            if k not in {"/routes", "/route_targets"}
            and not k.startswith(("/routes/", "/route_targets/"))
        }

    if non_route(normalize_vm_ha_observation(observation)) != non_route(tx.observation):
        raise ValueError("HA partial table recovery observed unrelated cloud drift")
    original = {t["workload_subnet_id"]: t for t in plan["targets"]}
    # First installation binds the receipt-created private targets for staging,
    # while they remain unattached. This is never runtime target admission.
    if state.route_targets:
        candidates = []
        for subnet, target in original.items():
            table = records.get(effect_key(digest(plan), 0, "table-" + subnet))
            candidates.append(
                {**target, "route_table_id": table["resource_id"]} if table else target
            )
        allowed = {
            tuple(sorted(json.dumps(t, sort_keys=True, separators=(",", ":")) for t in targets))
            for targets in (plan["targets"], candidates)
        }
        if state.route_targets not in allowed:
            raise ValueError("HA partial table recovery changed lifecycle target authority")
    actual = observation.get("route_targets")
    if not isinstance(actual, list) or len(actual) != len(original):
        raise ValueError("HA partial table recovery changed workload membership")
    seen: set[str] = set()
    expected_routes: list[dict] = []
    from .vm_ha_prehost import _cloud_observation

    before = _cloud_observation(tx.observation)
    for target in actual:
        subnet = target["workload_subnet_id"]
        if subnet not in original or subnet in seen:
            raise ValueError("HA partial table recovery changed workload membership")
        seen.add(subnet)
        old = original[subnet]
        op = records.get(effect_key(digest(plan), 0, "attach-" + subnet))
        if target == old and (op is None or op["status"] != "settled"):
            # Shared predecessor tables appear once per selecting subnet.
            rows = [r for r in before["routes"] if r["route_table_id"] == old["route_table_id"]]
            copies = sum(t["route_table_id"] == old["route_table_id"] for t in plan["targets"])
            unique = {r["route_id"]: r for r in rows}
            if len(rows) != len(unique) * copies:
                raise ValueError("HA partial table recovery predecessor is ambiguous")
            expected_routes.extend(unique.values())
            continue
        table_op = records.get(effect_key(digest(plan), 0, "table-" + subnet))
        if (
            op is None
            or table_op is None
            or target != {**old, "route_table_id": table_op["resource_id"]}
        ):
            raise ValueError("HA partial table recovery attachment lacks its exact CAS intent")
        for label, row in plan["requests"].items():
            if row["type"] != "CreateRouteRequest" or row["subnet"] != subnet:
                continue
            receipt = records[effect_key(digest(plan), 0, label)]
            request = resolve_request(plan, records, label, allocation=allocation)
            snapshot = {
                "id": receipt["resource_id"],
                "parent_id": request.metadata.parent_id,
                "name": request.metadata.name,
                "labels": dict(request.metadata.labels),
                "spec_sha256": message_digest(request.spec),
                "destination": request.spec.destination.cidr,
                "allocation_id": allocation_id(request),
            }
            from .route_manager import NebiusSDKRouteBackend

            expected_routes.append(
                {
                    "route_id": snapshot["id"],
                    "route_table_id": snapshot["parent_id"],
                    "name": snapshot["name"],
                    "prefix": snapshot["destination"],
                    "allocation_id": snapshot["allocation_id"],
                    "authority_labels": {
                        k: v
                        for k, v in snapshot["labels"].items()
                        if k in NebiusSDKRouteBackend._AUTHORITY_LABEL_KEYS
                    },
                    "snapshot_sha256": digest(snapshot),
                }
            )
    current_routes = observation.get("routes")
    if not isinstance(current_routes, list) or any(
        not str(r.get("revision", "")).isdecimal() or int(r["revision"]) <= 0
        for r in current_routes
    ):
        raise ValueError("HA partial table recovery route readback is incomplete")

    def canonical(rows):
        return Counter(digest({k: v for k, v in r.items() if k != "revision"}) for r in rows)

    if canonical(current_routes) != canonical(expected_routes):
        raise ValueError("HA partial table recovery routes differ from creation receipts")
    return True
