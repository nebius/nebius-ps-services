"""Settle an obsolete credential-only prefix before a newly approved deployment.

Submitted IAM effects remain with their original enrollment and key. This owner
never executes the unsubmitted graph, guest preparation, or infrastructure work.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import package_reservation
from .apply_plan import CommandPlan, Progress
from .credential_enrollment import EnrollmentJournal
from .ordinary_route_ownership import RouteJournal
from .provisioning_journal import ProvisioningJournal
from .provisioning_trust import TrustJournal
from .recovery import ReplanRejected, WriterBusy
from .replacement import Journal as ReplacementJournal
from .replacement import metadata_only
from .security_group_apply import AttachmentJournal
from .vm_ha_lifecycle import VMHALifecycleStatus

ACTION = "archive-credential-predecessor"
SCHEMA = "nebius-vpngw.credential-settlement.v1"


def _retired(snapshot: dict) -> dict:
    return {**snapshot, "transaction": None, "revision": snapshot["revision"] + 1}


def _advance(original: dict, current: dict) -> None:
    """Only the original submitted operation set may progress before retirement."""
    before, after = original["transaction"], current["transaction"]
    if (
        before is None
        or after is None
        or {k: v for k, v in before.items() if k not in {"operations", "selected"}}
        != {k: v for k, v in after.items() if k not in {"operations", "selected"}}
        or before["selected"] is not None
        and after["selected"] != before["selected"]
        or set(before["operations"]) != set(after["operations"])
    ):
        raise ReplanRejected("Credential settlement owner changed")
    ranks = {"intent": 0, "accepted": 1, "settled": 2}
    mutable = {"status", "cloud_operation_id", "resource_id", "submission_count"}
    for key, old in before["operations"].items():
        new = after["operations"][key]
        if (
            old["status"] not in ranks
            or new["status"] not in ranks
            or ranks[new["status"]] < ranks[old["status"]]
            or {k: v for k, v in old.items() if k not in mutable}
            != {k: v for k, v in new.items() if k not in mutable}
            or any(old[k] and old[k] != new[k] for k in ("cloud_operation_id", "resource_id"))
            or new["submission_count"] < old["submission_count"]
        ):
            raise ReplanRejected("Credential settlement changed an original IAM request")


def scope(journal, local: dict, lifecycle, artifact: str | None) -> dict | None:
    previous = journal.snapshot()
    parent = previous["transaction"] if previous else None
    if parent is None or parent["phase"] != "pending" or "credentials" not in previous["bindings"]:
        return None
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
    retained = journal.root / "credential-settlements" / (parent["execution_id"] + ".json")
    if not owners <= allowed or (
        plan.configuration_sha256 == wanted
        and artifact in {None, plan.artifact_sha256}
        and not retained.exists()
    ):
        return None
    if any(
        value != Progress.PLANNED.value
        for key, value in parent["progress"].items()
        if key != "credentials"
    ):
        return None
    if set(parent["receipts"]) - {"credentials"}:
        return None
    effect = next((item for item in plan.effects if item.key == "credentials"), None)
    if (
        effect is None
        or effect.owner != "managed-credentials"
        or effect.request_sha256 != digest(previous["bindings"]["credentials"])
    ):
        raise ReplanRejected("Credential settlement lacks its original parent effect")
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
    if runtime is not None and (
        any(runtime.get(key) for key in ("preparation", "continuation", "successor"))
        or package_reservation.recovery_owner(journal.root, parent["execution_id"], digest(runtime))
        != (journal.root, parent["execution_id"], digest(runtime))
    ):
        return None
    package_reservation.require_unreserved(journal.root, parent["execution_id"])
    return previous


@dataclass
class Settlement:
    journal: Any
    predecessor: dict
    owners: dict
    snapshots: dict
    original: dict
    proof: dict
    observed: Any
    path: Any
    raw: dict | None
    record: dict | None

    @property
    def approval(self) -> str:
        return digest(self.proof)

    @property
    def resumed(self) -> bool:
        return self.record is not None

    def execute(self, *, refresh, observe, settle) -> None:
        from .operator_guest import publish, read_file
        from .operator_keys import protected_directory

        with ExitStack() as stack:
            try:
                stack.enter_context(self.journal)
                for owner in self.owners.values():
                    stack.enter_context(owner)
            except BlockingIOError as error:
                raise WriterBusy("A credential predecessor owner is still running") from error
            if self.journal.state != self.predecessor:
                raise ReplanRejected("Credential predecessor changed after review")
            for name, owner in self.owners.items():
                expected = self.snapshots[name]
                if expected is None:
                    if owner.state["transaction"] is not None or owner.state["bindings"]:
                        raise ReplanRejected("A credential predecessor child appeared")
                elif owner.state != expected:
                    raise ReplanRejected("A credential predecessor child changed")
            fresh = refresh()
            if fresh is None or fresh.proof != self.proof or fresh.raw != self.raw:
                raise ReplanRejected("Credential settlement approval changed")
            package_reservation.require_unreserved(
                self.journal.root, self.predecessor["transaction"]["execution_id"]
            )
            expected_file = read_file(self.path)
            if expected_file != self.raw:
                raise ReplanRejected("Credential settlement record changed after review")
            record = self.record or {
                "schema": SCHEMA,
                "proof": self.proof,
                "children": self.original,
                "phase": "settling",
                "settled_child": None,
            }

            def save():
                nonlocal expected_file
                metadata_only(record)
                value = {
                    "content": json.dumps(record, sort_keys=True),
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "mode": 0o600,
                }
                publish(self.path, expected_file, value)
                expected_file = value

            protected_directory(self.path.parent)
            save()
            self.journal.archive()
            for name, owner in self.owners.items():
                if self.snapshots[name] is not None:
                    owner.archive()
            credentials = self.owners["credentials"]
            if record["phase"] == "settling":
                settle(credentials, fresh.observed)
                observed = observe(credentials)
                child = credentials.state["transaction"]
                credentials._admit_observation(observed, child)
                if any(item["status"] != "settled" for item in child["operations"].values()) or (
                    observed.pending_present
                    and not observed.journal_present
                    and not observed.source_present
                ):
                    raise ReplanRejected(
                        "Credential predecessor did not settle", code="predecessor-not-settled"
                    )
                record["settled_child"] = credentials.snapshot()
                record["phase"] = "retiring"
                save()
            settled = record["settled_child"]
            if credentials.state == settled:
                credentials.retire_predecessor(
                    settled, self.predecessor["transaction"], observe(credentials)
                )
            elif credentials.state != _retired(settled):
                raise ReplanRejected("Settled credential receipt changed before retirement")
            self.journal.retire_settled(self.predecessor, self.proof)
            record["phase"] = "complete"
            save()


def inspect(journal, *, local: dict, lifecycle, artifact: str | None, observe) -> Settlement | None:
    from .operator_guest import read_file

    previous = scope(journal, local, lifecycle, artifact)
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
    path = journal.root / "credential-settlements" / (parent["execution_id"] + ".json")
    raw = read_file(path)
    saved = json.loads(raw["content"]) if raw is not None else None
    if saved is not None:
        assert raw is not None
        metadata_only(saved)
        if (
            raw["uid"] != os.getuid()
            or raw["mode"] != 0o600
            or set(saved) != {"schema", "proof", "children", "phase", "settled_child"}
            or saved["schema"] != SCHEMA
            or saved["phase"] not in {"settling", "retiring", "complete"}
            or saved["proof"]["predecessor"] != digest(previous)
            or set(saved["children"]) != set(owners)
        ):
            raise ReplanRejected("Credential settlement record is invalid")
    original = saved["children"] if saved is not None else snapshots
    enrollment = original["credentials"]
    child = enrollment["transaction"] if enrollment else None
    if child is None:
        return None  # No credential owner started: the unused-plan path handles it.
    assert enrollment is not None
    if (
        child["approval"] != parent["approval"]
        or child["execution_id"] != parent["execution_id"]
        or child["intent"] != previous["bindings"]["credentials"]
    ):
        raise ReplanRejected("Credential settlement has another original owner")
    for name, owner in owners.items():
        original_snapshot = original[name]
        if original_snapshot is not None:
            owner.validate(original_snapshot)
        if name == "credentials":
            continue
        if snapshots[name] != original[name]:
            raise ReplanRejected("Credential settlement child changed")
        current_snapshot = snapshots[name]
        transaction = current_snapshot["transaction"] if current_snapshot else None
        if transaction and (
            transaction["phase"] != "complete"
            or transaction.get("execution_id", transaction.get("operation_id"))
            == parent["execution_id"]
            or transaction.get("approval") == parent["approval"]
        ):
            raise ReplanRejected(
                "Another predecessor owner needs settlement", code="predecessor-not-settled"
            )
    current = snapshots["credentials"]
    if current is None:
        raise ReplanRejected("Credential predecessor evidence disappeared")
    if saved is not None and saved["phase"] in {"retiring", "complete"}:
        settled = saved["settled_child"]
        owners["credentials"].validate(settled)
        _advance(enrollment, settled)
        if current not in (settled, _retired(settled)):
            raise ReplanRejected("Credential retirement receipt changed")
        evidence = settled["transaction"]
    else:
        _advance(enrollment, current)
        evidence = current["transaction"]
    observed = observe(owners["credentials"])
    owners["credentials"]._admit_observation(observed, evidence)
    proof = {
        "action": ACTION,
        "predecessor": digest(previous),
        "desired": digest(local),
        "artifact": artifact,
        "lifecycle": lifecycle.record_sha256 if lifecycle is not None else None,
        "owners": {name: digest(value) for name, value in original.items()},
        "public_key_sha256": observed.public_key_sha256,
        "source_present": observed.source_present,
        "pending_present": observed.pending_present,
    }
    if saved is not None:
        proof["desired"], proof["artifact"] = saved["proof"]["desired"], saved["proof"]["artifact"]
        if proof != saved["proof"]:
            raise ReplanRejected("Credential settlement authority changed")
    return Settlement(
        journal, previous, owners, snapshots, original, proof, observed, path, raw, saved
    )
