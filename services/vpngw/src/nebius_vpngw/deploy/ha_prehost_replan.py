"""Retire a conclusively settled reuse-only HA parent before fresh planning.

The lifecycle owner independently proves exact Compute, allocation, route and
guest authority. No obsolete package or cloud step runs to retire the parent.
Completed child owners remain intact; the next plan observes their current
resources without inheriting their historical create intent.
"""

from __future__ import annotations

import json
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
from .provisioning_trust import TrustJournal, selection
from .recovery import WriterBusy
from .replacement import Journal as ReplacementJournal
from .security_group_apply import AttachmentJournal
from .vm_ha_lifecycle import VMHALifecycleState
from .vm_ha_prehost import validate_prehost_cloud, validate_prehost_hosts

ACTION = "archive-settled-ha-prehost-predecessor"


@dataclass
class Settlement:
    journal: CommandJournal
    predecessor: dict
    proof: dict
    children: dict
    owners: dict

    @property
    def approval(self) -> str:
        return digest(self.proof)

    def review(self, *, approve, refresh, supplied: str | None, dry_run: bool) -> bool:
        resumed = self.journal.retirement_completed(self.proof)
        if not resumed or dry_run:
            accepted = approve(self.approval, supplied=supplied, dry_run=dry_run)
            if dry_run or not accepted:
                return False
        elif supplied not in {None, self.approval}:
            raise PreparationBlocked("approval-state-changed")
        current = refresh()
        if current is None:
            raise PreparationBlocked("durable-intent-changed")
        self.retire(current)
        return True

    def retire(self, refreshed: Settlement) -> None:
        if refreshed.proof != self.proof:
            raise PreparationBlocked("approval-state-changed")
        with ExitStack() as stack:
            stack.enter_context(self.journal)
            if self.journal.state != self.predecessor:
                raise PreparationBlocked("durable-intent-changed")
            for name, owner in self.owners.items():
                # Even an absent child needs exclusion while its parent retires.
                expected = self.children[name]
                try:
                    stack.enter_context(owner)
                except BlockingIOError as error:
                    raise WriterBusy("HA prehost child owner is still running") from error
                if expected is None:
                    if owner.state["transaction"] is not None:
                        raise PreparationBlocked("durable-intent-changed")
                elif owner.state != expected:
                    raise PreparationBlocked("durable-intent-changed")
            self.journal.retire_settled(self.predecessor, self.proof)


