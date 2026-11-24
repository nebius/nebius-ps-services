"""Controller-owned, narrow successor authority; never a promotion receipt."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..vm_ha_checkpoint import controller_checkpoint_from_dict
from ..vm_ha_controller import ControllerSnapshot, DataPlaneMode, HAState, RestorationRouteAuthority
from .auto_healing import (
    AutoHealingPolicyError,
    AutoHealingPolicyPhase,
    AutoHealingPolicyStore,
    StandbyAutoHealing,
)
from .models import canonical_json
from .restoration import (
    RestorationPhase,
    StandbyRestorationError,
    StandbyRestorationStore,
    policy_authorizes_restoration,
)
from .store import atomic_write_json

FILENAME = "standby-restoration-revalidation.json"
SCHEMA = "nebius-vpngw/vm-ha-restoration-revalidation-v1"
CHECKPOINT = "controller-checkpoint.json"


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def _request(
    state_dir: Path, receipt_id: str, owner: str, peer: str, generation: str
) -> str | None:
    path = state_dir / "rearm-request.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    from .rearm_request import validate_request

    authority = StandbyRestorationStore(state_dir).load()
    if authority is None:
        raise ValueError("restoration operator authority is missing")
    validate_request(
        value,
        cluster=authority.cluster_id,
        owner=owner,
        peer=peer,
        generation=generation,
        receipt=receipt_id,
    )
    return value["request_id"]


def reconciliation_authority(
    state_dir: Path, snapshot: ControllerSnapshot
) -> RestorationRouteAuthority | None:
    """Admit role-neutral route reproof, never an allocation transfer or Start."""
    try:
        authority = StandbyRestorationStore(state_dir).load()
        if authority is None or authority.phase not in {
            RestorationPhase.COMMITTED,
            RestorationPhase.START_ACCEPTED,
        }:
            return None
        checkpoint = controller_checkpoint_from_dict(
            json.loads((state_dir / CHECKPOINT).read_text())
        )
        policy = AutoHealingPolicyStore(state_dir).load()
        if not (
            authority.promotion_receipt_id is not None
            and authority.cluster_id == snapshot.cluster_id
            and authority.owner_node_id == snapshot.local_node_id
            and authority.former_owner_node_id == snapshot.peer_node_id
            and authority.generation_id == snapshot.local_generation_id
            and dict(authority.digests) == snapshot.local_digests.to_dict()
            and authority.allocation_id == snapshot.cloud.allocation_id
            and snapshot.cloud.transfer_complete(snapshot.local_node_id)
            and policy is not None
            and policy.phase is AutoHealingPolicyPhase.COMMITTED
            and policy.desired is StandbyAutoHealing.ENABLED
            and policy_authorizes_restoration(policy, authority)
            and not snapshot.apply_locked
            and not snapshot.transfer_inhibited
            and not snapshot.standby_replacement_inhibited
        ):
            return None
        operator = _request(
            state_dir,
            authority.promotion_receipt_id,
            authority.owner_node_id,
            authority.former_owner_node_id,
            authority.generation_id,
        )
        if operator is not None:
            return RestorationRouteAuthority(checkpoint.ownership_incarnation, operator)
        if (
            not checkpoint.ownership_continuity_invalidated
            and authority.ownership_incarnation == checkpoint.ownership_incarnation
        ):
            return RestorationRouteAuthority(checkpoint.ownership_incarnation)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        StandbyRestorationError,
        AutoHealingPolicyError,
    ):
        pass
    return None


def publish(state_dir: Path, observe: Callable[[], ControllerSnapshot]) -> bool:
    """Called by the controller between steps, with the rearm lock already held."""
    store = StandbyRestorationStore(state_dir)
    authority = store.load()
    if authority is None or authority.phase not in {
        RestorationPhase.COMMITTED,
        RestorationPhase.START_ACCEPTED,
    }:
        return False
    assert authority.promotion_receipt_id is not None
    checkpoint_raw = json.loads((state_dir / CHECKPOINT).read_text())
    checkpoint = controller_checkpoint_from_dict(checkpoint_raw)
    operator = _request(
        state_dir,
        authority.promotion_receipt_id,
        authority.owner_node_id,
        authority.former_owner_node_id,
        authority.generation_id,
    )
    if operator is None and (
        authority.ownership_incarnation is None
        or checkpoint.ownership_incarnation != authority.ownership_incarnation
    ):
        return False
    if any(
        (state_dir / name).exists()
        for name in (
            "apply.lock",
            "transfer-lineage.json",
            "accepted-cloud-operation.json",
            "rearm-cloud-operation.json",
            "mtls/inhibition.json",
        )
    ):
        return False
    snapshot = observe()
    route = snapshot.routes_reconciled_context
    policy = AutoHealingPolicyStore(state_dir).require_bound(
        cluster_id=authority.cluster_id,
        node_id=authority.owner_node_id,
        peer_node_id=authority.former_owner_node_id,
        generation_id=authority.generation_id,
    )
    if not (
        policy.phase is AutoHealingPolicyPhase.COMMITTED
        and policy.desired is StandbyAutoHealing.ENABLED
        and policy_authorizes_restoration(policy, authority)
        and snapshot.cluster_id == authority.cluster_id
        and snapshot.local_node_id == authority.owner_node_id
        and snapshot.peer_node_id == authority.former_owner_node_id
        and snapshot.local_generation_id == authority.generation_id
        and snapshot.local_digests.to_dict() == dict(authority.digests)
        and snapshot.cloud.allocation_id == authority.allocation_id
        and snapshot.cloud.transfer_complete(authority.owner_node_id)
        and snapshot.guard_boot_id == snapshot.boot_id
        and snapshot.data_plane_mode is DataPlaneMode.ACTIVE
        and snapshot.readiness.promotion_ready
        and not snapshot.apply_locked
        and not snapshot.transfer_inhibited
        and not snapshot.standby_replacement_inhibited
        and checkpoint.state is HAState.ACTIVE
        and checkpoint.pending_action is None
        and checkpoint.transfer_continuity is None
        and checkpoint.repair_attempt is None
        and not checkpoint.ownership_continuity_invalidated
        and checkpoint.established_ownership_context == snapshot.ownership_context
        and route is not None
        and route.ownership_context == snapshot.ownership_context
        and route.generation_id == authority.generation_id
        and route.digests == snapshot.local_digests
        and route.ownership_incarnation == checkpoint.ownership_incarnation
        and bool(route.operation_id)
    ):
        return False
    route_receipt = json.loads(
        (state_dir / "generation-store/route-reconciliation-receipt.json").read_text()
    )
    proof = {
        "route_receipt_digest": _digest(route_receipt),
        "schema": SCHEMA,
        "authorization_id": authority.authorization_id,
        "promotion_receipt_id": authority.promotion_receipt_id,
        "original_epoch": authority.ownership_epoch,
        "current_epoch": snapshot.cloud.ownership_epoch,
        "ownership_incarnation": checkpoint.ownership_incarnation,
        "operator_request_id": operator,
        "checkpoint_digest": _digest(checkpoint_raw),
        "policy_digest": policy.decision_digest,
        "boot_id": snapshot.boot_id,
        "route_operation_id": route.operation_id,
    }
    if (
        json.loads((state_dir / CHECKPOINT).read_text()) != checkpoint_raw
        or store.load() != authority
        or AutoHealingPolicyStore(state_dir).load() != policy
    ):
        return False
    if operator is not None:
        authority = store.authorize_operator(
            request_id=operator,
            incarnation=checkpoint.ownership_incarnation,
            epoch=snapshot.cloud.ownership_epoch,
            updated_at=snapshot.wall_time if snapshot.wall_time is not None else snapshot.now,
        )
        proof["authorization_id"] = authority.authorization_id
    path = state_dir / FILENAME
    if path.exists() and json.loads(path.read_text()) == proof:
        return True
    atomic_write_json(path, proof)
    return True


def permits(state_dir: Path, *, receipt_id: str, current_epoch: str, boot_id: str) -> bool:
    """Recheck durable authority after the caller's independent cloud observations."""
    try:
        authority = StandbyRestorationStore(state_dir).load()
        proof = json.loads((state_dir / FILENAME).read_text())
        raw = json.loads((state_dir / CHECKPOINT).read_text())
        checkpoint = controller_checkpoint_from_dict(raw)
        if authority is None:
            return False
        policy = AutoHealingPolicyStore(state_dir).load()
        guard = json.loads((state_dir / "guard.json").read_text())
        route_receipt = json.loads(
            (state_dir / "generation-store/route-reconciliation-receipt.json").read_text()
        )
        if not isinstance(guard, dict) or not isinstance(proof, dict):
            return False
        return bool(
            guard.get("schema") == "nebius-vpngw/vm-ha-status-v1"
            and guard.get("guard_boot_id") == boot_id
            and guard.get("data_plane_mode") == "active"
            and guard.get("generation_id") == authority.generation_id
            and guard.get("allocation_id") == authority.allocation_id
            and guard.get("node_id") == authority.owner_node_id
            and guard.get("digests") == dict(authority.digests)
            and proof.get("route_receipt_digest") == _digest(route_receipt)
            and set(proof)
            == {
                "route_receipt_digest",
                "schema",
                "authorization_id",
                "promotion_receipt_id",
                "original_epoch",
                "current_epoch",
                "ownership_incarnation",
                "operator_request_id",
                "checkpoint_digest",
                "policy_digest",
                "boot_id",
                "route_operation_id",
            }
            and proof["schema"] == SCHEMA
            and proof["authorization_id"] == authority.authorization_id
            and proof["promotion_receipt_id"] == authority.promotion_receipt_id == receipt_id
            and proof["original_epoch"] == authority.ownership_epoch
            and proof["current_epoch"] == current_epoch
            and proof["boot_id"] == boot_id
            and proof["checkpoint_digest"] == _digest(raw)
            and proof["ownership_incarnation"] == checkpoint.ownership_incarnation
            and not checkpoint.ownership_continuity_invalidated
            and authority.ownership_incarnation == checkpoint.ownership_incarnation
            and (
                proof["operator_request_id"] is None
                or (
                    authority.source.value == "operator-restoration"
                    and proof["operator_request_id"] == authority.request_fingerprint
                )
            )
            and policy is not None
            and policy.phase is AutoHealingPolicyPhase.COMMITTED
            and policy.desired is StandbyAutoHealing.ENABLED
            and proof["policy_digest"] == policy.decision_digest
            and policy_authorizes_restoration(policy, authority)
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        StandbyRestorationError,
        AutoHealingPolicyError,
    ):
        return False
