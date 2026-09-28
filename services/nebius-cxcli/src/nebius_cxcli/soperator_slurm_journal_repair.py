"""Seal the proven node-tuple identity defect without discarding recovery history."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .soperator_slurm_recovery import (
    SOPERATOR_SLURM_RECOVERY_SCHEMA,
    SlurmRecoveryDisposition,
    _sha256,
    normalize_slurm_recovery_event,
    validate_slurm_recovery_actions,
)

_REPAIR = "node-sequence-action-identity/v1"
_IDENTITY = (
    "schema",
    "targetRef",
    "command",
    "startedAt",
    "policy",
    "slurmPreimage",
    "operationSpecSha256",
)
_AFFECTED = {"scheduling-pause-recorded", "scheduling-pause-applied", "no-blocking-jobs"}


def _nodes(action: Mapping[str, Any]) -> tuple[str, ...]:
    nodes = action.get("node_names")
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", n) for n in nodes)
        or len(nodes) != len(set(nodes))
    ):
        raise RuntimeError("Slurm action identity repair lacks an exact node scope")
    return tuple(sorted(nodes))


def _correct(actions: object) -> list[dict[str, Any]]:
    if not isinstance(actions, list) or not actions:
        raise RuntimeError("Slurm action identity repair lacks original events")
    anchors: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}
    for action in actions:
        if not isinstance(action, dict):
            raise RuntimeError("Slurm action identity repair has malformed events")
        name = action.get("action")
        if name in {"slurm-gate-started", "slurm-gate-complete"}:
            validate_slurm_recovery_actions([action])
            key = (action["namespace"], action["checkpoint_id"])
            group = anchors.setdefault(key, {})
            if name in group:
                raise RuntimeError("Slurm action identity repair has ambiguous gate evidence")
            group[name] = _nodes(action)
    corrected = copy.deepcopy(actions)
    changed = False
    for action in corrected:
        try:
            validate_slurm_recovery_actions([action])
            continue
        except RuntimeError:
            pass
        if action.get("action") not in _AFFECTED:
            raise RuntimeError("Slurm action identity repair encountered an unrelated defect")
        group = anchors.get((action.get("namespace"), action.get("checkpoint_id")), {})
        nodes = _nodes(action)
        if group != {"slurm-gate-started": nodes, "slurm-gate-complete": nodes}:
            raise RuntimeError("Slurm action identity repair differs from its bound gate scope")
        try:
            epoch = int(action["fencingEpoch"])
            disposition = SlurmRecoveryDisposition(action["disposition"])
            omitted = normalize_slurm_recovery_event(
                {**action, "node_names": []}, fencing_epoch=epoch, disposition=disposition
            )
            normal = normalize_slurm_recovery_event(
                action, fencing_epoch=epoch, disposition=disposition
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Slurm action identity repair has invalid fencing evidence") from exc
        if (
            action.get("actionId") != omitted["actionId"]
            or action.get("actionKind") != normal["actionKind"]
            or omitted["actionId"] == normal["actionId"]
        ):
            raise RuntimeError("Slurm action identity mismatch is not the node-tuple defect")
        action["actionId"] = normal["actionId"]
        validate_slurm_recovery_actions([action])
        changed = True
    if not changed:
        raise RuntimeError("Slurm action identity repair has no proven correction")
    return corrected


def admit_action_identity_repair(
    journal: Mapping[str, Any], *, local: Mapping[str, Any] | None, allow_repair: bool
) -> dict[str, Any]:
    """Return canonical events; the caller must persist through its fenced CAS owner."""
    result = copy.deepcopy(dict(journal))
    repair = result.get("actionIdentityRepair")
    if repair is not None:
        if (
            not isinstance(repair, dict)
            or set(repair) != {"schema", "identity", "originalActions", "correctedSha256"}
            or repair["schema"] != _REPAIR
            or repair["identity"] != {key: result.get(key) for key in _IDENTITY}
        ):
            raise RuntimeError("Slurm action identity repair binding changed")
        corrected = _correct(repair["originalActions"])
        if (
            repair["correctedSha256"] != _sha256(corrected)
            or result.get("actions", [])[: len(corrected)] != corrected
        ):
            raise RuntimeError("Slurm action identity repair history changed")
        validate_slurm_recovery_actions(result["actions"])
        return result
    try:
        validate_slurm_recovery_actions(result["actions"])
        return result
    except RuntimeError:
        if not allow_repair:
            raise
    if (
        result.get("schema") != SOPERATOR_SLURM_RECOVERY_SCHEMA
        or result.get("command") != "deploy"
        or result.get("status") != "recovery-required"
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(result.get("operationSpecSha256", "")))
        or not isinstance(local, Mapping)
        or any(key not in result or local.get(key) != result[key] for key in _IDENTITY)
        or local.get("actions") != result["actions"]
    ):
        raise RuntimeError(
            "Slurm action identity repair requires matching exact operation journals"
        )
    corrected = _correct(result["actions"])
    result["actionIdentityRepair"] = {
        "schema": _REPAIR,
        "identity": {key: copy.deepcopy(result[key]) for key in _IDENTITY},
        "originalActions": result["actions"],
        "correctedSha256": _sha256(corrected),
    }
    result["actions"] = corrected
    return result
