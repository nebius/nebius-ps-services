from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryRecord,
    AutoHealingRecoveryStore,
    StandbyAutoHealing,
)
from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSError
from nebius_vpngw.agent.vm_ha.mtls_actions import (
    ACTION_SCHEMA,
    decode_action_request,
    encode_action_request,
    execute_mtls_action,
)


def _operation(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _prepare_request(operation_id: str, node: str) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "operation_kind": "bootstrap",
        "cluster_id": "cluster-a",
        "node_id": node,
        "compute_id": f"compute-{node[-1]}",
        "target_epoch": 1,
        "peer_epoch": 1,
    }


def test_action_request_encoding_is_canonical_and_strict() -> None:
    request = {"operation_id": _operation("action")}
    assert decode_action_request(encode_action_request(request)) == request
    with pytest.raises(ManagedMTLSError, match="request is invalid"):
        decode_action_request("not-base64")


def test_root_action_bootstraps_both_members_without_returning_private_material(
    tmp_path: Path,
) -> None:
    operation_id = _operation("bootstrap")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = execute_mtls_action(
        "prepare",
        _prepare_request(operation_id, "node-a"),
        state_dir=first_dir,
        require_root=False,
    )
    second = execute_mtls_action(
        "prepare",
        _prepare_request(operation_id, "node-b"),
        state_dir=second_dir,
        require_root=False,
    )

    execute_mtls_action(
        "stage-peer",
        {"operation_id": operation_id, "peer_receipt": second["result"]},
        state_dir=first_dir,
        require_root=False,
    )
    execute_mtls_action(
        "stage-peer",
        {"operation_id": operation_id, "peer_receipt": first["result"]},
        state_dir=second_dir,
        require_root=False,
    )
    for state_dir in (first_dir, second_dir):
        assert (
            execute_mtls_action(
                "expand-trust",
                {"operation_id": operation_id},
                state_dir=state_dir,
                require_root=False,
            )["result"]
            is None
        )
        execute_mtls_action(
            "activate",
            {"operation_id": operation_id},
            state_dir=state_dir,
            require_root=False,
        )
    first_receipt = first["result"]
    second_receipt = second["result"]
    assert isinstance(first_receipt, dict) and isinstance(second_receipt, dict)
    for state_dir, local, peer, label in (
        (first_dir, first_receipt, second_receipt, "first"),
        (second_dir, second_receipt, first_receipt, "second"),
    ):
        execute_mtls_action(
            "record-observation",
            {
                "operation_id": operation_id,
                "local_certificate_fingerprint": local["certificate_fingerprint"],
                "peer_certificate_fingerprint": peer["certificate_fingerprint"],
                "local_epoch": 1,
                "peer_epoch": 1,
                "observation_id": _operation(f"{label}-observation"),
            },
            state_dir=state_dir,
            require_root=False,
        )
    for state_dir in (first_dir, second_dir):
        execute_mtls_action(
            "commit",
            {"operation_id": operation_id},
            state_dir=state_dir,
            require_root=False,
        )
        status = execute_mtls_action(
            "prune",
            {"operation_id": operation_id},
            state_dir=state_dir,
            require_root=False,
        )
        assert status["schema"] == ACTION_SCHEMA
        encoded = json.dumps(status).lower()
        assert "private" not in encoded
        assert "/var/" not in encoded


def test_stage_peer_rejects_foreign_operation_receipt(tmp_path: Path) -> None:
    first_operation = _operation("first")
    second_operation = _operation("second")
    first_dir = tmp_path / "first"
    second = execute_mtls_action(
        "prepare",
        _prepare_request(second_operation, "node-b"),
        state_dir=tmp_path / "second",
        require_root=False,
    )
    execute_mtls_action(
        "prepare",
        _prepare_request(first_operation, "node-a"),
        state_dir=first_dir,
        require_root=False,
    )

    with pytest.raises(ManagedMTLSError, match="outside the operation"):
        execute_mtls_action(
            "stage-peer",
            {"operation_id": first_operation, "peer_receipt": second["result"]},
            state_dir=first_dir,
            require_root=False,
        )


def test_action_rejects_unknown_fields_before_mutation(tmp_path: Path) -> None:
    with pytest.raises(ManagedMTLSError, match="request is invalid"):
        execute_mtls_action(
            "prepare",
            {**_prepare_request(_operation("unknown"), "node-a"), "extra": True},
            state_dir=tmp_path,
            require_root=False,
        )
    assert not (tmp_path / "mtls" / "active.json").exists()


def test_inhibition_refuses_a_concurrent_apply_lock(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        json.dumps(
            {
                "vm_ha": {
                    "cluster_id": "cluster-a",
                    "node": {"node_id": "node-a"},
                    "generation": {"generation_id": "a" * 64},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "apply.lock").write_text("{}", encoding="utf-8")

    with pytest.raises(ManagedMTLSError, match="conflicts with an apply lock"):
        execute_mtls_action(
            "inhibit",
            {
                "operation_id": _operation("rotation"),
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
            },
            state_dir=tmp_path,
            config_path=config_path,
            require_root=False,
        )

    assert not (tmp_path / "mtls" / "inhibition.json").exists()


def test_inhibition_refuses_an_active_standby_recovery(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        json.dumps(
            {
                "vm_ha": {
                    "cluster_id": "cluster-a",
                    "node": {"node_id": "node-a"},
                    "generation": {"generation_id": "a" * 64},
                }
            }
        ),
        encoding="utf-8",
    )
    AutoHealingRecoveryStore(tmp_path).arm(
        AutoHealingRecoveryRecord(
            cluster_id="cluster-a",
            node_id="node-a",
            target_node_id="node-b",
            generation_id="a" * 64,
            desired=StandbyAutoHealing.ENABLED,
            operation_id="b" * 64,
            approval_digest="c" * 64,
            policy_digest="d" * 64,
            predecessor_digest="d" * 64,
            promotion_receipt_id="receipt-a",
            allocation_id="allocation-a",
            ownership_epoch="7",
            stopped_revision="8",
            phase=AutoHealingRecoveryPhase.ARMED,
            rearm_operation_id=None,
            updated_at=1.0,
        )
    )

    with pytest.raises(ManagedMTLSError, match="conflicts with standby auto-healing"):
        execute_mtls_action(
            "inhibit",
            {
                "operation_id": _operation("rotation"),
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
            },
            state_dir=tmp_path,
            config_path=config_path,
            require_root=False,
        )

    assert not (tmp_path / "mtls" / "inhibition.json").exists()
