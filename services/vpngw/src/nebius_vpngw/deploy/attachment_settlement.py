"""Settle submitted ordinary NIC updates without applying an obsolete suffix."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import security_group_apply as attachments
from .apply_plan import CommandPlan, Progress
from .operator_guest import publish, read_file
from .operator_keys import protected_directory
from .recovery import ReplanRejected
from .replacement import metadata_only
from .replacement_cloud import Cloud

SCHEMA = "nebius-vpngw.attachment-settlement.v1"


def admit_uncertain(manager, journal, member, record) -> None:
    from nebius.api.nebius.compute.v1 import SecurityGroup

    from .ordinary_route_conversion import readback_matches
    from .replacement_cloud import message_digest, restore_compute_update
    from .vm_ha_cloud import clone_nebius_sdk_message

    current = Cloud(manager._get_client(), journal).get_instance(member["compute_id"])
    requested = restore_compute_update(record, current)
    continuity = journal.state["transaction"].get("owner_continuity")
    if continuity is not None and continuity["compute_id"] == member["compute_id"]:
        if (
            str(requested.metadata.resource_version) != continuity["before_revision"]
            or message_digest(requested.spec) != continuity["after_spec"]
            or (str(current.metadata.resource_version), message_digest(current.spec))
            not in {
                (continuity["before_revision"], continuity["before_spec"]),
                (continuity["after_revision"], continuity["after_spec"]),
            }
        ):
            raise ReplanRejected(
                "Uncertain attachment owner lost its exact prepared revision and spec",
                code="predecessor-infrastructure-changed",
            )
    if current.metadata.resource_version > requested.metadata.resource_version and readback_matches(
        current, requested
    ):
        return
    before = clone_nebius_sdk_message(requested)
    interfaces = list(before.spec.network_interfaces)
    prior = {item["name"]: item["explicit_ids"] for item in member["before"]}
    for interface in interfaces:
        interface.security_groups = [
            SecurityGroup(id=identifier) for identifier in prior[interface.name]
        ]
    before.spec.network_interfaces = interfaces
    if (
        current.metadata.resource_version != before.metadata.resource_version
        or not readback_matches(current, before)
    ):
        raise ReplanRejected(
            "Uncertain attachment request has unrelated Compute drift",
            code="predecessor-infrastructure-changed",
        )


def retired(state: dict) -> dict:
    return {**state, "transaction": None, "revision": state["revision"] + 1}


def require_original(parent: dict, original: dict, current: dict) -> None:
    before, after = original["transaction"], current["transaction"]
    effect = next(
        (item for item in CommandPlan.parse(parent["plan"]).effects if item.key == "attachments"),
        None,
    )
    if (
        before is None
        or after is None
        or before["phase"] != "pending"
        or effect is None
        or effect.owner != "security-groups"
        or effect.request_sha256 != digest(before["plan"])
        or before["approval"] != parent["approval"]
        or before["operation_id"] != parent["execution_id"]
        or parent["progress"]["attachments"] != Progress.STARTED.value
        or any(
            value != Progress.VERIFIED.value
            for key, value in parent["progress"].items()
            if key != "attachments"
        )
        or before["locks"]
        or any(
            key in before
            for key in ("owner", "owner_inhibition", "owner_continuity", "release_started")
        )
        or not before.get("guests")
        or any(value["binding"] is not None for value in before["guests"].values())
        or {k: v for k, v in before.items() if k != "operations"}
        != {k: v for k, v in after.items() if k != "operations"}
        or set(before["operations"]) != set(after["operations"])
    ):
        raise ReplanRejected(
            "Attachment predecessor lacks settled ordinary authority",
            code="predecessor-not-settled",
        )
    require_operations(before, after)


def require_operations(before: dict, after: dict) -> None:
    """Admit only monotonic outcomes of the unchanged submitted request set."""
    if set(before["operations"]) != set(after["operations"]):
        raise ReplanRejected("Attachment settlement changed its original request set")
    mutable = {"status", "cloud_operation_id", "resource_id", "submission_count"}
    ranks = {"intent": 0, "accepted": 1, "settled": 2, "rejected": 2}
    for key, old in before["operations"].items():
        new = after["operations"][key]
        if (
            old["status"] not in ranks
            or new["status"] not in ranks
            or old["status"] == "rejected"
            and new != old
            or ranks[new["status"]] < ranks[old["status"]]
            or {k: v for k, v in old.items() if k not in mutable}
            != {k: v for k, v in new.items() if k not in mutable}
            or any(old[k] and old[k] != new[k] for k in ("cloud_operation_id", "resource_id"))
            or new.get("submission_count", 1) < old.get("submission_count", 1)
        ):
            raise ReplanRejected("Attachment settlement changed its original request")


def cloud_evidence(manager, command, journal, evidence: dict, *, settled: bool) -> dict:
    probe = copy.copy(journal)
    probe.state = evidence
    transaction = evidence["transaction"]
    plan = transaction["plan"]
    rejected = any(r["status"] == "rejected" for r in transaction["operations"].values())
    if rejected:
        from .attachment_retry import plan as rejection_proof

        # Reuse the exact single-attempt, full-request no-effect proof.
        # Retirement does not authorize the obsolete request's successor.
        if rejection_proof(manager, command, transaction) is None:
            raise ReplanRejected("Attachment rejection has no conclusive no-effect proof")
    attachments.require_plan(manager, plan, allow_desired=True)
    cloud_proofs = {}
    for name, member in plan["members"].items():
        record = next(
            (
                r
                for r in transaction["operations"].values()
                if r["label"] == "security-groups-" + name
            ),
            None,
        )
        if record is not None and record["status"] == "settled":
            cloud_proofs[name] = attachments.verified_update(manager, probe, name)
        elif record is not None and record["status"] == "rejected":
            continue  # Independently proven above; never resubmit this request.
        elif settled and record is not None:
            raise ReplanRejected(
                "Attachment request remains unsettled", code="predecessor-not-settled"
            )
        elif record is not None:
            admit_uncertain(manager, probe, member, record)
        elif record is None:
            actual = attachments.inspect_plan(manager, (name,), tuple(plan["desired_ids"]))
            if actual["members"][name]["before"] != member["before"]:
                raise ReplanRejected("An unsubmitted attachment member changed")
    return cloud_proofs


@dataclass
class Pending:
    command: Any
    journal: Any
    parent: dict
    manager: Any
    ssh: Any
    instances: tuple
    local: dict
    targets: dict
    refresh_ssh_policy: Any
    path: Any
    original: dict
    current: dict
    raw: dict | None
    saved: dict | None

    def observe(self, evidence: dict, *, settled: bool) -> dict:
        transaction = evidence["transaction"]
        plan = transaction["plan"]
        if self.manager.inspect_ordinary_targets(tuple(self.targets)) != self.targets:
            raise ReplanRejected("Attachment settlement lost its exact Compute targets")
        configs = {item.hostname: item for item in self.instances}
        if set(configs) != set(transaction["guests"]) or any(
            getattr(item, "vm_ha_node", None) is not None for item in configs.values()
        ):
            raise ReplanRejected("Attachment settlement has a different guest inventory")
        if any(
            self.targets[name][0] != member["compute_id"]
            for name, member in plan["members"].items()
        ):
            raise ReplanRejected("Attachment settlement selected another Compute member")
        guests = {
            name: attachments.guest_identity(self.ssh, self.targets[name][1], item, self.local)
            for name, item in configs.items()
        }
        if guests != transaction["guests"]:
            raise ReplanRejected("Attachment guest identity changed")
        return cloud_evidence(self.manager, self.command, self.journal, evidence, settled=settled)

    def execute(self, proof: dict) -> None:
        if self.journal._lock is None or self.journal.state != self.current:
            raise ReplanRejected("Attachment settlement owner changed after review")
        if read_file(self.path) != self.raw or self.saved and self.saved["proof"] != proof:
            raise ReplanRejected("Attachment settlement approval changed")
        record = self.saved or {
            "schema": SCHEMA,
            "proof": proof,
            "original": self.original,
            "phase": "settling",
            "settled": None,
        }
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

        evidence = self.current if record["phase"] == "settling" else record["settled"]
        self.observe(evidence, settled=record["phase"] != "settling")
        protected_directory(self.path.parent)
        save()
        if record["phase"] == "settling":
            self.journal.archive()
            rejected = any(
                r["status"] == "rejected"
                for r in self.journal.state["transaction"]["operations"].values()
            )
            if not rejected:
                Cloud(self.manager._get_client(), self.journal).settle()
            # The same original request may have become visible during settlement.
            # Refresh only cloud evidence; the SSH owner must retain exact pins.
            for name in evidence["transaction"]["plan"]["members"]:
                if any(
                    r["label"] == "security-groups-" + name and r["status"] == "rejected"
                    for r in self.journal.state["transaction"]["operations"].values()
                ):
                    continue
                verified = attachments.verified_update(self.manager, self.journal, name)
                if verified is not None:
                    attachments.refresh_guest_trust(
                        self.manager,
                        self.ssh,
                        self.journal,
                        name,
                        verified,
                        self.refresh_ssh_policy,
                    )
            self.observe(self.journal.state, settled=True)
            record["settled"] = self.journal.snapshot()
            record["phase"] = "retiring"
            save()
        settled = record["settled"]
        if self.journal.state == settled:
            self.journal.retire_predecessor(settled, self.parent)
        elif self.journal.state != retired(settled):
            raise ReplanRejected("Attachment retirement receipt changed")
        record["phase"] = "complete"
        save()


def inspect(
    command,
    journal,
    parent: dict,
    current: dict | None,
    *,
    manager,
    ssh,
    instances,
    local,
    targets,
    refresh_ssh_policy,
) -> Pending | None:
    path = command.root / "attachment-settlements" / (parent["execution_id"] + ".json")
    raw = read_file(path)
    saved = json.loads(raw["content"]) if raw else None
    child = current["transaction"] if current else None
    if saved is None and (
        child is None or child["phase"] == "complete" or attachments.unused_prelude(child)
    ):
        return None
    if saved is not None:
        assert raw is not None
        metadata_only(saved)
        if (
            raw["uid"] != os.getuid()
            or raw["mode"] != 0o600
            or set(saved) != {"schema", "proof", "original", "phase", "settled"}
            or saved["schema"] != SCHEMA
            or saved["phase"] not in {"settling", "retiring", "complete"}
        ):
            raise ReplanRejected("Attachment settlement receipt is invalid")
        journal.validate(saved["original"])
    if current is None:
        raise ReplanRejected("Attachment predecessor disappeared")
    original = saved["original"] if saved else current
    evidence = saved["settled"] if saved and saved["phase"] != "settling" else current
    if saved and saved["phase"] != "settling":
        journal.validate(evidence)
        if current not in (evidence, retired(evidence)):
            raise ReplanRejected("Attachment retirement has a different receipt")
    require_original(parent, original, evidence)
    proposed = Pending(
        command,
        journal,
        parent,
        manager,
        ssh,
        instances,
        local,
        targets,
        refresh_ssh_policy,
        path,
        original,
        current,
        raw,
        saved,
    )
    proposed.observe(evidence, settled=bool(saved and saved["phase"] != "settling"))
    if saved and saved["phase"] == "complete":
        if current != retired(evidence):
            raise ReplanRejected("Completed attachment settlement lost its retirement")
        return None
    return proposed