def inspect(
    journal: CommandJournal,
    *,
    lifecycle: VMHALifecycleState,
    cloud: dict,
    hosts: dict,
    bindings: dict,
    desired: str,
    credential_plan,
    ssh_policy,
) -> Settlement | None:
    previous = journal.snapshot()
    parent = previous["transaction"] if previous else None
    if parent is None or parent["phase"] != "pending":
        return None
    assert previous is not None
    plan = CommandPlan.parse(parent["plan"])
    runtime = previous["bindings"].get("runtime")
    if (
        runtime is None
        or runtime.get("preparation")
        or runtime.get("continuation")
        or runtime.get("successor")
        or any(
            effect.owner
            not in {"ha-lifecycle", "managed-credentials", "ssh-bootstrap", "security-groups"}
            for effect in plan.effects
        )
        or sum(
            effect.key == "runtime" and effect.owner == "ha-lifecycle" for effect in plan.effects
        )
        != 1
        or parent["progress"]["runtime"] == Progress.VERIFIED.value
        or lifecycle.transaction is None
    ):
        raise PreparationBlocked("command-execution-changed")
    packages = runtime.get("packages")
    if (
        packages
        and packages["checkpoint"] == lifecycle.record_sha256
        and runtime["report"]["kind"] == "recovery"
    ):
        # Already approved successor, interrupted before lifecycle publication.
        # Ordinary command resume still checks its exact current scope/artifact.
        return None
    validate_prehost_cloud(lifecycle, cloud, bindings)
    validate_prehost_hosts(lifecycle, hosts)
    prior_bindings = dict(lifecycle.transaction.resource_bindings)
    if prior_bindings.get("runtime-command-execution") != parent["execution_id"]:
        raise PreparationBlocked("command-execution-changed")
    approval = json.loads(prior_bindings.get("runtime-command-approval", "null"))
    if (
        not isinstance(approval, dict)
        or approval.get("approval") != runtime["approval"]
        or approval.get("scope") != runtime["runtime_scope"]
    ):
        raise PreparationBlocked("approval-state-changed")
    members = runtime.get("members")
    if members and any(value["kind"] != "retained" for value in members.values()):
        raise PreparationBlocked("checkpoint-binding-changed")
    actual_targets = tuple(sorted(member.compute_id for member in lifecycle.members))
    runtime_effect = next(effect for effect in plan.effects if effect.key == "runtime")
    if tuple(sorted(runtime_effect.targets)) != actual_targets:
        raise PreparationBlocked("checkpoint-binding-changed")
    fresh = credential_plan.observation_record()
    if (
        credential_plan.credentials is None
        or fresh["action"] != "reuse"
        or any(value["action"] != "reuse" for value in fresh["iam_resources"].values())
    ):
        raise PreparationBlocked("checkpoint-binding-changed")
    if ssh_policy is None or ssh_policy.managed_action is not None:
        raise PreparationBlocked("checkpoint-binding-changed")
    ssh_policy.assert_current()
    owners = {
        "credentials": EnrollmentJournal(journal.project, journal.gateway),
        "ssh": TrustJournal(journal.project, journal.gateway),
        "attachments": AttachmentJournal(journal.project, journal.gateway),
        "routes": RouteJournal(journal.project, journal.gateway),
        "provisioning": ProvisioningJournal(journal.project, journal.gateway),
        "replacement": ReplacementJournal(journal.project, journal.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in owners.items()}
    for name, snapshot in snapshots.items():
        child = snapshot["transaction"] if snapshot else None
        if name not in {"credentials", "ssh"}:
            # A submitted or unused pending SG child needs its own settlement.
            if child and child["phase"] != "complete":
                raise PreparationBlocked("durable-intent-changed")
            continue
        expected = previous["bindings"].get(name)
        if child and child["phase"] != "complete":
            raise PreparationBlocked("durable-intent-changed")
        if expected is None:
            continue
        if (
            not child
            or child["intent"] != expected
            or child["execution_id"] != parent["execution_id"]
            or child["approval"] != parent["approval"]
        ):
            raise PreparationBlocked("command-execution-changed")
        if name == "credentials":
            credential_owner = owners[name]
            assert isinstance(credential_owner, EnrollmentJournal)
            credential_owner._admit_observation(credential_plan, child)
            if (
                child["receipt"] != credential_plan.credentials.resource_bindings()
                or child["selected"] != credential_plan.public_key_sha256
                or any(record["status"] != "settled" for record in child["operations"].values())
            ):
                raise PreparationBlocked("checkpoint-binding-changed")
            receipt = {
                "intent": digest(child["intent"]),
                "selected": child["selected"],
                "bindings": digest(child["receipt"]),
                "operations": digest(child["operations"]),
                "execution_id": child["execution_id"],
            }
        else:
            if child["selected"] != selection(ssh_policy, tuple(child["intent"]["members"])):
                raise PreparationBlocked("checkpoint-binding-changed")
            receipt = {
                "intent": digest(child["intent"]),
                "selection": digest(child["selected"]),
                "execution_id": child["execution_id"],
            }
        if parent["progress"][name] == Progress.VERIFIED.value and parent["receipts"][name][
            "sha256"
        ] != digest(receipt):
            raise PreparationBlocked("command-execution-changed")
    root = package_reservation.recovery_owner(journal.root, parent["execution_id"], digest(runtime))
    for member in lifecycle.members:
        if package_reservation.read(*root, member.instance_name) is not None:
            raise PreparationBlocked("package-parent-changed")
    stable_hosts = json.loads(json.dumps(hosts))
    for host in stable_hosts.values():
        host["checkpoint"].pop("sequence", None)
    proof = {
        "action": ACTION,
        "predecessor": digest(previous),
        "lifecycle": lifecycle.record_sha256,
        "desired": desired,
        "cloud": digest(cloud),
        "hosts": digest(stable_hosts),
        "owners": {name: digest(value) for name, value in snapshots.items()},
    }
    return Settlement(journal, previous, proof, snapshots, owners)
