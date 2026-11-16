"""Retire an ordinary pre-Compute prefix without submitting obsolete resources."""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import package_reservation, provisioning_retirement
from .apply_plan import CommandPlan, Progress
from .credential_enrollment import EnrollmentJournal
from .operator_guest import publish, read_file
from .operator_keys import protected_directory
from .ordinary_route_ownership import RouteJournal
from .provisioning_execute import settle_submitted
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .provisioning_trust import TrustJournal
from .recovery import ReplanRejected, WriterBusy
from .replacement import Journal as ReplacementJournal
from .replacement import metadata_only
from .security_group_apply import AttachmentJournal
from .vm_ha_lifecycle import VMHALifecycleStatus

ACTION = "archive-provisioning-prefix-predecessor"
SCHEMA = "nebius-vpngw.provisioning-settlement.v1"


def observe_prerequisites(journal, previous, owners, *, sdk, ssh_observe, credential_observe):
    """Prove no guest writer exists and every completed prerequisite still holds."""
    from nebius.api.nebius.common.v1 import GetByNameRequest
    from nebius.api.nebius.compute.v1 import InstanceServiceClient

    from .provisioning_trust import selection
    from .vm_ha_cloud import nebius_request_error_code_is, vm_ha_request_kwargs

    parent = previous["transaction"]
    plan = previous["bindings"]["provisioning"]
    absent = sorted({recipe.guest for recipe in requests_from_plan(plan) if recipe.guest})
    for name in absent:
        try:
            InstanceServiceClient(sdk).get_by_name(
                GetByNameRequest(parent_id=journal.project, name=name), **vm_ha_request_kwargs()
            ).wait()
        except Exception as error:
            if not nebius_request_error_code_is(error, "NOT_FOUND"):
                raise
        else:
            raise ReplanRejected(
                "Pre-Compute retirement found an existing guest", code="predecessor-not-settled"
            )
    observed: dict[str, Any] = {"absent": absent}
    for name in ("credentials", "ssh"):
        if name not in previous["bindings"]:
            continue
        state = owners[name].snapshot()
        child = state["transaction"] if state else None
        if (
            child is None
            or child["phase"] != "complete"
            or child["intent"] != previous["bindings"][name]
            or child["approval"] != parent["approval"]
            or child["execution_id"] != parent["execution_id"]
            or parent["progress"][name] != Progress.VERIFIED.value
        ):
            raise ReplanRejected("Provisioning prerequisite lacks its original terminal receipt")
        if name == "credentials":
            current = credential_observe(owners[name], child["intent"])
            owners[name]._admit_observation(current, child)
            observed[name] = digest(current.observation_record())
        else:
            policy = ssh_observe(child["intent"])
            current = selection(policy, tuple(child["intent"]["members"]))
            if current != child["selected"]:
                raise ReplanRejected("Provisioning SSH identity changed after publication")
            observed[name] = digest(current)
    return observed


