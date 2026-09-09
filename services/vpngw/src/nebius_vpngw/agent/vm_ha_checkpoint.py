"""Canonical checkpoint codec, usable before importing installed runtime code."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .vm_ha.models import DigestSet
from .vm_ha_controller import (
    ActionKind,
    ControllerAction,
    ControllerCheckpoint,
    HAState,
    OwnershipContext,
    RepairAttempt,
    TransferContinuity,
)

_CHECKPOINT_SCHEMA_V1 = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
_CHECKPOINT_SCHEMA_V2 = "nebius-vpngw/vm-ha-controller-checkpoint-v2"
_CHECKPOINT_SCHEMA_V3 = "nebius-vpngw/vm-ha-controller-checkpoint-v3"
_CHECKPOINT_SCHEMA = "nebius-vpngw/vm-ha-controller-checkpoint-v4"


def _digests_from_dict(payload: object) -> DigestSet:
    if not isinstance(payload, dict) or set(payload) != {
        "configuration",
        "static_routes",
        "bgp_policy",
    }:
        raise ValueError("checkpoint digests have an invalid shape")
    return DigestSet(
        configuration=str(payload["configuration"]),
        static_routes=str(payload["static_routes"]),
        bgp_policy=str(payload["bgp_policy"]),
    )


def controller_checkpoint_to_dict(checkpoint: ControllerCheckpoint) -> dict[str, Any]:
    """Serialize every recovery-relevant controller and pending-action identity."""

    pending: dict[str, Any] | None = None
    if checkpoint.pending_action is not None:
        action = checkpoint.pending_action
        pending = {
            "kind": action.kind.value,
            "operation_id": action.operation_id,
            "boot_id": action.boot_id,
            "target_node_id": action.target_node_id,
            "allocation_id": action.allocation_id,
            "ownership_epoch": action.ownership_epoch,
            "generation_id": action.generation_id,
            "digests": action.digests.to_dict(),
            "ownership_incarnation": action.ownership_incarnation,
            "takeover_fence_required": action.takeover_fence_required,
            "repair_deadline_at": action.repair_deadline_at,
            "repair_reasons": list(action.repair_reasons),
        }
    established = (
        asdict(checkpoint.established_ownership_context)
        if checkpoint.established_ownership_context is not None
        else None
    )
    continuity = checkpoint.transfer_continuity
    continuity_payload = (
        None
        if continuity is None
        else {
            "allocation_id": continuity.allocation_id,
            "attach_operation_id": continuity.attach_operation_id,
            "candidate_node_id": continuity.candidate_node_id,
            "digests": continuity.digests.to_dict(),
            "former_owner_node_id": continuity.former_owner_node_id,
            "generation_id": continuity.generation_id,
            "ownership_confirmed": continuity.ownership_confirmed,
            "ownership_incarnation": continuity.ownership_incarnation,
            "post_attach_revision": continuity.post_attach_revision,
            "pre_attach_revision": continuity.pre_attach_revision,
        }
    )
    repair = checkpoint.repair_attempt
    repair_payload = (
        None
        if repair is None
        else {
            "allocation_id": repair.allocation_id,
            "boot_id": repair.boot_id,
            "deadline_at": repair.deadline_at,
            "failure_fingerprint": list(repair.failure_fingerprint),
            "generation_id": repair.generation_id,
            "healthy_observations": repair.healthy_observations,
            "healthy_since": repair.healthy_since,
            "operation_id": repair.operation_id,
            "owner_node_id": repair.owner_node_id,
            "ownership_epoch": repair.ownership_epoch,
            "ownership_incarnation": repair.ownership_incarnation,
            "started_at": repair.started_at,
        }
    )
    return {
        "schema": _CHECKPOINT_SCHEMA,
        "sequence": checkpoint.sequence,
        "state": checkpoint.state.value,
        "suspect_since": checkpoint.suspect_since,
        "pending_action": pending,
        "established_ownership_context": established,
        "ownership_continuity_invalidated": checkpoint.ownership_continuity_invalidated,
        "ownership_incarnation": checkpoint.ownership_incarnation,
        "transfer_continuity": continuity_payload,
        "repair_attempt": repair_payload,
    }


def controller_checkpoint_from_dict(payload: object) -> ControllerCheckpoint:
    """Restore the exact checkpoint; malformed or partial state always fails closed."""

    common = {
        "schema",
        "sequence",
        "state",
        "suspect_since",
        "pending_action",
        "established_ownership_context",
        "ownership_continuity_invalidated",
        "ownership_incarnation",
    }
    if not isinstance(payload, dict):
        raise ValueError("controller checkpoint has an invalid shape")
    schema = payload.get("schema")
    expected = common
    if schema in {_CHECKPOINT_SCHEMA_V2, _CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
        expected |= {"transfer_continuity"}
    if schema == _CHECKPOINT_SCHEMA:
        expected |= {"repair_attempt"}
    if set(payload) != expected:
        raise ValueError("controller checkpoint has an invalid shape")
    if schema not in {
        _CHECKPOINT_SCHEMA_V1,
        _CHECKPOINT_SCHEMA_V2,
        _CHECKPOINT_SCHEMA_V3,
        _CHECKPOINT_SCHEMA,
    }:
        raise ValueError("unsupported controller checkpoint schema")

    pending_payload = payload["pending_action"]
    pending: ControllerAction | None = None
    if pending_payload is not None:
        pending_expected = {
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
        if schema in {_CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
            pending_expected.add("takeover_fence_required")
        if schema == _CHECKPOINT_SCHEMA:
            pending_expected.update({"repair_deadline_at", "repair_reasons"})
        if not isinstance(pending_payload, dict) or set(pending_payload) != pending_expected:
            raise ValueError("pending controller action has an invalid shape")
        takeover_fence_required = pending_payload.get("takeover_fence_required")
        if schema in {_CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
            if not isinstance(takeover_fence_required, bool):
                raise ValueError("pending takeover fence requirement must be boolean")
        else:
            # Legacy checkpoints did not distinguish a historical ownership
            # incarnation from an in-flight takeover. Preserve their stricter
            # behavior so upgrades never weaken a checkpointed promotion.
            takeover_fence_required = int(pending_payload["ownership_incarnation"]) > 0
        repair_deadline_at = pending_payload.get("repair_deadline_at")
        if repair_deadline_at is not None:
            if isinstance(repair_deadline_at, bool) or not isinstance(
                repair_deadline_at, (int, float)
            ):
                raise ValueError("pending repair deadline must be numeric")
            repair_deadline_at = float(repair_deadline_at)
        repair_reasons_payload = pending_payload.get("repair_reasons", [])
        if not isinstance(repair_reasons_payload, list) or not all(
            isinstance(reason, str) and reason for reason in repair_reasons_payload
        ):
            raise ValueError("pending repair reasons have an invalid shape")
        pending = ControllerAction(
            kind=ActionKind(str(pending_payload["kind"])),
            operation_id=str(pending_payload["operation_id"]),
            boot_id=str(pending_payload["boot_id"]),
            target_node_id=str(pending_payload["target_node_id"]),
            allocation_id=str(pending_payload["allocation_id"]),
            ownership_epoch=str(pending_payload["ownership_epoch"]),
            generation_id=str(pending_payload["generation_id"]),
            digests=_digests_from_dict(pending_payload["digests"]),
            ownership_incarnation=int(pending_payload["ownership_incarnation"]),
            takeover_fence_required=takeover_fence_required,
            repair_deadline_at=repair_deadline_at,
            repair_reasons=tuple(repair_reasons_payload),
        )

    ownership_payload = payload["established_ownership_context"]
    ownership: OwnershipContext | None = None
    if ownership_payload is not None:
        if not isinstance(ownership_payload, dict) or set(ownership_payload) != {
            "owner_node_id",
            "allocation_id",
            "ownership_epoch",
        }:
            raise ValueError("established ownership context has an invalid shape")
        ownership = OwnershipContext(
            owner_node_id=str(ownership_payload["owner_node_id"]),
            allocation_id=str(ownership_payload["allocation_id"]),
            ownership_epoch=str(ownership_payload["ownership_epoch"]),
        )

    continuity: TransferContinuity | None = None
    if schema in {_CHECKPOINT_SCHEMA_V2, _CHECKPOINT_SCHEMA_V3, _CHECKPOINT_SCHEMA}:
        continuity_payload = payload["transfer_continuity"]
        if continuity_payload is not None:
            continuity_expected = {
                "allocation_id",
                "attach_operation_id",
                "candidate_node_id",
                "digests",
                "former_owner_node_id",
                "generation_id",
                "ownership_confirmed",
                "ownership_incarnation",
                "post_attach_revision",
                "pre_attach_revision",
            }
            if (
                not isinstance(continuity_payload, dict)
                or set(continuity_payload) != continuity_expected
            ):
                raise ValueError("transfer continuity has an invalid shape")
            if not isinstance(continuity_payload["ownership_confirmed"], bool):
                raise ValueError("transfer continuity ownership_confirmed must be boolean")
            continuity = TransferContinuity(
                attach_operation_id=str(continuity_payload["attach_operation_id"]),
                allocation_id=str(continuity_payload["allocation_id"]),
                former_owner_node_id=str(continuity_payload["former_owner_node_id"]),
                candidate_node_id=str(continuity_payload["candidate_node_id"]),
                generation_id=str(continuity_payload["generation_id"]),
                digests=_digests_from_dict(continuity_payload["digests"]),
                ownership_incarnation=int(continuity_payload["ownership_incarnation"]),
                pre_attach_revision=str(continuity_payload["pre_attach_revision"]),
                post_attach_revision=(
                    None
                    if continuity_payload["post_attach_revision"] is None
                    else str(continuity_payload["post_attach_revision"])
                ),
                ownership_confirmed=bool(continuity_payload["ownership_confirmed"]),
            )

    repair_attempt: RepairAttempt | None = None
    if schema == _CHECKPOINT_SCHEMA:
        repair_payload = payload["repair_attempt"]
        repair_expected = {
            "allocation_id",
            "boot_id",
            "deadline_at",
            "failure_fingerprint",
            "generation_id",
            "healthy_observations",
            "healthy_since",
            "operation_id",
            "owner_node_id",
            "ownership_epoch",
            "ownership_incarnation",
            "started_at",
        }
        if repair_payload is not None:
            if not isinstance(repair_payload, dict) or set(repair_payload) != repair_expected:
                raise ValueError("repair attempt has an invalid shape")
            fingerprint = repair_payload["failure_fingerprint"]
            if not isinstance(fingerprint, list) or not all(
                isinstance(reason, str) and reason for reason in fingerprint
            ):
                raise ValueError("repair attempt fingerprint has an invalid shape")
            repair_attempt = RepairAttempt(
                operation_id=str(repair_payload["operation_id"]),
                owner_node_id=str(repair_payload["owner_node_id"]),
                allocation_id=str(repair_payload["allocation_id"]),
                ownership_epoch=str(repair_payload["ownership_epoch"]),
                ownership_incarnation=int(repair_payload["ownership_incarnation"]),
                generation_id=str(repair_payload["generation_id"]),
                boot_id=str(repair_payload["boot_id"]),
                failure_fingerprint=tuple(fingerprint),
                started_at=float(repair_payload["started_at"]),
                deadline_at=float(repair_payload["deadline_at"]),
                healthy_since=(
                    None
                    if repair_payload["healthy_since"] is None
                    else float(repair_payload["healthy_since"])
                ),
                healthy_observations=int(repair_payload["healthy_observations"]),
            )

    if not isinstance(payload["ownership_continuity_invalidated"], bool):
        raise ValueError("controller checkpoint invalidation flag must be boolean")
    if isinstance(payload["ownership_incarnation"], bool) or not isinstance(
        payload["ownership_incarnation"], int
    ):
        raise ValueError("controller checkpoint ownership incarnation must be an integer")
    invalidated = payload["ownership_continuity_invalidated"]
    incarnation = payload["ownership_incarnation"]
    state = HAState(str(payload["state"]))
    if schema == _CHECKPOINT_SCHEMA_V1:
        if pending is not None and pending.kind is ActionKind.ATTACH_CANDIDATE:
            continuity = TransferContinuity(
                attach_operation_id=pending.operation_id,
                allocation_id=pending.allocation_id,
                former_owner_node_id=(
                    str(ownership.owner_node_id) if ownership is not None else "legacy-former-owner"
                ),
                candidate_node_id=pending.target_node_id,
                generation_id=pending.generation_id,
                digests=pending.digests,
                ownership_incarnation=pending.ownership_incarnation,
                pre_attach_revision=pending.ownership_epoch,
            )
        elif (
            pending is not None
            and pending.kind
            in {
                ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
                ActionKind.PREPARE_CANDIDATE_DATAPLANE,
                ActionKind.RECONCILE_ROUTES,
                ActionKind.ENABLE_ACTIVE,
            }
        ) or (pending is None and state in {HAState.OWNERSHIP_TRANSFER, HAState.PROMOTING}):
            was_invalidated = invalidated
            pending = None
            ownership = None
            continuity = None
            invalidated = True
            incarnation = max(1, incarnation + (0 if was_invalidated else 1))
            state = HAState.BLOCKED

    return ControllerCheckpoint(
        sequence=int(payload["sequence"]),
        state=state,
        suspect_since=(
            None if payload["suspect_since"] is None else float(payload["suspect_since"])
        ),
        pending_action=pending,
        established_ownership_context=ownership,
        ownership_continuity_invalidated=invalidated,
        ownership_incarnation=incarnation,
        transfer_continuity=continuity,
        repair_attempt=repair_attempt,
    )
