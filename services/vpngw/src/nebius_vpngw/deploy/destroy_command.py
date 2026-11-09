"""Retire obsolete HA command admission only after verified gateway destruction."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from ..apply_approval import digest
from . import package_reservation
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .operator_guest import finish_publication, publish, read_file
from .operator_keys import protected_directory
from .vm_ha_lifecycle import VMHALifecycleStatus

if TYPE_CHECKING:
    from .destroy import NebiusDestroyBackend, VMHADestroyJournal


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
        if members is not None and any(
            member["compute_id"] not in targets for member in members.values()
        ):
            raise RuntimeError("Pending member targets differ from HA authority")
        attachments = intent.get("attachments")
        if attachments is not None and any(
            member["compute_id"] not in targets for member in attachments["members"].values()
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
                or reservation["request"]["package"]["target"] not in targets
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
                if not set(effect.targets) <= targets:
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
            for target in sorted(targets):
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