def scope(journal, local, artifact, lifecycle) -> dict | None:
    previous = journal.snapshot()
    parent = previous["transaction"] if previous else None
    if parent is None or parent["phase"] != "pending":
        return None
    plan = CommandPlan.parse(parent["plan"])
    effects = {effect.key: effect for effect in plan.effects}
    if "provisioning" not in effects or effects["provisioning"].owner != "ordinary-provisioning":
        return None
    path = journal.root / "provisioning-settlements" / (parent["execution_id"] + ".json")
    if (
        plan.configuration_sha256 == digest(local)
        and artifact in {None, plan.artifact_sha256}
        and not path.exists()
    ):
        return None
    allowed = {
        "ordinary-provisioning",
        "ordinary-provisioned-runtime",
        "ordinary-provisioned-routes",
        "managed-credentials",
        "ordinary-route-reservation",
        "ordinary-route-runtime",
        "ssh-bootstrap",
        "security-groups",
    }
    if any(effect.owner not in allowed for effect in effects.values()):
        return None
    prior = {"credentials", "ssh", "route-reservation"}
    if any(
        value != Progress.PLANNED.value
        for key, value in parent["progress"].items()
        if key not in prior | {"provisioning"}
    ):
        return None
    if (
        set(parent["receipts"]) - prior
        or parent["progress"]["provisioning"] != Progress.STARTED.value
    ):
        return None
    if lifecycle is not None and (
        lifecycle.status not in {VMHALifecycleStatus.REMOVED, VMHALifecycleStatus.DESTROYED}
    ):
        return None
    desired = previous["bindings"]["provisioning"]
    if effects["provisioning"].request_sha256 != digest(desired):
        raise ReplanRejected("Provisioning predecessor lost its exact parent intent")
    recipes = requests_from_plan(desired)
    guests = {recipe.guest for recipe in recipes if recipe.guest is not None}
    if any(
        effect.owner == "ordinary-provisioned-runtime"
        and effect.targets != (effect.key.removeprefix("guest:"),)
        for effect in effects.values()
    ):
        raise ReplanRejected("Provisioning predecessor has a foreign runtime target")
    if "route-reservation" in effects:
        reservation = previous["bindings"]["route-reservation"]
        if reservation["node"] not in guests or reservation["predecessor"]["authority"] is not None:
            return None
    package_reservation.require_unreserved(journal.root, parent["execution_id"])
    return previous


def _advance(original, current):
    before, after = original["transaction"], current["transaction"]
    if (
        before is None
        or after is None
        or {k: v for k, v in before.items() if k not in {"operations", "verified"}}
        != {k: v for k, v in after.items() if k not in {"operations", "verified"}}
        or set(before["operations"]) != set(after["operations"])
        or not set(before["verified"]) <= set(after["verified"])
    ):
        raise ReplanRejected("Provisioning settlement has another original operation set")
    mutable = {"status", "cloud_operation_id", "resource_id", "submission_count"}
    ranks = {"intent": 0, "accepted": 1, "settled": 2}
    for key, old in before["operations"].items():
        new = after["operations"][key]
        if (
            old["status"] not in ranks
            or new["status"] not in ranks
            or ranks[new["status"]] < ranks[old["status"]]
            or {k: v for k, v in old.items() if k not in mutable}
            != {k: v for k, v in new.items() if k not in mutable}
            or any(old[k] and old[k] != new[k] for k in ("cloud_operation_id", "resource_id"))
            or new.get("submission_count", 1) < old.get("submission_count", 1)
        ):
            raise ReplanRejected("Provisioning settlement changed its original request")


@dataclass
class Settlement:
    journal: Any
    predecessor: dict
    owners: dict
    snapshots: dict
    original: dict
    proof: dict
    path: Any
    raw: dict | None
    record: dict | None

    @property
    def approval(self):
        return digest(self.proof)

    @property
    def resumed(self):
        return self.record is not None

    def execute(self, *, sdk, refresh) -> None:
        with ExitStack() as stack:
            try:
                stack.enter_context(self.journal)
                for owner in self.owners.values():
                    stack.enter_context(owner)
            except BlockingIOError as error:
                raise WriterBusy("A provisioning predecessor owner is still running") from error
            if self.journal.state != self.predecessor:
                raise ReplanRejected("Provisioning predecessor changed after review")
            for name, owner in self.owners.items():
                expected = self.snapshots[name]
                if expected is None:
                    if owner.state["transaction"] is not None or owner.state["bindings"]:
                        raise ReplanRejected("A provisioning predecessor child appeared")
                elif owner.state != expected:
                    raise ReplanRejected("A provisioning predecessor child changed")
            fresh = refresh()
            if fresh is None or fresh.proof != self.proof or fresh.raw != self.raw:
                raise ReplanRejected("Provisioning settlement approval changed")
            expected_file = read_file(self.path)
            if expected_file != self.raw:
                raise ReplanRejected("Provisioning settlement receipt changed")
            record = self.record or {
                "schema": SCHEMA,
                "proof": self.proof,
                "children": self.original,
                "phase": "settling",
            }

            def save():
                nonlocal expected_file
                metadata_only(record)
                value = dict(
                    content=json.dumps(record, sort_keys=True),
                    uid=os.getuid(),
                    gid=os.getgid(),
                    mode=0o600,
                )
                publish(self.path, expected_file, value)
                expected_file = value

            protected_directory(self.path.parent)
            save()
            self.journal.archive()
            for name, owner in self.owners.items():
                if self.snapshots[name] is not None:
                    owner.archive()
            child = self.owners["provisioning"]
            settle_submitted(sdk, child, self.journal)
            provisioning_retirement.seal(child, child.snapshot(), self.proof)
            self.journal.retire_settled(self.predecessor, self.proof)
            record["phase"] = "complete"
            save()


