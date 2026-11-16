"""Retire unused HA/provisioning authority without starting any child owner.

This path requires that no child authorization was published. Started or uncertain
work belongs to the corresponding settlement owner, never this unused-plan path.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

from ..apply_approval import digest
from . import package_reservation
from .apply_command import CommandJournal
from .apply_diagnostics import PreparationBlocked
from .apply_plan import CommandPlan, Progress
from .credential_enrollment import EnrollmentJournal
from .ordinary_route_ownership import RouteJournal
from .provisioning_journal import ProvisioningJournal
from .provisioning_trust import TrustJournal
from .recovery import WriterBusy
from .replacement import Journal as ReplacementJournal
from .security_group_apply import AttachmentJournal
from .vm_ha_lifecycle import VMHALifecycleState, VMHALifecycleStatus

ACTION = "archive-unused-command-predecessor"


@dataclass
class Settlement:
    journal: CommandJournal
    predecessor: dict
    proof: dict
    owners: dict
    snapshots: dict

    @property
    def approval(self) -> str:
        return digest(self.proof)

    def retire(self, fresh: Settlement) -> None:
        if self.proof != fresh.proof:
            raise PreparationBlocked("approval-state-changed")
        with ExitStack() as stack:
            try:
                stack.enter_context(self.journal)
                for owner in self.owners.values():
                    stack.enter_context(owner)
            except BlockingIOError as error:
                raise WriterBusy("An unused predecessor owner is still running") from error
            if self.journal.state != self.predecessor:
                raise PreparationBlocked("approval-state-changed")
            package_reservation.require_unreserved(
                self.journal.root, self.predecessor["transaction"]["execution_id"]
            )
            for name, owner in self.owners.items():
                previous = self.snapshots[name]
                if previous is None:
                    if owner.state["transaction"] is not None or owner.state["bindings"]:
                        raise PreparationBlocked("durable-intent-changed")
                elif owner.state != previous:
                    raise PreparationBlocked("durable-intent-changed")
            # Historical completed children remain canonical. Only the unused
            # parent is archived; no submitted operation needs to be settled.
            self.journal.retire_settled(self.predecessor, self.proof)


def inspect(
    journal: CommandJournal,
    *,
    local: dict,
    lifecycle: VMHALifecycleState | None,
    artifact: str | None = None,
) -> Settlement | None:
    previous = journal.snapshot()
    parent = previous["transaction"] if previous else None
    if parent is None or parent["phase"] != "pending":
        return None
    assert previous is not None
    plan = CommandPlan.parse(parent["plan"])
    owners = {effect.owner for effect in plan.effects}
    if "ha-lifecycle" in owners:
        wanted = digest(
            {"desired": local, "artifact": plan.artifact_sha256, "project": journal.project}
        )
        allowed = {"ha-lifecycle", "managed-credentials", "ssh-bootstrap", "security-groups"}
    elif "ordinary-provisioning" in owners:
        wanted = digest(local)
        allowed = {
            "ordinary-provisioning",
            "ordinary-provisioned-routes",
            "ordinary-provisioned-runtime",
            "ordinary-runtime",
            "ssh-bootstrap",
            "security-groups",
            "managed-credentials",
            "ordinary-route-reservation",
            "ordinary-route-runtime",
        }
    else:
        return None
    if not owners <= allowed or (
        plan.configuration_sha256 == wanted and artifact in {None, plan.artifact_sha256}
    ):
        return None
    if parent["receipts"] or any(
        value != Progress.PLANNED.value for value in parent["progress"].values()
    ):
        return None
    if lifecycle is not None and (
        lifecycle.status
        not in {
            VMHALifecycleStatus.ACTIVE,
            VMHALifecycleStatus.REMOVED,
            VMHALifecycleStatus.DESTROYED,
        }
        or lifecycle.transaction is not None
        and dict(lifecycle.transaction.resource_bindings).get("runtime-command-execution")
        == parent["execution_id"]
    ):
        return None
    runtime = previous["bindings"].get("runtime")
    if runtime is not None:
        if runtime.get("preparation") or runtime.get("continuation") or runtime.get("successor"):
            return None
        recovery_owner = package_reservation.recovery_owner(
            journal.root, parent["execution_id"], digest(runtime)
        )
        # A composed package owner may be older than this parent. Its existence
        # needs the started-work recovery path, even without a reservation yet.
        if recovery_owner != (journal.root, parent["execution_id"], digest(runtime)):
            raise PreparationBlocked("package-parent-changed")
    try:
        package_reservation.require_unreserved(journal.root, parent["execution_id"])
    except RuntimeError as error:
        raise PreparationBlocked("package-parent-changed") from error
    children = {
        "credentials": EnrollmentJournal(journal.project, journal.gateway),
        "ssh": TrustJournal(journal.project, journal.gateway),
        "attachments": AttachmentJournal(journal.project, journal.gateway),
        "provisioning": ProvisioningJournal(journal.project, journal.gateway),
        "routes": RouteJournal(journal.project, journal.gateway),
        "replacement": ReplacementJournal(journal.project, journal.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in children.items()}
    for snapshot in snapshots.values():
        child = snapshot["transaction"] if snapshot else None
        if child is not None and (
            child["phase"] != "complete"
            or child.get("execution_id", child.get("operation_id")) == parent["execution_id"]
            or child.get("approval") == parent["approval"]
        ):
            raise PreparationBlocked("command-execution-changed")
    return Settlement(
        journal,
        previous,
        {
            "action": ACTION,
            "predecessor": digest(previous),
            "desired": digest(local),
            "artifact": artifact,
            "lifecycle": lifecycle.record_sha256 if lifecycle is not None else None,
            "owners": {name: digest(value) for name, value in snapshots.items()},
        },
        children,
        snapshots,
    )
