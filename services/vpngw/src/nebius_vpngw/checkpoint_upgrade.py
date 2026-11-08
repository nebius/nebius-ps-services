"""Deployment-only lossless checkpoint conversion; runtime accepts v7 only.

This module may inspect predecessors before importing the installed agent. Its
publisher requires caller-owned HA writer locks and exact package verification.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .agent.vm_ha_checkpoint import (
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
    validate_conversion_receipt,
)
from .agent.vm_ha_controller import ControllerCheckpoint

SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v7"
PREFIX = "nebius-vpngw/vm-ha-controller-checkpoint-v"
RECEIPT = "nebius-vpngw/checkpoint-conversion-v1"


def _completed_predecessor(receipt: dict, payload: dict, artifact: str) -> bool:
    """A completed older release is distinct from rollback of this conversion."""
    return (
        receipt.get("phase") == "completed"
        and receipt.get("artifact") != artifact
        and payload.get("schema") != SCHEMA
    )


def inspect_checkpoint(payload: object) -> ControllerCheckpoint:
    """Validate supported predecessor shape without discarding recovery state."""
    if not isinstance(payload, dict):
        raise ValueError("controller checkpoint has an invalid shape")
    if payload.get("schema") == SCHEMA:
        return controller_checkpoint_from_dict(payload)
    versions = {PREFIX + str(version): version for version in range(1, 7)}
    version = versions.get(str(payload.get("schema")))
    if version is None:
        raise ValueError("unsupported predecessor checkpoint schema")
    expected = {
        "schema",
        "sequence",
        "state",
        "suspect_since",
        "pending_action",
        "established_ownership_context",
        "ownership_continuity_invalidated",
        "ownership_incarnation",
    }
    if version >= 2:
        expected.add("transfer_continuity")
    if version >= 4:
        expected.add("repair_attempt")
    if version >= 5:
        expected.add("suspicion_boot_id")
    if version >= 6:
        expected.add("path_transfer_destinations")
    if set(payload) != expected:
        raise ValueError("predecessor checkpoint has an invalid shape")
    suspicion = payload["suspect_since"]
    if suspicion is not None and (
        isinstance(suspicion, bool)
        or not isinstance(suspicion, (int, float))
        or not math.isfinite(suspicion)
        or suspicion < 0
    ):
        raise ValueError("predecessor suspicion is invalid")
    value = copy.deepcopy(payload)
    pending = value["pending_action"]
    if pending is not None:
        fields = {
            "kind",
            "operation_id",
            "boot_id",
            "target_node_id",
            "allocation_id",
            "ownership_epoch",
            "generation_id",
            "digests",
            "ownership_incarnation",
        }
        if version >= 3:
            fields.add("takeover_fence_required")
        if version >= 4:
            fields.update({"repair_deadline_at", "repair_reasons"})
        if not isinstance(pending, dict) or set(pending) != fields:
            raise ValueError("predecessor pending action has an invalid shape")
        if version == 1 and pending["kind"] in {
            "attach-candidate",
            "confirm-candidate-ownership",
            "prepare-candidate-dataplane",
            "reconcile-routes",
            "enable-active",
        }:
            raise ValueError("predecessor requires lossy recovery; conversion refused")
        if version < 3:
            incarnation = pending["ownership_incarnation"]
            if type(incarnation) is not int or incarnation < 0:
                raise ValueError("predecessor ownership incarnation is invalid")
            pending["takeover_fence_required"] = incarnation > 0
        if version < 4:
            pending.update(repair_deadline_at=None, repair_reasons=[])
        pending["repair_mode"] = "active"
    if version == 1 and value["state"] in {"ownership-transfer", "promoting"}:
        raise ValueError("predecessor lacks transfer continuity; conversion refused")
    value.setdefault("transfer_continuity", None)
    value.setdefault("repair_attempt", None)
    if value["repair_attempt"] is not None:
        # Predecessor repair was restricted to active owners. Preserve every
        # existing budget field and make that authority explicit for v7.
        if (
            not isinstance(value["repair_attempt"], dict)
            or "repair_mode" in value["repair_attempt"]
        ):
            raise ValueError("predecessor repair attempt has an invalid shape")
        value["repair_attempt"]["repair_mode"] = "active"
    value["schema"] = SCHEMA
    value.setdefault("path_transfer_destinations", [])
    if version < 5:
        value.update(suspect_since=None, suspicion_boot_id=None)
    checkpoint = controller_checkpoint_from_dict(value)
    # Decoder coercion must not silently change any predecessor recovery field.
    if json.dumps(controller_checkpoint_to_dict(checkpoint), sort_keys=True) != json.dumps(
        value, sort_keys=True
    ):
        raise ValueError("predecessor cannot be represented losslessly")
    return checkpoint


def conversion_preflight(
    *,
    expected: dict[str, Any] | None,
    artifact: str,
    state_dir: Path,
    boot_id: str,
    repair: Any,
    unstarted: bool = False,
) -> bool:
    """Read-only package admission; True requires guarded conversion after install."""
    path = state_dir / "controller-checkpoint.json"
    if not path.exists() and not path.is_symlink():
        if unstarted and not (state_dir / "status.json").exists():
            return False
        raise RuntimeError("HA checkpoint missing before package preparation")
    payload = repair.read_json(path)
    inspect_checkpoint(payload)
    receipt_path = state_dir / "checkpoint-conversion.json"
    receipt = repair.read_json(receipt_path) if receipt_path.exists() else None
    if receipt is not None:
        validate_conversion_receipt(receipt)
    if receipt is not None and _completed_predecessor(receipt, payload, artifact):
        # The supported predecessor reader above has validated the older
        # checkpoint. This new release still needs the current lock/boot fence.
        receipt = None
    elif receipt is not None and receipt["phase"] == "completed":
        controller_checkpoint_from_dict(payload)
        return False
    if receipt is None and payload.get("schema") == SCHEMA:
        return False
    if expected is None:
        raise RuntimeError("legacy or pending checkpoint requires coordinated apply")
    lock = repair.exact_lock(expected, state_dir)
    guard = repair.read_json(state_dir / "guard.json")
    if (
        repair.BOOT.read_text().strip() != boot_id
        or guard.get("schema") != repair.STATUS_SCHEMA
        or guard.get("guard_boot_id") != boot_id
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or repair.FORWARDING.read_text().strip() != "0"
    ):
        raise RuntimeError("checkpoint conversion forwarding exclusion is unproven")
    if receipt is not None:
        binding = {
            "schema": RECEIPT,
            "cluster_id": lock["cluster_id"],
            "node_id": lock["node_id"],
            "generation_id": lock["generation_id"],
            "operation_id": lock["operation_id"],
            "host_id": expected["host_id"],
            "boot_id": boot_id,
            "artifact": artifact,
        }
        if any(receipt[key] != value for key, value in binding.items()):
            raise RuntimeError("checkpoint conversion binding changed; roll forward exact artifact")
        raw = repair.verified_file(path, repair.sha(path.read_bytes()))
        current = repair.sha(raw)
        if current not in {receipt["before"], receipt["after"]}:
            raise RuntimeError("checkpoint conversion checkpoint hash changed")
        if current == receipt["after"]:
            controller_checkpoint_from_dict(payload)
        else:
            candidate = (
                json.dumps(
                    controller_checkpoint_to_dict(inspect_checkpoint(payload)),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            if repair.sha(candidate) != receipt["after"]:
                raise RuntimeError("checkpoint conversion candidate hash changed")
    return True


def admit_activation(
    *,
    expected: dict[str, Any],
    artifact: str,
    state_dir: Path,
    boot_id: str,
    repair: Any,
    unstarted: bool,
    marker: Path = Path("/etc/nebius-vpngw/vm-ha-enabled"),
) -> None:
    """Seed only a coordinator-proven fresh host before publishing its HA marker."""
    import time

    from nebius_vpngw import ordinary_operations as ops

    path = state_dir / "controller-checkpoint.json"
    if path.exists() or path.is_symlink():
        conversion_preflight(
            expected=expected,
            artifact=artifact,
            state_dir=state_dir,
            boot_id=boot_id,
            repair=repair,
        )
        controller_checkpoint_from_dict(repair.read_json(path))
        if marker.exists() or marker.is_symlink():
            return

    def require_first_start() -> None:
        repair.exact_lock(expected, state_dir)
        if (
            not unstarted
            or marker.exists()
            or marker.is_symlink()
            or (state_dir / "status.json").exists()
            or repair.BOOT.read_text().strip() != boot_id
        ):
            raise RuntimeError("HA initial checkpoint lacks first-start authority")

    require_first_start()
    # A reboot after config publication can start the enabled ordinary agent
    # before the HA marker exists. Stop only management writers on this exact
    # never-activated Compute; an active HA controller is not first-start state.
    manager = ops.Manager(time.monotonic() + 35)
    if not all(manager.stopped(unit) for unit in ops.HA_WRITERS):
        raise RuntimeError("HA initial checkpoint writers are active")
    if not all(manager.stopped(unit) for unit in ops.MANAGEMENT):
        require_first_start()
        manager.command(
            ["systemctl", "--job-mode=replace-irreversibly", "stop", *ops.MANAGEMENT],
            limit=20,
        )
    # Stop jobs may need the routing lock. Acquire writer locks only after
    # quiescence, then reprove authority and stopped processes under those locks.
    with repair.writer_locks(state_dir=state_dir, deadline=time.monotonic() + 5):
        require_first_start()
        manager = ops.Manager(time.monotonic() + 15)
        if not all(manager.stopped(unit) for unit in (*ops.MANAGEMENT, *ops.HA_WRITERS)):
            raise RuntimeError("HA initial checkpoint writers are active")
        # The service has a marker condition, so its first cold guard must run
        # directly while startup is still excluded and all writer locks are held.
        repair.cold_guard(state_dir=state_dir, boot_id=boot_id)
        repair.exact_lock(expected, state_dir)
        repair.initialize_unstarted_checkpoint(state_dir=state_dir, unstarted=True)
        controller_checkpoint_from_dict(repair.read_json(path))


def convert_checkpoint(
    *,
    expected: dict[str, Any],
    artifact: str,
    state_dir: Path,
    boot_id: str,
    repair: Any,
    unstarted: bool = False,
) -> dict[str, Any]:
    """Publish once under verified quiescence; exact receipt supports crash replay.

    Caller holds repair.writer_locks, has verified the approved installed artifact
    and proven management/controller writers stopped. We independently verify the
    current apply lock and boot forwarding guard before each publication.
    """
    if len(artifact) != 64 or any(c not in "0123456789abcdef" for c in artifact):
        raise ValueError("checkpoint conversion artifact is invalid")
    path = state_dir / "controller-checkpoint.json"
    receipt_path = state_dir / "checkpoint-conversion.json"

    def guard() -> dict[str, Any]:
        lock = repair.exact_lock(expected, state_dir)
        evidence = repair.read_json(state_dir / "guard.json")
        if (
            repair.BOOT.read_text().strip() != boot_id
            or evidence.get("schema") != repair.STATUS_SCHEMA
            or evidence.get("guard_boot_id") != boot_id
            or evidence.get("data_plane_mode") not in {"blocked", "passive"}
            or repair.FORWARDING.read_text().strip() != "0"
        ):
            raise RuntimeError("checkpoint conversion forwarding exclusion is unproven")
        return lock

    lock = guard()
    binding = {
        "schema": RECEIPT,
        "cluster_id": lock["cluster_id"],
        "node_id": lock["node_id"],
        "generation_id": lock["generation_id"],
        "operation_id": lock["operation_id"],
        "host_id": expected["host_id"],
        "boot_id": boot_id,
        "artifact": artifact,
    }
    if not path.exists() and not path.is_symlink():
        repair.initialize_unstarted_checkpoint(state_dir=state_dir, unstarted=unstarted)
    payload = repair.read_json(path)
    # The no-follow identity reread ties the raw hash to the validated record.
    raw = repair.verified_file(path, hashlib.sha256(path.read_bytes()).hexdigest())
    if json.loads(raw) != payload:
        raise RuntimeError("checkpoint changed during conversion")
    current_hash = hashlib.sha256(raw).hexdigest()
    receipt = repair.read_json(receipt_path) if receipt_path.exists() else None
    if receipt is not None:
        validate_conversion_receipt(receipt)
    prior_completed = None
    if receipt is not None and _completed_predecessor(receipt, payload, artifact):
        prior_completed = repair.verified_file(receipt_path, repair.sha(receipt_path.read_bytes()))
        receipt = None
    elif receipt is not None and receipt.get("phase") == "completed":
        controller_checkpoint_from_dict(payload)
        return receipt
    if receipt is not None:
        if (
            receipt.get("phase") != "pending"
            or set(receipt) != set(binding) | {"before", "after", "phase"}
            or any(receipt.get(key) != value for key, value in binding.items())
        ):
            raise RuntimeError("checkpoint conversion binding changed; roll forward exact artifact")
        if current_hash == receipt["after"]:
            controller_checkpoint_from_dict(payload)
            return receipt
        if current_hash != receipt["before"]:
            raise RuntimeError("checkpoint conversion checkpoint hash changed")
    elif payload.get("schema") == SCHEMA:
        controller_checkpoint_from_dict(payload)
        return {"schema": RECEIPT, **binding, "before": current_hash, "after": current_hash}
    candidate = (
        json.dumps(
            controller_checkpoint_to_dict(inspect_checkpoint(payload)),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    candidate_hash = hashlib.sha256(candidate).hexdigest()
    proposed = {**binding, "before": current_hash, "after": candidate_hash, "phase": "pending"}
    if receipt is not None and receipt != proposed:
        raise RuntimeError("checkpoint conversion candidate changed")
    guard()
    if prior_completed is not None:
        # Keep the completed prior release as immutable audit evidence before
        # publishing the next canonical conversion receipt.
        prior_hash = repair.sha(prior_completed)
        archive = state_dir / f"checkpoint-conversion-{prior_hash}.completed.json"
        if archive.exists():
            repair.verified_file(archive, prior_hash)
        else:
            repair.publish(archive, prior_completed)
        repair.verified_file(receipt_path, prior_hash)
    repair.publish(receipt_path, json.dumps(proposed, sort_keys=True).encode() + b"\n")
    guard()
    repair.verified_file(path, current_hash)
    repair.publish(path, candidate)
    repair.verified_file(path, candidate_hash)
    return proposed
