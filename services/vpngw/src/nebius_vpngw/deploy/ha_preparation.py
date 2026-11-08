"""Durable successful peer preparation and separately approved continuation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from ..apply_approval import bind, digest, resume
from .vm_ha_lifecycle import VMHALifecycleJournal, VMHALifecycleState, VMHALifecycleStatus

PREPARED = "runtime-peer-preparation"
CONTINUATION = "runtime-peer-continuation"
COMMAND = "runtime-peer-continuation-command"


def _identity(state: VMHALifecycleState) -> dict:
    if state.status is not VMHALifecycleStatus.ACTIVATING or state.transaction is None:
        raise RuntimeError("Peer preparation requires its activating lifecycle")
    return {
        "operation": state.transaction.operation_id,
        "desired": state.transaction.desired_state_digest,
        "members": digest([member.to_dict() for member in state.members]),
    }


def publish(
    journal: VMHALifecycleJournal,
    *,
    artifact: str,
    preparation_scope: dict,
    continuation_scope: dict,
    statuses: dict[str, dict],
    lock_operation: str,
) -> None:
    state = journal.state
    identity = _identity(state)
    if set(statuses) != {member.node_id for member in state.members}:
        raise RuntimeError("Peer preparation needs evidence from both exact members")
    verify_statuses(state, statuses, lock_operation=lock_operation)
    value = {
        "schema": "nebius-vpngw.peer-preparation.v1",
        **identity,
        "artifact": artifact,
        "lock_operation": lock_operation,
        "preparation_scope": digest(preparation_scope),
        "continuation_scope": digest(continuation_scope),
    }
    assert state.transaction is not None
    existing = dict(state.transaction.resource_bindings).get(PREPARED)
    if existing is not None:
        if json.loads(existing) != value:
            raise RuntimeError("Completed peer preparation changed its authority")
        return
    effect = "verify-peer-rotation-prepared"
    journal.begin(effect)
    journal.complete(effect, resource_updates={PREPARED: json.dumps(value, sort_keys=True)})


def verify_statuses(
    state: VMHALifecycleState, statuses: dict[str, dict], *, lock_operation: str
) -> None:
    """Fresh proof for both initial publication and mutation-free repeats."""
    _identity(state)
    if set(statuses) != {member.node_id for member in state.members}:
        raise RuntimeError("Peer preparation needs evidence from both exact members")
    for node, status in statuses.items():
        projection = status.get("apply_convergence") or {}
        if (
            status.get("node_id") != node
            or status.get("apply_locked") is not True
            or status.get("apply_operation_id") != lock_operation
            or status.get("data_plane_mode") != "passive"
            or status.get("promotion_ready") is not False
            or status.get("pending_operation_id") is not None
            or projection.get("fresh") is not True
            or projection.get("data_plane_mode") != "passive"
            or not isinstance(projection.get("boot_id"), str)
            or not projection["boot_id"]
            or projection["boot_id"] != status.get("guard_boot_id")
            or (status.get("heartbeat") or {}).get("peer_v6_fresh") is not True
        ):
            raise RuntimeError("Peer preparation lacks fresh current-boot passive guard proof")


def receipt(state: VMHALifecycleState) -> dict | None:
    if state.transaction is None:
        return None
    bindings = dict(state.transaction.resource_bindings)
    raw = bindings.get(PREPARED)
    if raw is None:
        return None
    prepared = json.loads(raw)
    identity = _identity(state)
    if (
        set(prepared)
        != {
            "schema",
            "operation",
            "desired",
            "members",
            "artifact",
            "preparation_scope",
            "continuation_scope",
            "lock_operation",
        }
        or prepared["schema"] != "nebius-vpngw.peer-preparation.v1"
        or any(prepared.get(key) != value for key, value in identity.items())
        or "verify-peer-rotation-prepared" not in state.transaction.completed_effects
    ):
        raise RuntimeError("Peer continuation differs from successful prepared intent")
    return prepared


def continuation(
    state: VMHALifecycleState, *, artifact: str, scope: dict
) -> tuple[dict, bool] | None:
    prepared = receipt(state)
    if prepared is None:
        return None
    if prepared["artifact"] != artifact or prepared["continuation_scope"] != digest(scope):
        raise RuntimeError("Peer continuation differs from successful prepared intent")
    assert state.transaction is not None
    bindings = dict(state.transaction.resource_bindings)
    record = bind(digest({"prepared": prepared, "scope": digest(scope)}), scope)
    existing = bindings.get(CONTINUATION)
    if existing is not None and json.loads(existing) != record:
        raise RuntimeError("Peer continuation authority changed")
    return (
        record,
        existing is not None
        or state.transaction.pending_effect == "authorize-peer-rotation-continuation",
    )


def authorize(
    journal: VMHALifecycleJournal,
    record: dict,
    *,
    artifact: str,
    scope: dict,
    execution: str | None = None,
    previous_execution: str | None = None,
) -> None:
    proposed = continuation(journal.state, artifact=artifact, scope=scope)
    if proposed is None or proposed[0] != record:
        raise RuntimeError("Peer continuation changed before approval persistence")
    resume(record, scope)
    updates = {CONTINUATION: json.dumps(record, sort_keys=True)}
    if execution is not None:
        assert journal.state.transaction is not None
        bindings = dict(journal.state.transaction.resource_bindings)
        existing = json.loads(bindings[COMMAND]) if COMMAND in bindings else None
        command = {
            "execution": execution,
            "previous_execution": previous_execution,
            "approval": record,
        }
        if (
            re.fullmatch(r"[a-f0-9]{32}", execution) is None
            or not previous_execution
            or bindings.get("runtime-command-execution") != previous_execution
            or existing is not None
            and existing != command
        ):
            raise RuntimeError("Peer continuation has different command execution authority")
        updates[COMMAND] = json.dumps(command, sort_keys=True)
    effect = "authorize-peer-rotation-continuation"
    journal.begin(effect)
    journal.complete(effect, resource_updates=updates)


def command_authority(state: VMHALifecycleState) -> tuple[str | None, dict | None]:
    """Continuation is an append-only handoff, preserving original preparation."""
    bindings = dict(state.transaction.resource_bindings) if state.transaction else {}
    command = continuation_command(bindings)
    if command is None:
        return (
            bindings.get("runtime-command-execution"),
            json.loads(bindings.get("runtime-command-approval", "null")),
        )
    return command["execution"], command["approval"]


def continuation_command(bindings: Mapping[str, str]) -> dict | None:
    from ..apply_approval import validate

    if COMMAND not in bindings:
        return None
    try:
        command = json.loads(bindings[COMMAND])
        prepared = json.loads(bindings[PREPARED])
        original = json.loads(bindings["runtime-command-approval"])
        validate(original)
        validate(command["approval"])
        if (
            set(command) != {"execution", "previous_execution", "approval"}
            or re.fullmatch(r"[a-f0-9]{32}", command["execution"]) is None
            or re.fullmatch(r"[a-f0-9]{32}", command["previous_execution"]) is None
            or command["execution"] == command["previous_execution"]
            or command["previous_execution"] != bindings.get("runtime-command-execution")
            or command["approval"] != json.loads(bindings[CONTINUATION])
            or command["approval"]["scope"] != prepared["continuation_scope"]
            or original["scope"] != prepared["preparation_scope"]
        ):
            raise ValueError("different authority")
        return command
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise ValueError("Peer continuation has inconsistent command authority") from error
