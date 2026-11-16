"""Settle obsolete HA attachments after proving all preceding runtime work terminal.

The retained generation and allocation owner must remain exact. This owner never
executes an unsubmitted member update or treats partial attachments as complete.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import ha_attachment_runtime, package_reservation
from . import security_group_apply as attachments
from .apply_plan import CommandPlan, Progress
from .attachment_command import _plan
from .attachment_settlement import cloud_evidence, require_operations, retired
from .credential_enrollment import EnrollmentJournal
from .ha_attachment_guard import Guard
from .operator_guest import publish, read_file
from .operator_keys import protected_directory
from .ordinary_route_ownership import RouteJournal
from .provisioning_journal import ProvisioningJournal
from .provisioning_trust import TrustJournal
from .recovery import ReplanRejected, WriterBusy
from .replacement import Journal as ReplacementJournal
from .replacement import metadata_only
from .replacement_cloud import Cloud
from .vm_ha_lifecycle import VMHALifecycleStatus

SCHEMA = "nebius-vpngw.ha-attachment-settlement.v1"
ACTION = "archive-ha-attachment-predecessor"


def require_original(command, parent: dict, original: dict, current: dict) -> None:
    before, after = original["transaction"], current["transaction"]
    plan = CommandPlan.parse(parent["plan"])
    if (
        before is None
        or after is None
        or (
            before["phase"] != "pending"
            or before["operation_id"] != parent["execution_id"]
            or {k: v for k, v in before.items() if k != "operations"}
            != {k: v for k, v in after.items() if k != "operations"}
        )
    ):
        raise ReplanRejected("HA attachment predecessor lost its original command authority")
    if len(plan.effects) == 1:
        if (
            parent["progress"] != {"attachments": Progress.STARTED.value}
            or before["approval"]
            != digest({"scope": before["scope"], "attachments": before["plan"]})
            or _plan(
                command, before["scope"], plan.artifact_sha256, before["plan"], before["approval"]
            )
            != plan
        ):
            raise ReplanRejected("HA attachment predecessor lost its original command authority")
    else:
        ha_attachment_runtime.original(command, parent, before)
    require_operations(before, after)


@dataclass
class Settlement:
    command: Any
    parent: dict
    lifecycle: Any
    read_lifecycle: Any
    owners: dict
    snapshots: dict
    child: Any
    original: dict
    current: dict
    path: Any
    raw: dict | None
    saved: dict | None
    proof: dict
    guard: Guard
    refresh_ssh_policy: Any
    verify_ha: Any
    prerequisites: Any

    def runtime_evidence(self) -> dict | None:
        if "runtime" not in self.parent["bindings"]:
            package_reservation.require_unreserved(
                self.command.root,
                self.parent["transaction"]["execution_id"],
            )
            return None
        return ha_attachment_runtime.observe(
            self.command,
            self.parent,
            self.lifecycle,
            self.owners,
            self.snapshots,
            self.guard,
            self.prerequisites,
        )

    @property
    def approval(self) -> str:
        return digest(self.proof)

    @property
    def resumed(self) -> bool:
        return self.saved is not None

    def observe(self, evidence: dict, *, releasing: bool) -> dict:
        if self.read_lifecycle() != self.lifecycle:
            raise ReplanRejected("HA lifecycle changed during attachment settlement")
        require_original(self.command, self.parent["transaction"], self.original, evidence)
        self.guard.transaction = evidence["transaction"]
        if self.runtime_evidence() != self.proof.get("runtime"):
            raise ReplanRejected("HA attachment completed runtime authority changed")
        self.guard.observe(releasing=releasing)
        return cloud_evidence(
            self.guard.manager,
            self.command,
            self.child,
            evidence,
            settled=releasing,
        )

    def execute(self, refresh) -> None:
        fresh = refresh()
        if fresh is None or fresh.proof != self.proof:
            raise ReplanRejected("HA attachment settlement approval changed")
        with ExitStack() as stack:
            try:
                stack.enter_context(self.command)
                for owner in self.owners.values():
                    stack.enter_context(owner)
            except BlockingIOError as error:
                raise WriterBusy("An HA attachment predecessor owner is still running") from error
            if self.command.state != self.parent:
                raise ReplanRejected("HA attachment command changed after review")
            for name, owner in self.owners.items():
                expected = self.current if name == "attachments" else self.snapshots[name]
                if expected is None:
                    if owner.state["transaction"] is not None or owner.state["bindings"]:
                        raise ReplanRejected("HA attachment child owner changed after review")
                elif owner.state != expected:
                    raise ReplanRejected("HA attachment child owner changed after review")
            if read_file(self.path) != self.raw:
                raise ReplanRejected("HA attachment settlement receipt changed after review")
            record: dict = (
                deepcopy(self.saved)
                if self.saved
                else {
                    "schema": SCHEMA,
                    "proof": self.proof,
                    "original": self.original,
                    "phase": "settling",
                    "settled": None,
                }
            )
            expected = self.raw

            def save():
                nonlocal expected
                metadata_only(record)
                value = dict(
                    content=json.dumps(record, sort_keys=True),
                    uid=os.getuid(),
                    gid=os.getgid(),
                    mode=0o600,
                )
                publish(self.path, expected, value)
                expected = value

            releasing = record["phase"] != "settling"
            evidence = record["settled"] if releasing else self.current
            self.observe(evidence, releasing=releasing)
            protected_directory(self.path.parent)
            save()
            if not releasing:
                for name, owner in self.owners.items():
                    if name != "attachments" and self.snapshots[name] is not None:
                        owner.archive()
                self.child.archive()
                if not any(
                    r["status"] == "rejected"
                    for r in self.child.state["transaction"]["operations"].values()
                ):
                    Cloud(self.guard.manager._get_client(), self.child).settle()
                proofs = cloud_evidence(
                    self.guard.manager, self.command, self.child, self.child.state, settled=True
                )
                for name, proof in proofs.items():
                    attachments.refresh_guest_trust(
                        self.guard.manager,
                        self.guard.ssh,
                        self.child,
                        name,
                        proof,
                        self.refresh_ssh_policy,
                    )
                self.observe(self.child.state, releasing=False)
                record["settled"] = self.child.snapshot()
                record["phase"] = "releasing"
                save()
            # Publish release authority before either native gate changes. A
            # lost reply is reconciled with its original native release receipt.
            self.guard.transaction = record["settled"]["transaction"]

            def admit_release():
                if read_file(self.path) != expected or self.read_lifecycle() != self.lifecycle:
                    raise ReplanRejected("HA attachment approval changed before gate release")
                self.guard.authority()
                if self.runtime_evidence() != self.proof.get("runtime"):
                    raise ReplanRejected("HA attachment runtime changed before gate release")

            self.guard.release(admit_release)
            self.verify_ha()
            self.observe(record["settled"], releasing=True)
            # Recheck the private approval after the potentially long health wait.
            if read_file(self.path) != expected:
                raise ReplanRejected("HA attachment settlement approval changed before retirement")
            if self.child.state == record["settled"]:
                self.child.retire_ha_predecessor(
                    record["settled"], self.command, self.parent["transaction"]
                )
            elif self.child.state != retired(record["settled"]):
                raise ReplanRejected("HA attachment retirement lost its original child")
            self.command.retire_settled(self.parent, self.proof)
            record["phase"] = "complete"
            save()


def inspect(
    command,
    child,
    *,
    scope: str,
    artifact: str,
    read_lifecycle,
    manager,
    ssh,
    instances: tuple,
    local: dict,
    refresh_ssh_policy,
    verify_ha,
    prerequisites=None,
) -> Settlement | None:
    parent = command.snapshot()
    transaction = parent["transaction"] if parent else None
    if transaction is None or transaction["phase"] != "pending":
        return None
    plan = CommandPlan.parse(transaction["plan"])
    if not any(
        effect.key == "attachments" and effect.owner == "security-groups" for effect in plan.effects
    ):
        return None
    path = command.root / "ha-attachment-settlements" / (transaction["execution_id"] + ".json")
    raw = read_file(path)
    saved = json.loads(raw["content"]) if raw else None
    if (
        saved is None
        and "runtime" in parent["bindings"]
        and transaction["progress"]["attachments"] == Progress.PLANNED.value
    ):
        # The runtime owner still owns recovery before attachment execution.
        # Its approved successor validates and replaces the unused child; this
        # settlement owner must not intercept that canonical recovery path.
        return None
    if saved is None and plan.configuration_sha256 == scope and plan.artifact_sha256 == artifact:
        return None
    current = child.snapshot()
    if current is None:
        raise ReplanRejected("HA attachment predecessor is missing")
    if saved is not None:
        assert raw is not None
        metadata_only(saved)
        if (
            raw["uid"] != os.getuid()
            or raw["mode"] != 0o600
            or set(saved) != {"schema", "proof", "original", "phase", "settled"}
            or saved["schema"] != SCHEMA
            or saved["phase"] not in {"settling", "releasing", "complete"}
        ):
            raise ReplanRejected("HA attachment settlement receipt is invalid")
        child.validate(saved["original"])
    original = saved["original"] if saved else current
    releasing = bool(saved and saved["phase"] != "settling")
    evidence = saved["settled"] if saved and releasing else current
    child.validate(evidence)
    if releasing and current not in (evidence, retired(evidence)):
        raise ReplanRejected("HA attachment retirement receipt changed")
    require_original(command, transaction, original, evidence)
    lifecycle = read_lifecycle()
    if (
        lifecycle is None
        or lifecycle.status is not VMHALifecycleStatus.ACTIVE
        or lifecycle.transaction is None
        or lifecycle.transaction.pending_effect is not None
    ):
        raise ReplanRejected("HA attachment settlement requires a terminal lifecycle")
    targets = manager.inspect_ordinary_targets(item.hostname for item in instances)
    guard = Guard(manager, ssh, instances, local, targets, evidence["transaction"])
    binding, _configs, _owner, _operation = guard.authority()
    if (
        lifecycle.cluster_id != binding.cluster_id
        or lifecycle.allocation_id != binding.shared_allocation_id
        or {m.compute_id for m in lifecycle.members} != {node.compute_id for node in binding.nodes}
    ):
        raise ReplanRejected("HA attachment lifecycle and guest authority differ")
    owners = {
        "credentials": EnrollmentJournal(command.project, command.gateway),
        "ssh": TrustJournal(command.project, command.gateway),
        "attachments": child,
        "routes": RouteJournal(command.project, command.gateway),
        "provisioning": ProvisioningJournal(command.project, command.gateway),
        "replacement": ReplacementJournal(command.project, command.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in owners.items()}
    snapshots["attachments"] = original
    for name, snapshot in snapshots.items():
        pending = snapshot["transaction"] if snapshot else None
        if name != "attachments" and pending and pending["phase"] != "complete":
            raise ReplanRejected("HA attachment settlement has another unresolved child owner")
    proof = {
        "action": ACTION,
        "predecessor": digest(parent),
        "desired": saved["proof"]["desired"] if saved else digest([scope, artifact]),
        "lifecycle": lifecycle.record_sha256,
        "targets": targets,
        "owners": {name: digest(snapshot) for name, snapshot in snapshots.items()},
    }
    proof = json.loads(json.dumps(proof))
    if "runtime" in parent["bindings"]:
        proof["runtime"] = ha_attachment_runtime.observe(
            command,
            parent,
            lifecycle,
            owners,
            snapshots,
            guard,
            prerequisites,
        )
    if saved and proof != saved["proof"]:
        raise ReplanRejected("HA attachment original settlement authority changed")
    result = Settlement(
        command,
        parent,
        lifecycle,
        read_lifecycle,
        owners,
        snapshots,
        child,
        original,
        current,
        path,
        raw,
        saved,
        proof,
        guard,
        refresh_ssh_policy,
        verify_ha,
        prerequisites,
    )
    result.observe(evidence, releasing=releasing)
    return result
