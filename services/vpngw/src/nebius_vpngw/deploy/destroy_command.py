"""Retire obsolete HA command admission only after verified gateway destruction."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from ..apply_approval import SCHEMA, digest
from . import package_reservation
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .operator_guest import finish_publication, publish, read_file
from .operator_keys import protected_directory
from .vm_ha_lifecycle import VMHALifecycleStatus, vm_ha_effective_resource_bindings

if TYPE_CHECKING:
    from .destroy import NebiusDestroyBackend, VMHADestroyJournal
    from .vm_ha_lifecycle import VMHALifecycleState


def _created_compute_targets(
    state: VMHALifecycleState, transaction: dict, plan: CommandPlan, intent: dict
) -> set[str]:
    """Resolve creation tokens using retained publication authority, never by name.

    Destruction retains these immutable bindings while replacing the lifecycle
    transaction. The original creation receipt identifies the exact Compute IDs
    whose absence must still be observed before cancelling command admission.
    """
    from .ha_created_packages import PREFIX

    assert state.transaction is not None
    raw = dict(state.transaction.resource_bindings)
    effective = vm_ha_effective_resource_bindings(raw)
    packages = intent["packages"]
    saved = json.loads(raw.get(PREFIX + packages["parent"], "null"))
    members = {m.instance_name: m for m in state.members}
    if (
        not isinstance(saved, dict)
        or set(saved) != {"authority", "manifests"}
        or not isinstance(saved["authority"], dict)
        or set(saved["authority"]) != {"execution", "operation", "artifact", "lineage"}
        or not isinstance(saved["manifests"], dict)
        or set(saved["manifests"]) != set(members)
        or raw.get("runtime-command-execution") != transaction["execution_id"]
        or raw.get("heartbeat-initial-artifact") != plan.artifact_sha256
        or json.loads(raw.get("runtime-package-manifests", "null")) != packages["manifests"]
        or json.loads(raw.get("runtime-command-approval", "null"))
        != {"schema": SCHEMA, "approval": intent["approval"], "scope": intent["runtime_scope"]}
    ):
        raise RuntimeError("Created command retirement lacks its exact publication receipt")
    authority = saved["authority"]
    lineage = authority["lineage"]
    if (
        authority["execution"] != transaction["execution_id"]
        or authority["operation"] != packages["parent"]
        or authority["artifact"] != plan.artifact_sha256
        or not isinstance(lineage, dict)
        or set(lineage) != set(members)
    ):
        raise RuntimeError("Created command retirement changed publication authority")
    for name, member in members.items():
        row = lineage[name]
        manifest = saved["manifests"][name]
        if (
            not isinstance(row, dict)
            or set(row) != {"compute", "disk", "node", "creation", "configuration"}
            or not member.compute_id
            or not member.disk_id
            or row["compute"] != member.compute_id
            or row["disk"] != member.disk_id
            or row["node"] != member.node_id
            or row["creation"] != f"provision-{name}-compute"
            or effective.get(f"compute:{name}") != member.compute_id
            or effective.get(f"disk:{name}") != member.disk_id
            or not isinstance(manifest, dict)
            or manifest.get("wheel_sha256") != plan.artifact_sha256
        ):
            raise RuntimeError("Created command retirement changed member lineage")
    return {m.compute_id for m in members.values()}


def retire(
    command: CommandJournal, destruction: VMHADestroyJournal, backend: NebiusDestroyBackend
) -> None:
    """Caller holds the gateway lock and has completed fresh destruction verification.

    This cancels authority; it never marks unfinished effects successful. Compute
    targets from an earlier destroyed incarnation are read by exact ID only and
    grant no additional deletion authority.
    """
    if command.snapshot() is None:
        return
    with command:
        transaction = command.state["transaction"]
        if transaction is None or transaction["phase"] != "pending":
            return
        state = destruction.journal.state
        terminal = state.transaction
        if (
            state.status is not VMHALifecycleStatus.DESTROYED
            or state.project_id != command.project
            or state.gateway_name != command.gateway
            or terminal is None
            or terminal.approval_kind != "destruction"
            or terminal.pending_effect is not None
            or terminal.accepted_cloud_operation_id is not None
            or terminal.checkpoint != "destruction-verified-absent"
            or terminal.approval_digest != destruction.plan.digest
        ):
            raise RuntimeError("Command retirement requires exact terminal destruction")
        plan = CommandPlan.parse(transaction["plan"])
        runtime = next((effect for effect in plan.effects if effect.key == "runtime"), None)
        if (
            runtime is None
            or runtime.owner != "ha-lifecycle"
            or runtime.action != "apply"
            or not runtime.targets
        ):
            raise RuntimeError("Destruction cannot retire an unrelated pending command")
        targets = set(runtime.targets)
        intent = command.state["bindings"].get("runtime")
        if not isinstance(intent, dict):
            raise RuntimeError("Pending HA command lost its runtime authority")
        members = intent.get("members")
        compute_targets = targets
        if any(target.startswith("create:") for target in targets):
            from .ha_members import targets as member_targets

            # Creation tokens are never Compute identities. Resolve only the
            # exact created lineage, or prove that provisioning never began.
            terminal_members = {member.instance_name: member for member in state.members}
            packages = intent.get("packages")
            if (
                not isinstance(members, dict)
                or set(members) != set(terminal_members)
                or not set(members) <= set(destruction.plan.expected_compute_names)
                or not isinstance(packages, dict)
                or any(
                    not isinstance(member, dict)
                    or set(member)
                    != {"kind", "node_id", "instance_index", "role", "compute_id", "disk_id"}
                    or member["kind"] != "created"
                    or member["compute_id"] != ""
                    or member["disk_id"] != ""
                    or type(member["instance_index"]) is not int
                    or any(
                        member[key] != getattr(terminal_members[name], key)
                        for key in ("node_id", "instance_index", "role")
                    )
                    for name, member in members.items()
                )
                or tuple(sorted(targets)) != member_targets(members)
            ):
                raise RuntimeError("Pending creation targets lack exact terminal authority")
            compute_targets = (
                set()
                if packages.get("checkpoint") == state.record_sha256
                else _created_compute_targets(state, transaction, plan, intent)
            )
        elif members is not None and any(
            member["compute_id"] not in targets for member in members.values()
        ):
            raise RuntimeError("Pending member targets differ from HA authority")
        attachments = intent.get("attachments")
        if attachments is not None and any(
            member["compute_id"] not in compute_targets
            for member in attachments["members"].values()
        ):
            raise RuntimeError("Pending attachment intent differs from HA authority")
        scope = package_reservation.recovery_owner(
            command.root, transaction["execution_id"], digest(intent)
        )
        reservation_directory = package_reservation.path_for(*scope[:2], "probe").parent
        for path in reservation_directory.glob("*.json"):
            if path.name == "recovery-owner.json":
                continue
            raw = read_file(path)
            if raw is None:
                raise RuntimeError("Package reservation disappeared during retirement")
            host = json.loads(raw["content"])["host"]
            reservation = package_reservation.read(*scope, host)
            if (
                path != package_reservation.path_for(*scope[:2], host)
                or reservation is None
                or reservation["request"]["package"]["target"] not in compute_targets
            ):
                raise RuntimeError("Pending package target differs from HA authority")
        for effect in plan.effects:
            if effect is runtime:
                continue
            if (
                effect.key == "attachments"
                and effect.owner == "security-groups"
                and effect.action == "reconcile"
            ):
                if not set(effect.targets) <= compute_targets:
                    raise RuntimeError("Pending attachment targets differ from HA authority")
            elif (effect.key, effect.owner, effect.action) not in {
                ("credentials", "managed-credentials", "enroll"),
                ("ssh", "ssh-bootstrap", "publish"),
            }:
                raise RuntimeError("Destruction cannot retire an unknown child owner")
            if transaction["progress"][effect.key] == Progress.STARTED.value:
                raise RuntimeError("Pending prerequisite requires its owning recovery workflow")
        # Missing, stale or unavailable observations do not revoke an admission.
        for _ in range(2):
            for target in sorted(compute_targets):
                if not backend.command_compute_absent(target):
                    raise RuntimeError("Pending command Compute target is not proven absent")
        predecessor = command.archive()
        record = {
            "schema": "nebius-vpngw.destroy-command-retirement.v1",
            "project": command.project,
            "gateway": command.gateway,
            "command": predecessor,
            "execution": transaction["execution_id"],
            "destruction": state.record_sha256,
            "operation": terminal.operation_id,
            "targets": sorted(targets),
        }
        directory = command.root / "retirements"
        protected_directory(directory)
        path = directory / (transaction["execution_id"] + ".json")
        desired = {
            "content": json.dumps(record, sort_keys=True) + "\n",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mode": 0o600,
        }
        existing = read_file(path)
        if existing is None:
            publish(path, None, desired)
        elif existing != desired:
            raise RuntimeError("Command destruction retirement authority changed")
        finish_publication(path, None, desired)
        command.state = {**command.state, "transaction": None, "bindings": {}}
        command.save()
