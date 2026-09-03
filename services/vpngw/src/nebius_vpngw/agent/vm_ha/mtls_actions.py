"""Root-only JSON action boundary for VM-local managed mTLS state."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ..vm_ha_rearm import _acquire_rearm_lock
from .auto_healing import (
    AutoHealingPolicyError,
    require_auto_healing_writer_quiescent,
)
from .models import canonical_json
from .mtls import (
    ManagedMTLSError,
    ManagedMTLSStore,
    MTLSOperationKind,
    MTLSReceipt,
    MTLSSnapshot,
)

ACTION_SCHEMA = "nebius-vpngw/vm-ha-mtls-action-v1"
ACTION_NAMES = (
    "status",
    "prepare",
    "prepare-peer-replacement",
    "stage-peer",
    "expand-trust",
    "activate",
    "record-observation",
    "commit",
    "prune",
    "rollback",
    "inhibit",
    "release-inhibition",
)


def _require_keys(request: Mapping[str, Any], expected: set[str]) -> None:
    if set(request) != expected:
        raise ManagedMTLSError("managed mTLS action request is invalid")


def _require_operation_id(value: object) -> str:
    operation_id = str(value)
    if len(operation_id) != 64 or any(
        character not in "0123456789abcdef" for character in operation_id
    ):
        raise ManagedMTLSError("managed mTLS action operation identity is invalid")
    return operation_id


def _snapshot_public(snapshot: MTLSSnapshot) -> dict[str, object]:
    return {
        "cluster_id": snapshot.cluster_id,
        "node_id": snapshot.node_id,
        "compute_id": snapshot.compute_id,
        "epoch": snapshot.epoch,
        "certificate_fingerprint": snapshot.certificate_fingerprint,
        "spki_fingerprint": snapshot.spki_fingerprint,
        "peers": [peer.to_dict() for peer in snapshot.peers],
    }


def decode_action_request(encoded: str) -> Mapping[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ManagedMTLSError("managed mTLS action request is invalid") from None
    if not isinstance(value, Mapping):
        raise ManagedMTLSError("managed mTLS action request is invalid")
    return value


def encode_action_request(request: Mapping[str, Any]) -> str:
    return base64.b64encode(canonical_json(request).encode("ascii")).decode("ascii")


def execute_mtls_action(
    action: str,
    request: Mapping[str, Any],
    *,
    state_dir: Path,
    config_path: Path | None = None,
    require_root: bool = True,
) -> dict[str, object]:
    """Execute one allowlisted action without returning local private material."""

    if action not in ACTION_NAMES:
        raise ManagedMTLSError("managed mTLS action is invalid")
    if require_root and os.geteuid() != 0:
        raise ManagedMTLSError("managed mTLS action requires root")
    store = ManagedMTLSStore(state_dir / "mtls", create=action != "status")

    result: object
    if action == "status":
        _require_keys(request, set())
        result = store.status().to_dict()
    elif action in {"inhibit", "release-inhibition"}:
        _require_keys(
            request,
            {"operation_id", "cluster_id", "node_id", "generation_id"},
        )
        if config_path is None:
            raise ManagedMTLSError("managed mTLS inhibition requires installed runtime identity")
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            raise ManagedMTLSError(
                "managed mTLS installed runtime identity is unavailable"
            ) from None
        vm_ha = payload.get("vm_ha") if isinstance(payload, Mapping) else None
        node = vm_ha.get("node") if isinstance(vm_ha, Mapping) else None
        generation = vm_ha.get("generation") if isinstance(vm_ha, Mapping) else None
        expected = (
            str(vm_ha.get("cluster_id") or "") if isinstance(vm_ha, Mapping) else "",
            str(node.get("node_id") or "") if isinstance(node, Mapping) else "",
            str(generation.get("generation_id") or "") if isinstance(generation, Mapping) else "",
        )
        requested = (
            str(request["cluster_id"]),
            str(request["node_id"]),
            str(request["generation_id"]),
        )
        if requested != expected:
            raise ManagedMTLSError("managed mTLS inhibition runtime identity is stale")
        operation_id = _require_operation_id(request["operation_id"])
        writer_lock = _acquire_rearm_lock(
            state_dir / "rearm.lock",
            timeout_seconds=25.0,
        )
        if writer_lock is None:
            raise ManagedMTLSError("managed mTLS inhibition writer is busy")
        try:
            if action == "inhibit":
                if (state_dir / "apply.lock").exists():
                    raise ManagedMTLSError("managed mTLS inhibition conflicts with an apply lock")
                try:
                    require_auto_healing_writer_quiescent(state_dir)
                except AutoHealingPolicyError as error:
                    raise ManagedMTLSError(
                        "managed mTLS inhibition conflicts with standby auto-healing"
                    ) from error
                result = store.install_inhibition(
                    operation_id=operation_id,
                    cluster_id=requested[0],
                    node_id=requested[1],
                    generation_id=requested[2],
                )
            else:
                result = store.release_inhibition(operation_id)
        finally:
            os.close(writer_lock)
    elif action == "prepare":
        _require_keys(
            request,
            {
                "operation_id",
                "operation_kind",
                "cluster_id",
                "node_id",
                "compute_id",
                "target_epoch",
                "peer_epoch",
            },
        )
        try:
            kind = MTLSOperationKind(str(request["operation_kind"]))
        except ValueError:
            raise ManagedMTLSError("managed mTLS operation kind is invalid") from None
        receipt = store.prepare_identity(
            operation_id=_require_operation_id(request["operation_id"]),
            operation_kind=kind,
            cluster_id=str(request["cluster_id"]),
            node_id=str(request["node_id"]),
            compute_id=str(request["compute_id"]),
            target_epoch=request["target_epoch"],  # type: ignore[arg-type]
            peer_epoch=request["peer_epoch"],  # type: ignore[arg-type]
        )
        result = receipt.to_dict()
    elif action == "prepare-peer-replacement":
        _require_keys(
            request,
            {
                "operation_id",
                "cluster_id",
                "node_id",
                "compute_id",
                "target_peer_epoch",
            },
        )
        result = store.prepare_peer_replacement(
            operation_id=_require_operation_id(request["operation_id"]),
            cluster_id=str(request["cluster_id"]),
            node_id=str(request["node_id"]),
            compute_id=str(request["compute_id"]),
            target_peer_epoch=request["target_peer_epoch"],  # type: ignore[arg-type]
        ).to_dict()
    elif action == "stage-peer":
        _require_keys(request, {"operation_id", "peer_receipt"})
        operation_id = _require_operation_id(request["operation_id"])
        result = store.stage_peer_receipt(
            operation_id,
            MTLSReceipt.from_mapping(request["peer_receipt"]),
        ).to_dict()
    else:
        operation_id = _require_operation_id(request.get("operation_id"))
        if action == "record-observation":
            _require_keys(
                request,
                {
                    "operation_id",
                    "local_certificate_fingerprint",
                    "peer_certificate_fingerprint",
                    "local_epoch",
                    "peer_epoch",
                    "observation_id",
                },
            )
            result = {
                "verified_observations": store.record_fresh_observation(
                    operation_id,
                    local_certificate_fingerprint=str(request["local_certificate_fingerprint"]),
                    peer_certificate_fingerprint=str(request["peer_certificate_fingerprint"]),
                    local_epoch=request["local_epoch"],  # type: ignore[arg-type]
                    peer_epoch=request["peer_epoch"],  # type: ignore[arg-type]
                    observation_id=str(request["observation_id"]),
                )
            }
        else:
            _require_keys(request, {"operation_id"})
            if action == "expand-trust":
                snapshot = store.expand_peer_trust(operation_id)
                result = None if snapshot is None else _snapshot_public(snapshot)
            elif action == "activate":
                result = _snapshot_public(store.activate_identity(operation_id))
            elif action == "commit":
                result = _snapshot_public(store.commit_transaction(operation_id))
            elif action == "prune":
                result = _snapshot_public(store.prune_obsolete(operation_id))
            elif action == "rollback":
                store.rollback_before_switch(operation_id)
                result = store.status().to_dict()
            else:  # pragma: no cover - ACTION_NAMES and branches are kept in lockstep.
                raise ManagedMTLSError("managed mTLS action is invalid")

    response: dict[str, object] = {
        "schema": ACTION_SCHEMA,
        "action": action,
        "result": result,
    }
    encoded = canonical_json(response).lower()
    if "private-key" in encoded or "begin private key" in encoded:
        raise ManagedMTLSError("managed mTLS action response contains private material")
    return response
