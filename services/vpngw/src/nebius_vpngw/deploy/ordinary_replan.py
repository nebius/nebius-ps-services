"""Retire conclusively settled ordinary owners before planning changed intent.

This boundary never runs an obsolete unsubmitted effect. Unknown child outcomes
remain with their original owner; parent progress alone is never settlement proof.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .. import apply_approval
from .apply_diagnostics import PreparationBlocked
from .apply_plan import CommandPlan, Progress
from .credential_enrollment import EnrollmentJournal
from .ordinary_route_ownership import RouteJournal
from .ordinary_runtime_handoff import Handoff
from .provisioning_journal import ProvisioningJournal
from .provisioning_trust import TrustJournal
from .replacement import Journal as ReplacementJournal
from .security_group_apply import AttachmentJournal, unused_prelude

OWNERS = {
    "ordinary-runtime",
    "ordinary-routes",
    "security-groups",
    "managed-credentials",
    "ordinary-route-reservation",
    "ordinary-route-runtime",
}


def retired_snapshot(value: dict) -> dict:
    if value["transaction"] is None:
        return value
    return {**value, "transaction": None, "revision": value["revision"] + 1}


def parked_holder(proof: dict) -> str:
    return apply_approval.digest(["ordinary-replan", proof["predecessor"], proof["desired"]])


def transferred_snapshot(proof: dict, name: str) -> dict:
    value = proof["route_authority"][name]
    owner = value["authority"]
    if not owner or owner["writer"] != "workstation":
        return value
    holder = parked_holder(proof)
    return {
        **value,
        "authority": {
            **owner,
            "holder": holder,
            "receipt": {
                "holder": holder,
                "request": apply_approval.digest(
                    ["supersede", owner["holder"], holder, apply_approval.digest(value)]
                ),
            },
        },
    }


def changed(snapshot: dict | None, local: dict, targets: dict, artifact: str) -> bool:
    current = snapshot["transaction"] if snapshot else None
    if current is None or current["phase"] != "pending":
        return False
    plan = CommandPlan.parse(current["plan"])
    if not all(effect.owner in OWNERS for effect in plan.effects):
        return False
    return (
        plan.configuration_sha256 != apply_approval.digest({"desired": local, "targets": targets})
        or plan.artifact_sha256 != artifact
    )


def require_child_settled(kind: str, snapshot: dict | None, parent: dict) -> None:
    """Owner-specific proof, including hidden child effects before parent updates."""
    child = snapshot["transaction"] if snapshot else None
    if child is None:
        return
    assert snapshot is not None
    if child["phase"] == "complete":
        return  # Each owner's schema verifies its terminal operation receipts.
    if child.get("approval") != parent["approval"]:
        raise PreparationBlocked("command-execution-changed")
    if kind == "credentials":
        safe = (
            child["execution_id"] == parent["execution_id"]
            and not child["operations"]
            and child["selected"] is None
            and child["receipt"] is None
        )
    elif kind == "routes":
        # A settled prefix is sufficient. Remaining approved requests are
        # obsolete and must never be submitted just to finish this journal.
        records = tuple(child["operations"].values())
        safe = (
            all(record.get("status") == "settled" for record in records)
            and len({record["label"] for record in records}) == len(records)
            and {record["label"] for record in records} == set(child["verified"])
        )
        if safe and records:
            effects = CommandPlan.parse(parent["plan"]).effects
            safe = any(
                effect.owner == "ordinary-routes"
                and effect.request_sha256 == apply_approval.digest(child["intent"])
                for effect in effects
            )
        for record in records:
            if safe:
                wanted = child["plan"]["requests"][record["label"]]
                if not (
                    wanted["type"] == "DeleteRouteRequest"
                    or wanted["type"] == "CreateRouteRequest" and "instance_index" in wanted
                ):
                    # A partial private-table conversion also owns infrastructure.
                    # Keep that receipt until its own successor can reuse it.
                    safe = False
            if safe and record["request_type"] == "CreateRouteRequest":
                owned = snapshot["bindings"].get(record["resource_id"])
                safe = safe and owned is not None and (
                    owned["creation_operation"] == record["cloud_operation_id"]
                    and owned["request_sha256"] == record["request_sha256"]
                )
    elif kind == "attachments":
        safe = child["operation_id"] == parent["execution_id"] and unused_prelude(child)
    else:
        safe = False
    if not safe:
        raise PreparationBlocked("durable-intent-changed")


@dataclass
class Settlement:
    journal: Any
    predecessor: dict
    desired: str
    targets: dict
    guests: dict
    route_authority: dict
    owners: dict
    snapshots: dict
    handoffs: dict
    resumed: bool = False
    pending_routes: Any = None
    pending_attachments: Any = None

    def mapping(self) -> dict:
        return json.loads(
            json.dumps(
                {
                    "action": (
                        "settle-submitted-attachment-predecessor" if self.pending_attachments
                        else "settle-submitted-route-predecessor" if self.pending_routes
                        else "archive-settled-ordinary-predecessor"
                    ),
                    "predecessor": apply_approval.digest(self.predecessor),
                    "desired": self.desired,
                    "targets": self.targets,
                    "guests": self.guests,
                    "route_authority": self.route_authority,
                    "owners": {
                        key: apply_approval.digest(value) for key, value in self.snapshots.items()
                    },
                }
            )
        )

    @property
    def approval(self) -> str:
        return apply_approval.digest(self.mapping())

    def retire(self, fresh: Settlement) -> None:
        if fresh.approval != self.approval:
            raise PreparationBlocked("approval-state-changed")
        # Take every participating local owner's lock before the first archive.
        # A failed lock or changed snapshot leaves every original owner intact.
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(self.journal)
            if self.journal.state != self.predecessor:
                raise PreparationBlocked("approval-state-changed")
            for name, owner in self.owners.items():
                try:
                    stack.enter_context(owner)
                except BlockingIOError as error:
                    from .recovery import WriterBusy

                    raise WriterBusy("A predecessor child owner is still running") from error
                if self.snapshots[name] is None:
                    if owner.state["transaction"] is not None:
                        raise PreparationBlocked("approval-state-changed")
                else:
                    expected = (
                        self.pending_routes.current
                        if name == "routes" and self.pending_routes is not None
                        else self.pending_attachments.current
                        if name == "attachments" and self.pending_attachments is not None
                        else self.snapshots[name]
                    )
                    if owner.state != expected and not (
                        self.resumed and owner.state == retired_snapshot(self.snapshots[name])
                    ):
                        raise PreparationBlocked("approval-state-changed")
            if self.journal.snapshot() != self.predecessor:
                raise PreparationBlocked("approval-state-changed")
            if self.pending_attachments is not None:
                from .apply_command import ReplanReady

                self.pending_attachments.execute(self.mapping())
                raise ReplanReady()
            if self.pending_routes is not None:
                from .apply_command import ReplanReady

                self.pending_routes.execute(self.mapping())
                # The original parent remains pending. Re-observe settled
                # outcomes before reviewing its archival and the fresh plan.
                raise ReplanReady()
            self.journal.prepare_retirement(self.mapping(), self.snapshots)
            for name, value in self.route_authority.items():
                owner = value["authority"]
                if owner and owner["writer"] == "workstation":
                    received = self.handoffs[name].supersede_reservation(
                        owner["holder"], parked_holder(self.mapping()), value
                    )
                    if received != transferred_snapshot(self.mapping(), name):
                        raise PreparationBlocked("durable-intent-changed")
            for name, owner in self.owners.items():
                if self.snapshots[name] is not None and owner.state["transaction"] is not None:
                    if name in {"credentials", "routes", "attachments"}:
                        owner.retire_settled(self.snapshots[name], self.predecessor["transaction"])
                    else:
                        # Completed provisioning/trust/replacement receipts are
                        # still authoritative for retained infrastructure.
                        owner.archive()
            self.journal.retire_settled(self.predecessor, self.mapping())


def inspect(
    journal, *, ssh, instances: tuple, local: dict, targets: dict, artifact: str,
    route_factory=None, attachment_factory=None,
) -> Settlement | None:
    from .ordinary_apply import remote

    snapshot = journal.snapshot()
    saved = journal.retirement()
    if saved and saved["proof"]["predecessor"] != apply_approval.digest(snapshot):
        saved = None
    if not saved and not changed(snapshot, local, targets, artifact):
        return None
    assert snapshot is not None
    parent = snapshot["transaction"]
    plan = CommandPlan.parse(parent["plan"])
    by_name = {item.hostname: item for item in instances}
    if set(targets) != set(by_name):
        raise PreparationBlocked("checkpoint-binding-changed")
    guests = {}
    for effect in plan.effects:
        if effect.owner != "ordinary-runtime":
            continue
        name = effect.key.removeprefix("guest:")
        if name not in targets or tuple(targets[name]) != effect.targets:
            raise PreparationBlocked("checkpoint-binding-changed")
        manifest = snapshot["bindings"][effect.key]
        observed = remote(
            ssh,
            targets[name][1],
            by_name[name],
            local,
            {
                "action": "inspect",
                "manifest": {
                    **manifest,
                    "command_approval": apply_approval.bind(plan.approval, plan.mapping()),
                },
            },
        )["observation"]
        if observed.get("runtime_pending") is not None or observed.get("operation") is not None:
            raise PreparationBlocked("package-parent-changed")
        completion = observed.get("runtime_completion")
        if parent["progress"][effect.key] != Progress.PLANNED.value and not (
            completion
            and completion["approval"] == apply_approval.bind(plan.approval, plan.mapping())
            and completion["manifest"] == effect.request_sha256
        ):
            raise PreparationBlocked("package-parent-changed")
        guests[name] = {
            key: observed.get(key)
            for key in (
                "boot_id",
                "runtime_completion",
                "runtime_pending",
                "operation",
                "config_sha256",
                "files",
                "assets",
            )
        }
    authorities = {}
    handoffs = {}
    previous_holder = apply_approval.digest(
        [journal.project, journal.gateway, parent["execution_id"]]
    )
    for name, instance in by_name.items():
        handoffs[name] = Handoff(ssh, targets[name][1], instance, local)
        value = handoffs[name].inspect()
        if (
            saved
            and name in saved["proof"]["route_authority"]
            and value == transferred_snapshot(saved["proof"], name)
        ):
            value = deepcopy(saved["proof"]["route_authority"][name])
        owner = value["authority"]
        if owner and (
            owner["writer"] == "workstation"
            and owner["holder"] != previous_holder
            or owner["binding"] is not None
            and any(
                owner["binding"][key] != expected
                for key, expected in (
                    ("project", journal.project),
                    ("gateway", journal.gateway),
                    ("node", name),
                )
            )
        ):
            raise PreparationBlocked("durable-intent-changed")
        transaction = value["journal"]["transaction"] if value["journal"] else None
        if transaction and transaction["phase"] != "complete":
            raise PreparationBlocked("durable-intent-changed")
        authorities[name] = value
    owners = {
        "credentials": EnrollmentJournal(journal.project, journal.gateway),
        "routes": RouteJournal(journal.project, journal.gateway),
        "attachments": AttachmentJournal(journal.project, journal.gateway),
        "ssh": TrustJournal(journal.project, journal.gateway),
        "provisioning": ProvisioningJournal(journal.project, journal.gateway),
        "replacement": ReplacementJournal(journal.project, journal.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in owners.items()}
    if saved:
        for name, current in snapshots.items():
            original = saved["children"][name]
            if original is not None:
                owners[name].validate(original)
            if current != original and not (original and current == retired_snapshot(original)):
                raise PreparationBlocked("approval-state-changed")
        snapshots = saved["children"]
    pending_routes = None
    pending_attachments = None
    if not saved and attachment_factory is not None:
        from . import attachment_settlement

        pending_attachments = attachment_settlement.inspect(
            journal, owners["attachments"], parent, snapshots["attachments"],
            **attachment_factory(owners["attachments"]),
        )
        if pending_attachments is not None:
            snapshots["attachments"] = pending_attachments.original
    if not saved and route_factory is not None and snapshots["routes"] is not None:
        from . import ordinary_route_settlement

        child = snapshots["routes"]["transaction"]
        if child and child["phase"] == "pending" and child["operations"]:
            sdk, observe = route_factory(owners["routes"])
            pending_routes = ordinary_route_settlement.inspect(
                journal, owners["routes"], parent, snapshots["routes"], sdk=sdk, observe=observe,
            )
            if pending_routes is not None:
                if any(
                    value["authority"] is None
                    or value["authority"]["writer"] != "workstation"
                    for value in authorities.values()
                ):
                    raise PreparationBlocked("durable-intent-changed")
                snapshots["routes"] = pending_routes.original
    for name, child_snapshot in snapshots.items():
        if name == "routes" and pending_routes is not None:
            continue
        if name == "attachments" and pending_attachments is not None:
            continue
        require_child_settled(name, child_snapshot, parent)
    if pending_routes is not None and pending_attachments is not None:
        raise PreparationBlocked("durable-intent-changed")
    proposal = Settlement(
        journal,
        snapshot,
        apply_approval.digest({"desired": local, "artifact": artifact}),
        targets,
        guests,
        authorities,
        owners,
        snapshots,
        handoffs,
        saved is not None or bool(pending_routes and pending_routes.saved) or bool(pending_attachments and pending_attachments.saved),
        pending_routes,
        pending_attachments,
    )
    if saved:
        # Finish only the already approved archival workflow if desired input
        # changed again. It performs no obsolete deployment steps.
        proposal.desired = saved["proof"]["desired"]
        if proposal.mapping() != saved["proof"]:
            raise PreparationBlocked("approval-state-changed")
    if pending_routes is not None and pending_routes.saved is not None:
        proposal.desired = pending_routes.saved["proof"]["desired"]
        if proposal.mapping() != pending_routes.saved["proof"]:
            raise PreparationBlocked("approval-state-changed")
    if pending_attachments is not None and pending_attachments.saved is not None:
        proposal.desired = pending_attachments.saved["proof"]["desired"]
        if proposal.mapping() != pending_attachments.saved["proof"]:
            raise PreparationBlocked("approval-state-changed")
    return proposal
