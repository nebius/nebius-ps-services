"""New, audited apply cycle after a completed-package release failure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleState,
    VMHALifecycleStatus,
    vm_ha_activation_effect_is_host_only,
    vm_ha_effective_resource_bindings,
    vm_ha_has_unsettled_replacement,
)
from .vm_ha_upgrade_recovery import ARTIFACT, AUTHORITY, READY, release_started

AUTHORITY_KEY = "corrective-release-authority"
CYCLE_KEY = "apply-lock-cycle"
RESERVED_KEY = "corrective-release-reserved"
OBSERVED_EFFECT = "verify-corrective-cloud"


def bind_cloud_observation(journal: VMHALifecycleJournal, observe: Callable[[], dict]) -> None:
    """Bind the approved snapshot through the existing zero-change cloud guard."""
    record = authority(journal.state)
    transaction = journal.state.transaction
    if (
        record is None
        or transaction is None
        or (dict(transaction.resource_bindings).get(RESERVED_KEY) != record["operation"])
    ):
        raise ValueError("corrective cloud binding requires both guarded reservations")
    current = observe()
    if digest(current) != record["cloud"]:
        raise ValueError("corrective approved cloud snapshot changed")
    journal.begin(OBSERVED_EFFECT, observation=current)
    journal.complete(OBSERVED_EFFECT, observation=observe())


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def authority(state: VMHALifecycleState | None) -> dict | None:
    if state is None or state.transaction is None:
        return None
    raw = dict(state.transaction.resource_bindings).get(AUTHORITY_KEY)
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) - {"replan"} != {
        "prior_record",
        "prior_parent",
        "prior_artifact",
        "artifact",
        "owner",
        "hosts",
        "children",
        "locks",
        "operation",
        "ssh_trust",
        "cloud",
        "approval",
    }:
        raise ValueError("corrective release authority is malformed")
    if "replan" in value:
        from .ha_replan import validate_record

        proof = validate_record(value["replan"])
        if (
            proof["desired"] != state.transaction.desired_state_digest
            or proof["artifact"] != value["artifact"]
        ):
            raise ValueError("HA replanned desired state differs from settlement")
    for key in (
        "prior_record",
        "prior_artifact",
        "artifact",
        "operation",
        "ssh_trust",
        "cloud",
        "approval",
        "hosts",
    ):
        if not isinstance(value[key], str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None:
            raise ValueError("corrective release authority digest is malformed")
    if not isinstance(value["prior_parent"], str) or not value["prior_parent"]:
        raise ValueError("corrective release predecessor operation is malformed")
    nodes = {m.node_id for m in state.members}
    if (
        value["owner"] not in nodes
        or set(value["children"]) != nodes
        or set(value["locks"]) != nodes
    ):
        raise ValueError("corrective release member authority differs")
    if (
        value["operation"] != state.transaction.operation_id
        or value["artifact"] != dict(state.transaction.resource_bindings).get(ARTIFACT)
        or value["approval"] != state.transaction.approval_digest
    ):
        raise ValueError("corrective release transaction binding differs")
    return value


def unused_prelude(state: VMHALifecycleState) -> bool:
    """Client frontier only; guest absence and unchanged history need live proof."""
    tx = state.transaction
    record = authority(state)
    return bool(
        tx is not None
        and record is not None
        and RESERVED_KEY not in dict(tx.resource_bindings)
        and not tx.completed_effects
        and tx.pending_effect in {None, *("reserve-corrective-" + m.node_id for m in state.members)}
    )


def activation_started(state: VMHALifecycleState) -> bool:
    """Only exact member activation effects establish the pre-release frontier."""
    tx = state.transaction
    if tx is None:
        return False
    effects = set(tx.completed_effects) | {tx.pending_effect}
    return any("activate-" + member.node_id in effects for member in state.members)


def prepared_recovery(state: VMHALifecycleState) -> bool:
    """A fully installed recovery may become a new immutable successor."""
    from .vm_ha_upgrade_recovery import READY, recovery_authority

    record = recovery_authority(state)
    transaction = state.transaction
    if record is None or transaction is None:
        return False
    resources = dict(transaction.resource_bindings)
    return (
        resources.get(READY) == record["artifact"] == resources.get(ARTIFACT)
        and transaction.pending_effect is None
    )


def validate_checkpoint(state: VMHALifecycleState, *, settlement: dict | None = None) -> None:
    from .vm_ha_upgrade_recovery import unfinished_recovery

    unfinished = unfinished_recovery(state)
    if unfinished is not None:
        from .ha_replan import validate_record

        if settlement is None or "recovery_frontier" not in validate_record(
            settlement, previous=state
        ):
            raise ValueError("Unfinished recovery requires its completed owner settlement")
    tx = state.transaction
    if not (
        state.record_version == 4
        and state.status is VMHALifecycleStatus.ACTIVATING
        and tx is not None
        and len(state.members) == 2
        and tx.accepted_cloud_operation_id is None
        and tx.accepted_cloud_operation_effect is None
        and tx.observation_guard is None
        and tx.replacement_host_recovery is None
        and (
            (release_started(state) or activation_started(state) or prepared_recovery(state))
            and vm_ha_activation_effect_is_host_only(tx.pending_effect)
            or unused_prelude(state)
            or unfinished is not None
        )
    ):
        raise ValueError("corrective release requires a host-only failed release checkpoint")
    bindings = dict(tx.resource_bindings)
    if (
        not re.fullmatch(r"[a-f0-9]{64}", bindings.get(ARTIFACT, ""))
        or "heartbeat-initial-artifact" in bindings
        or vm_ha_has_unsettled_replacement(state)
        or not all(m.compute_id and m.disk_id for m in state.members)
    ):
        raise ValueError("corrective release requires the exact retained pair")


def operation_id(state: VMHALifecycleState, artifact: str) -> str:
    validate_checkpoint(state)
    return digest(
        {
            "domain": "nebius-vpngw/corrective-release-v1",
            "predecessor": state.record_sha256,
            "artifact": artifact,
        }
    )


def completed_children(value: dict, *, parent: str, artifact: str) -> None:
    if not (
        set(value) == {"runtime", "ordinary", "conversion"}
        and value["runtime"]["phase"] in {"installed", "activating", "complete"}
        and value["runtime"]["parent"] == parent
        and value["runtime"]["artifact"] == artifact
        and value["ordinary"]["state"] == "complete"
        and value["ordinary"]["id"] == value["runtime"]["operation"]
        and (value["conversion"] is None or value["conversion"]["phase"] == "completed")
    ):
        raise ValueError("corrective release requires completed exact predecessor packages")


def successor(
    previous: VMHALifecycleState, record: dict, *, bindings: dict[str, str]
) -> VMHALifecycleState:
    validate_checkpoint(previous, settlement=record.get("replan"))
    tx = previous.transaction
    assert tx is not None
    resources = dict(tx.resource_bindings)
    for key in (
        ARTIFACT,
        AUTHORITY,
        READY,
        AUTHORITY_KEY,
        RESERVED_KEY,
        "runtime-command-approval",
        "runtime-command-execution",
        "runtime-peer-preparation",
        "runtime-peer-continuation",
        "runtime-peer-continuation-command",
        "runtime-package-manifests",
    ):
        resources.pop(key, None)
    resources.update(bindings)
    resources.update(
        {
            ARTIFACT: record["artifact"],
            CYCLE_KEY: record["operation"],
            AUTHORITY_KEY: json.dumps(record, sort_keys=True),
        }
    )
    result = replace(
        previous,
        transaction=replace(
            tx,
            operation_id=record["operation"],
            approval_digest=record["approval"],
            current_state_digest=record["cloud"],
            desired_state_digest=record["replan"]["proof"]["desired"]
            if "replan" in record
            else tx.desired_state_digest,
            checkpoint="corrective-release-approved",
            pending_effect=None,
            completed_effects=(),
            revision=1,
            predecessor_sha256=previous.record_sha256,
            observation=(),
            resource_bindings=tuple(sorted(resources.items())),
        ),
    )
    validate_successor(previous, result)
    return result


def validate_successor(previous: VMHALifecycleState, current: VMHALifecycleState) -> bool:
    tx, new = previous.transaction, current.transaction
    if tx is None or new is None or new.operation_id == tx.operation_id:
        return False
    if AUTHORITY_KEY not in dict(new.resource_bindings):
        return False
    record = authority(current)
    assert record is not None
    validate_checkpoint(previous, settlement=record.get("replan"))
    replan = record.get("replan")
    if replan is not None:
        from .ha_replan import successor_operation, validate_record

        proof = validate_record(replan, previous=previous)
        if (
            proof["cloud"] != record["cloud"]
            or proof["hosts"] != record["hosts"]
            or proof["ssh_trust"] != record["ssh_trust"]
        ):
            raise ValueError("HA successor settlement observation differs")
        expected_operation = successor_operation(previous, replan)
        expected_desired = proof["desired"]
    else:
        expected_operation = operation_id(previous, record["artifact"])
        expected_desired = tx.desired_state_digest
    if not (
        current.status is previous.status
        and current.has_same_identity(previous)
        and new.revision == 1
        and new.pending_effect is None
        and not new.completed_effects
        and new.checkpoint == "corrective-release-approved"
        and new.desired_state_digest == expected_desired
        and new.current_state_digest == record["cloud"]
        and new.predecessor_sha256 == previous.record_sha256
        and new.approval_kind == tx.approval_kind
        and new.accepted_cloud_operation_id is None
        and new.observation_guard is None
        and record["prior_record"] == previous.record_sha256
        and record["prior_parent"] == tx.operation_id
        and record["prior_artifact"] == dict(tx.resource_bindings)[ARTIFACT]
        and (
            record["artifact"] != record["prior_artifact"]
            or replan is not None
            and expected_desired != tx.desired_state_digest
        )
        and record["operation"] == expected_operation
        and dict(new.resource_bindings)[CYCLE_KEY] == new.operation_id
        and vm_ha_effective_resource_bindings(dict(tx.resource_bindings))
        == vm_ha_effective_resource_bindings(dict(new.resource_bindings))
    ):
        raise ValueError("corrective release cannot rebind identity or rewrite prior effects")
    retained = authority(previous) if unused_prelude(previous) else None
    if retained is not None and (
        record["children"] != retained["children"]
        or record["locks"] != retained["locks"]
        or record["hosts"] != retained["hosts"]
    ):
        raise ValueError("unused corrective prelude changed original guest authority")
    for node, item in record["children"].items():
        if replan is not None and (
            replan["requests"][node]["previous_lock"] != record["locks"][node]
            or replan["requests"][node]["children"] != item
        ):
            raise ValueError("HA replanned child or lock differs from settlement")
        baseline = None if retained is None else retained["children"][node]["runtime"]
        expected_artifact = record["prior_artifact"] if baseline is None else baseline["artifact"]
        if replan is not None and "recovery_frontier" in proof:
            from ..apply_approval import manifest_identity
            from .ha_replan import recovery_member_manifest

            member = next(member for member in previous.members if member.node_id == node)
            manifest = recovery_member_manifest(
                previous,
                member.instance_name,
                item,
                replan["requests"][node]["package_reservation"],
                json.loads(dict(tx.resource_bindings)["runtime-package-manifests"]),
            )
            if manifest_identity(manifest) != proof["recovery_frontier"]["manifests"][node]:
                raise ValueError("HA successor predecessor package manifest differs")
            expected_artifact = manifest["wheel_sha256"]
        completed_children(
            item,
            parent=tx.operation_id if baseline is None else baseline["parent"],
            artifact=expected_artifact,
        )
    bindings = dict(tx.resource_bindings)
    owner = next(m for m in previous.members if m.node_id == record["owner"])
    if owner.compute_id != bindings.get(
        "shared-allocation-owner-compute"
    ) or owner.network_interface_name != bindings.get("shared-allocation-owner-nic"):
        raise ValueError("corrective release owner differs from retained allocation authority")
    return True


def archive(previous: VMHALifecycleState, path: Path) -> None:
    """Content-addressed history, durable before canonical lifecycle replacement."""
    directory = path.parent / (path.name + ".history")
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("corrective lifecycle archive directory is unsafe")
    payload = json.dumps(previous.to_dict(), sort_keys=True, separators=(",", ":")).encode() + b"\n"
    target = directory / (previous.record_sha256 + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".corrective-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                    or os.read(fd, len(payload) + 1) != payload
                ):
                    raise ValueError("corrective lifecycle history differs")
            finally:
                os.close(fd)
    finally:
        os.unlink(temporary)
    for parent in (directory, path.parent):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
