"""Persist package selection for command-owned new guests before installation."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from ..apply_approval import digest, manifest_identity
from . import vm_ha_package
from .vm_ha_lifecycle import (
    vm_ha_activation_effect_is_host_only,
    vm_ha_effective_resource_bindings,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
)

PREFIX = "runtime-created-package-manifests:"


def saved_manifests(state: Any) -> dict:
    """Read the full frozen selection for a reviewed peer continuation."""
    tx = state.transaction
    if tx is None:
        raise RuntimeError("Package restoration lacks its lifecycle")
    raw = dict(tx.resource_bindings)
    manifests = json.loads(raw.get("runtime-package-manifests", "{}"))
    key = PREFIX + tx.operation_id
    if key not in raw:
        return manifests
    saved = json.loads(raw[key])
    authority = saved["authority"]
    if (
        authority["execution"] != raw.get("runtime-command-execution")
        or authority["operation"] != tx.operation_id
        or authority["artifact"] != raw.get("heartbeat-initial-artifact")
        or "select-created-package-manifests" not in tx.completed_effects
        or set(saved["manifests"]) != set(authority["lineage"])
        or set(manifests).intersection(saved["manifests"])
    ):
        raise RuntimeError("Created package receipt has different lifecycle authority")
    effective = vm_ha_effective_resource_bindings(raw)
    members = {member.instance_name: member for member in state.members}
    for name, lineage in authority["lineage"].items():
        member = members.get(name)
        if (
            member is None
            or member.node_id != lineage["node"]
            or member.compute_id != lineage["compute"]
            or member.disk_id != lineage["disk"]
            or effective.get(f"compute:{name}") != lineage["compute"]
            or effective.get(f"disk:{name}") != lineage["disk"]
            or lineage["creation"] not in tx.completed_effects
        ):
            raise RuntimeError("Created package receipt lost its exact member lineage")
    return {**manifests, **saved["manifests"]}


def prepare(
    *,
    command: Any,
    journal: Any,
    artifact: Any,
    ssh: Any,
    local: dict,
    staged: list,
    resources: Any,
) -> Any:
    """Selection is fill-once; resumed installs restore the original wheel hashes.

    The lifecycle remains the authority for creation. This selection cannot seed
    a checkpoint, admit a different Compute, or authorize controller activation.
    """
    expected = {
        name: member
        for name, member in (command.intent.get("members") or {}).items()
        if member["kind"] in {"created", "replacement"}
    }
    if not expected:
        return artifact
    state = journal.state
    tx = state.transaction
    if tx is None:
        raise RuntimeError("Created guest package selection lost lifecycle authority")
    raw = dict(tx.resource_bindings)
    key = PREFIX + tx.operation_id
    effect = "select-created-package-manifests"
    if (
        tx.operation_id != command.intent["packages"]["parent"]
        or raw.get("runtime-command-execution") != command.execution_id
        or raw.get("heartbeat-initial-artifact") != artifact.sha256
        or not (
            tx.pending_effect in {None, effect}
            or key in raw
            and vm_ha_activation_effect_is_host_only(tx.pending_effect)
        )
        or tx.accepted_cloud_operation_id
        or tx.observation_guard
    ):
        raise RuntimeError("Created guest package selection has different authority")
    effective = vm_ha_effective_resource_bindings(raw)
    members = {member.instance_name: member for member in state.members}
    entries = {instance.hostname: (instance, target) for instance, target, _receipt in staged}
    lineage = {}
    for name, wanted in expected.items():
        member = members.get(name)
        if (
            member is None
            or name not in entries
            or not member.binding_complete
            or member.node_id != wanted["node_id"]
            or member.instance_index != wanted["instance_index"]
            or member.role != wanted["role"]
            or effective.get(f"compute:{name}") != member.compute_id
            or effective.get(f"disk:{name}") != member.disk_id
        ):
            raise RuntimeError("Created guest package identity changed")
        creation = f"provision-{name}-compute"
        if wanted["kind"] == "replacement":
            cycle = wanted["cycle"]
            creation = vm_ha_missing_standby_replacement_effect(name, cycle, "create-compute")
            for kind, value in (
                ("approval", wanted["approval"]),
                ("retired-compute", wanted["compute_id"]),
                ("retired-disk", wanted["disk_id"]),
                ("compute", member.compute_id),
                ("disk", member.disk_id),
            ):
                if raw.get(vm_ha_passive_replacement_binding_key(kind, name, cycle)) != value:
                    raise RuntimeError("Created guest package replacement lineage changed")
        if creation not in tx.completed_effects:
            raise RuntimeError("Created guest package lacks its exact creation receipt")
        lineage[name] = {
            "compute": member.compute_id,
            "disk": member.disk_id,
            "node": member.node_id,
            "creation": creation,
            "configuration": digest(entries[name][0].config_yaml),
        }
    authority = {
        "execution": command.execution_id,
        "operation": tx.operation_id,
        "artifact": artifact.sha256,
        "lineage": lineage,
    }
    saved = json.loads(raw[key]) if key in raw else None
    if saved is not None and (
        saved.get("authority") != authority or set(saved.get("manifests", {})) != set(expected)
    ):
        raise RuntimeError("Created guest package selection changed after publication")
    plans = dict(artifact.dependency_plans)
    for name in sorted(expected):
        if name in plans:
            raise RuntimeError("Created guest has an ambiguous retained package selection")
        instance, target = entries[name]
        from .package_reservation import read as read_reservation
        from .package_reservation import recovery_owner

        package = vm_ha_package.inspect_plan(
            ssh,
            target,
            instance,
            local,
            target_identity=lineage[name]["compute"],
            wheel=artifact.path,
            resume_parent=tx.operation_id,
            resume_manifest=saved["manifests"][name] if saved is not None else None,
            reservation=read_reservation(
                *recovery_owner(command.journal.root, command.execution_id, digest(command.intent)),
                name,
            ),
        )
        resources.callback(package.close)
        if saved is not None and manifest_identity(package.manifest) != manifest_identity(
            saved["manifests"][name]
        ):
            raise RuntimeError("Created guest package selection changed during restoration")
        plans[name] = package
    selected = {
        "authority": authority,
        "manifests": {name: plans[name].manifest for name in sorted(expected)},
    }
    if saved is None:
        journal.begin(effect)
        journal.complete(effect, resource_updates={key: json.dumps(selected, sort_keys=True)})
    elif effect not in tx.completed_effects:
        raise RuntimeError("Created guest package selection lacks its durable receipt")
    # Every selection is durable before the first package stage/install.
    for name in sorted(expected):
        vm_ha_package.stage(ssh, plans[name], local, artifact=artifact)
    return replace(artifact, dependency_plans=tuple(plans.items()))
