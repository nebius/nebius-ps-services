"""Explicit lifecycle membership commits for prepared (still-present) VM replacements.

Cloud/guest effects are journalled by replacement.Journal. This owner validates
the corresponding lifecycle identity transitions; stopped resources are never
represented as absent to obtain creation-only recovery authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from .replacement import digest, metadata_only
from .vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHAMigrationTransaction,
    vm_ha_effective_resource_bindings,
)

KIND = "staged-replacement"
PREFIX = "staged-replacement-"
PLAN_KEY = PREFIX + "plan"
PHASES = ("approved", "member-1-committed", "promoted", "member-2-committed", "complete")


def canonical(value: Any) -> str:
    metadata_only(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def owner(members: tuple[VMHALifecycleMember, ...], allocation: str) -> VMHALifecycleMember:
    owners = [member for member in members if member.alias_allocation_ids == (allocation,)]
    if len(owners) != 1 or any(
        set(member.alias_allocation_ids) - {allocation} for member in members
    ):
        raise ValueError("Prepared replacement requires one exact shared-allocation owner")
    return owners[0]


def identity(member: VMHALifecycleMember, *, replacement: bool = False) -> dict:
    value = member.to_dict()
    for field in ("compute_revision", "alias_allocation_ids"):
        value.pop(field)
    if replacement:
        for field in ("compute_id", "disk_id", "primary_allocation_id"):
            value.pop(field)
    return value


def bindings(state: VMHALifecycleState, members: tuple[VMHALifecycleMember, ...]) -> dict[str, str]:
    assert state.transaction is not None
    value = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    value = {
        key: item
        for key, item in value.items()
        if not key.startswith(
            ("compute:", "disk:", "primary-allocation:", "public-allocation:", PREFIX)
        )
    }
    for member in members:
        name, nic = member.instance_name, member.network_interface_name
        value.update(
            {
                f"compute:{name}": member.compute_id,
                f"disk:{name}": member.disk_id,
                f"primary-allocation:{name}:{nic}": member.primary_allocation_id,
                f"public-allocation:{name}:{nic}": member.public_allocation_id,
            }
        )
    current_owner = owner(members, state.allocation_id)
    value.update(
        {
            "shared-allocation-owner-compute": current_owner.compute_id,
            "shared-allocation-owner-nic": current_owner.network_interface_name,
        }
    )
    return value


def start(
    previous: VMHALifecycleState,
    plan: dict,
    *,
    current_members: tuple[VMHALifecycleMember, VMHALifecycleMember],
    desired_digest: str,
) -> VMHALifecycleState:
    if previous.transaction is None:
        raise ValueError("Prepared replacement requires a current lifecycle transaction")
    # Store membership authority plus the full approved digest, not a recursive
    # copy of earlier lifecycle audit histories embedded in plan.ha.
    membership_plan = {key: value for key, value in plan.items() if key != "ha"}
    membership_plan["full_plan_sha256"] = digest(plan)
    transaction = VMHAMigrationTransaction(
        operation_id=plan["operation_id"],
        approval_kind=KIND,
        approval_digest=digest(plan),
        desired_state_digest=desired_digest,
        current_state_digest=previous.record_sha256,
        checkpoint=PREFIX + PHASES[0],
        pending_effect=None,
        completed_effects=(),
        revision=1,
        predecessor_sha256=previous.record_sha256,
        resource_bindings=tuple(
            sorted(
                {
                    **bindings(previous, current_members),
                    PLAN_KEY: canonical(membership_plan),
                }.items()
            )
        ),
    )
    successor = replace(
        previous,
        status=VMHALifecycleStatus.PROVISIONING,
        members=current_members,
        transaction=transaction,
    )
    validate_successor(previous, successor)
    return successor


def advance(
    previous: VMHALifecycleState,
    *,
    members: tuple[VMHALifecycleMember, VMHALifecycleMember],
    evidence: dict,
) -> VMHALifecycleState:
    before = previous.transaction
    if (
        before is None
        or before.approval_kind != KIND
        or before.revision not in range(1, len(PHASES))
    ):
        raise ValueError("Prepared replacement has no next membership transition")
    audit = {key: value for key, value in before.resource_bindings if key.startswith(PREFIX)}
    audit[PREFIX + f"event-{before.revision + 1}"] = canonical(evidence)
    phase = PHASES[before.revision]
    transaction = replace(
        before,
        revision=before.revision + 1,
        predecessor_sha256=previous.record_sha256,
        checkpoint=PREFIX + phase,
        resource_bindings=tuple(sorted({**bindings(previous, members), **audit}.items())),
    )
    successor = replace(
        previous,
        members=members,
        transaction=transaction,
        status=VMHALifecycleStatus.ACTIVE
        if phase == "complete"
        else VMHALifecycleStatus.PROVISIONING,
    )
    validate_successor(previous, successor)
    return successor


def validate_successor(previous: VMHALifecycleState, successor: VMHALifecycleState) -> bool:
    before, after = previous.transaction, successor.transaction
    if (
        before is not None
        and before.approval_kind == KIND
        and before.checkpoint != PREFIX + "complete"
    ):
        if (
            after is None
            or after.approval_kind != KIND
            or after.operation_id != before.operation_id
        ):
            raise ValueError(
                "Resume the pending prepared replacement before another lifecycle operation"
            )
    if after is None or after.approval_kind != KIND:
        return False
    if before is None or previous.record_version != 4 or successor.record_version != 4:
        raise ValueError("Prepared replacement requires a quiescent v4 lifecycle")
    if after.predecessor_sha256 != previous.record_sha256:
        raise ValueError("Prepared replacement predecessor changed")
    for field in (
        "project_id",
        "gateway_name",
        "cluster_id",
        "allocation_id",
        "allocation_name",
        "route_runtime_id",
        "route_targets",
    ):
        if getattr(previous, field) != getattr(successor, field):
            raise ValueError("Prepared replacement changed deployment or route authority")
    if any(
        (
            before.pending_effect,
            before.accepted_cloud_operation_id,
            after.pending_effect,
            after.accepted_cloud_operation_id,
            after.replacement_host_recovery,
            after.observation_guard,
        )
    ):
        raise ValueError("Prepared replacement cannot bypass unsettled lifecycle effects")
    values = dict(after.resource_bindings)
    try:
        plan = json.loads(values[PLAN_KEY])
    except (KeyError, ValueError, TypeError):
        raise ValueError("Prepared replacement approval plan is missing") from None
    metadata_only(plan)
    if (
        not plan.get("vm_ha")
        or plan.get("project_id") != previous.project_id
        or plan.get("gateway_name") != previous.gateway_name
        or plan.get("operation_id") != after.operation_id
        or plan.get("full_plan_sha256") != after.approval_digest
        or len(plan.get("members", [])) != 2
        or plan["members"][0]["logical_name"] == plan.get("observed_owner")
        or plan["members"][1]["logical_name"] != plan.get("observed_owner")
    ):
        raise ValueError("Prepared replacement approval does not bind the exact HA pair and owner")
    members = {member.instance_name: member for member in successor.members}
    previous_members = {member.instance_name: member for member in previous.members}
    if set(members) != set(previous_members) or {
        item["logical_name"] for item in plan["members"]
    } != set(members):
        raise ValueError("Prepared replacement changed logical membership")
    if not all(
        member.binding_complete and int(member.compute_revision) > 0 and member.public_allocation_id
        for member in successor.members
    ):
        raise ValueError("Prepared replacement membership evidence is incomplete")
    current_owner = owner(successor.members, successor.allocation_id)
    current_bindings = {key: value for key, value in values.items() if not key.startswith(PREFIX)}
    if current_bindings != bindings(previous, successor.members):
        raise ValueError("Prepared replacement resource bindings disagree with membership")
    if before.approval_kind != KIND or before.operation_id != after.operation_id:
        if (
            previous.status is not VMHALifecycleStatus.ACTIVE
            or after.revision != 1
            or successor.status is not VMHALifecycleStatus.PROVISIONING
            or after.checkpoint != PREFIX + "approved"
            or after.current_state_digest != previous.record_sha256
            or after.completed_effects
            or current_owner.instance_name != plan["observed_owner"]
            or {key for key in values if key.startswith(PREFIX)} != {PLAN_KEY}
        ):
            raise ValueError("Prepared replacement start is not an exact active-pair approval")
        for item in plan["members"]:
            member = members[item["logical_name"]]
            if (
                identity(member) != identity(previous_members[member.instance_name])
                or item["compute_id"] != member.compute_id
                or item["disk_id"] != member.disk_id
                or str(item["revision"]) != member.compute_revision
            ):
                raise ValueError(
                    "Prepared replacement source differs from its inspected lifecycle identity"
                )
        return True
    if after.revision != before.revision + 1 or after.revision not in range(2, len(PHASES) + 1):
        raise ValueError("Prepared replacement phase is not monotonic")
    phase = PHASES[after.revision - 1]
    if (
        after.checkpoint != PREFIX + phase
        or before.checkpoint != PREFIX + PHASES[before.revision - 1]
        or successor.status
        is not (
            VMHALifecycleStatus.ACTIVE if phase == "complete" else VMHALifecycleStatus.PROVISIONING
        )
        or before.completed_effects != after.completed_effects
    ):
        raise ValueError("Prepared replacement skipped its membership or promotion phase")
    for field in (
        "approval_digest",
        "desired_state_digest",
        "current_state_digest",
        "v2_predecessor_sha256",
    ):
        if getattr(before, field) != getattr(after, field):
            raise ValueError("Prepared replacement changed frozen approval authority")
    old_audit = {key: value for key, value in before.resource_bindings if key.startswith(PREFIX)}
    key = PREFIX + f"event-{after.revision}"
    if {name for name in values if name.startswith(PREFIX)} != {*old_audit, key} or any(
        values.get(name) != value for name, value in old_audit.items()
    ):
        raise ValueError("Prepared replacement changed its prior membership evidence")
    evidence = json.loads(values[key])
    if phase in {"member-1-committed", "member-2-committed"}:
        target_name = plan["members"][0 if phase == "member-1-committed" else 1]["logical_name"]
        old, new = previous_members[target_name], members[target_name]
        if (
            evidence
            != {
                "kind": "membership",
                "retired_compute_id": old.compute_id,
                "retired_state": "stopped",
                "candidate_compute_id": new.compute_id,
                "candidate_disk_id": new.disk_id,
                "owner_compute_id": current_owner.compute_id,
            }
            or old.compute_id == new.compute_id
            or old.disk_id == new.disk_id
            or identity(old, replacement=True) != identity(new, replacement=True)
            or old.alias_allocation_ids
            or new.alias_allocation_ids
            or current_owner.instance_name == target_name
            or any(
                identity(previous_members[name]) != identity(member)
                for name, member in members.items()
                if name != target_name
            )
        ):
            raise ValueError(
                "Prepared replacement requires the exact stopped nonowner and prepared successor"
            )
        expected_owner = (
            plan["observed_owner"]
            if phase == "member-1-committed"
            else plan["members"][0]["logical_name"]
        )
        if current_owner.instance_name != expected_owner:
            raise ValueError("Prepared replacement shared owner changed unexpectedly")
    else:
        if any(
            identity(previous_members[name]) != identity(member) for name, member in members.items()
        ):
            raise ValueError(
                "Prepared replacement promotion or completion changed Compute identity"
            )
        if current_owner.instance_name != plan["members"][0]["logical_name"]:
            raise ValueError("Prepared replacement must promote the first verified replacement")
        if phase == "promoted":
            if (
                set(evidence) != {"kind", "owner_compute_id", "canonical_promotion_sha256"}
                or evidence["kind"] != "promotion"
                or evidence["owner_compute_id"] != current_owner.compute_id
                or not isinstance(evidence["canonical_promotion_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", evidence["canonical_promotion_sha256"]) is None
            ):
                raise ValueError("Prepared replacement has no canonical fenced promotion receipt")
        elif evidence != {
            "kind": "complete",
            "verified_member_ids": sorted(member.compute_id for member in successor.members),
        }:
            raise ValueError("Prepared replacement completion is not independently verified")
    return True
