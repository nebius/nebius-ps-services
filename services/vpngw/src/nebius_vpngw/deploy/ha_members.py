"""Exact retained or created member lineage for the outer apply receipt."""

from __future__ import annotations

from typing import Any

from .vm_ha_lifecycle import (
    VMHALifecycleState,
    vm_ha_effective_resource_bindings,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycles,
)


def intent(members: tuple, *, created: set[str], replacement: Any = None) -> dict:
    result = {}
    for member in members:
        name = member.instance_name
        selected = {
            "node_id": member.node_id,
            "instance_index": member.instance_index,
            "role": member.role,
            "kind": "created" if name in created else "retained",
            "compute_id": member.compute_id,
            "disk_id": member.disk_id,
        }
        if replacement is not None and name == replacement.target_instance_name:
            selected.update(
                kind="replacement",
                cycle=replacement.replacement_cycle,
                approval=replacement.approval_digest,
                compute_id=replacement.retired_compute_id,
                disk_id=replacement.retired_disk_id,
            )
        elif selected["kind"] == "retained" and not (member.compute_id and member.disk_id):
            raise RuntimeError("Retained HA member lacks exact Compute and disk identities")
        result[name] = selected
    return result


def targets(members: dict) -> tuple[str, ...]:
    return tuple(
        sorted(
            value["compute_id"] if value["kind"] == "retained" else "create:" + value["node_id"]
            for value in members.values()
        )
    )


def verify_members(state: VMHALifecycleState, expected: dict) -> bool:
    """Creation receipts prove lineage; they never prove current cloud readiness."""
    tx = state.transaction
    if tx is None or tx.pending_effect or tx.accepted_cloud_operation_id or tx.observation_guard:
        return False
    actual = {member.instance_name: member for member in state.members}
    if set(actual) != set(expected):
        return False
    raw = dict(tx.resource_bindings)
    effective = vm_ha_effective_resource_bindings(raw)
    for name, desired in expected.items():
        member = actual[name]
        if not member.binding_complete or any(
            getattr(member, key) != desired[key] for key in ("node_id", "instance_index", "role")
        ):
            return False
        if any(
            effective.get(f"{kind}:{name}") != value
            for kind, value in (("compute", member.compute_id), ("disk", member.disk_id))
        ):
            return False
        if desired["kind"] == "retained":
            if (member.compute_id, member.disk_id) != (desired["compute_id"], desired["disk_id"]):
                return False
        elif desired["kind"] == "created":
            if (
                vm_ha_passive_replacement_cycles(raw, name)
                or f"provision-{name}-compute" not in tx.completed_effects
            ):
                return False
        elif desired["kind"] == "replacement":
            cycle = desired["cycle"]
            cycles = vm_ha_passive_replacement_cycles(raw, name)
            if not cycles or cycles[-1] != cycle:
                return False
            for kind, value in (
                ("approval", desired["approval"]),
                ("retired-compute", desired["compute_id"]),
                ("retired-disk", desired["disk_id"]),
                ("compute", member.compute_id),
                ("disk", member.disk_id),
            ):
                if raw.get(vm_ha_passive_replacement_binding_key(kind, name, cycle)) != value:
                    return False
            if (
                vm_ha_missing_standby_replacement_effect(name, cycle, "create-compute")
                not in tx.completed_effects
            ):
                return False
        else:
            return False
    return True