def inspect(journal, *, local, artifact, lifecycle, observe) -> Settlement | None:
    previous = scope(journal, local, artifact, lifecycle)
    if previous is None:
        return None
    parent = previous["transaction"]
    owners: dict[str, Any] = {
        "credentials": EnrollmentJournal(journal.project, journal.gateway),
        "ssh": TrustJournal(journal.project, journal.gateway),
        "attachments": AttachmentJournal(journal.project, journal.gateway),
        "provisioning": ProvisioningJournal(journal.project, journal.gateway),
        "routes": RouteJournal(journal.project, journal.gateway),
        "replacement": ReplacementJournal(journal.project, journal.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in owners.items()}
    path = journal.root / "provisioning-settlements" / (parent["execution_id"] + ".json")
    raw = read_file(path)
    saved = json.loads(raw["content"]) if raw is not None else None
    if saved is not None:
        assert raw is not None
        metadata_only(saved)
        if (
            raw["uid"] != os.getuid()
            or raw["mode"] != 0o600
            or set(saved) != {"schema", "proof", "children", "phase"}
            or saved["schema"] != SCHEMA
            or saved["phase"] not in {"settling", "complete"}
            or set(saved["children"]) != set(owners)
        ):
            raise ReplanRejected("Provisioning settlement receipt is invalid")
    original = saved["children"] if saved is not None else snapshots
    child = original["provisioning"]
    transaction = child["transaction"] if child else None
    if transaction is None:
        return None
    if (
        transaction["plan"] != previous["bindings"]["provisioning"]
        or transaction["approval"] != parent["approval"]
        or transaction["execution_id"] != parent["execution_id"]
    ):
        raise ReplanRejected("Provisioning child belongs to another command")
    submitted = {record["label"] for record in transaction["operations"].values()}
    if any(
        recipe.guest is not None and recipe.key in submitted
        for recipe in requests_from_plan(transaction["plan"])
    ):
        return None
    for name, owner in owners.items():
        original_snapshot = original[name]
        if original_snapshot is not None:
            owner.validate(original_snapshot)
        if name == "provisioning":
            if snapshots[name] is None:
                raise ReplanRejected("Provisioning prefix disappeared")
            _advance(original[name], snapshots[name])
            continue
        if snapshots[name] != original[name]:
            raise ReplanRejected("Provisioning prerequisite changed")
        current_snapshot = snapshots[name]
        current = current_snapshot["transaction"] if current_snapshot else None
        if current is not None and current["phase"] != "complete":
            raise ReplanRejected(
                "Another predecessor owner needs settlement", code="predecessor-not-settled"
            )
        if (
            current is not None
            and current.get("execution_id", current.get("operation_id")) == parent["execution_id"]
            and name not in {"credentials", "ssh"}
        ):
            raise ReplanRejected("Provisioning has a participating runtime owner")
    observed = observe(previous, owners)
    proof = {
        "action": ACTION,
        "predecessor": digest(previous),
        "desired": digest(local),
        "artifact": artifact,
        "lifecycle": lifecycle.record_sha256 if lifecycle else None,
        "owners": {name: digest(value) for name, value in original.items()},
        "observation": observed,
    }
    if saved is not None:
        proof["desired"], proof["artifact"] = saved["proof"]["desired"], saved["proof"]["artifact"]
        if proof != saved["proof"]:
            raise ReplanRejected("Provisioning settlement authority changed")
    metadata_only(proof)
    return Settlement(journal, previous, owners, snapshots, original, proof, path, raw, saved)
