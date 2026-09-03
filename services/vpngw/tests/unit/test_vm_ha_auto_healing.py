from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent.vm_ha import (
    AtomicGenerationStore,
    CorruptStateError,
    ReplayState,
)
from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyError,
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryRecord,
    AutoHealingRecoveryStore,
    StandbyAutoHealing,
    auto_healing_recovery_digest,
    decode_policy_request,
    encode_policy_request,
    policy_decision_digest,
    project_local_policy,
    require_auto_healing_writer_quiescent,
)
from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat, canonical_json
from nebius_vpngw.agent.vm_ha.restoration import (
    PolicyAgreementStore,
    RestorationSource,
    StandbyRestorationStore,
)
from nebius_vpngw.cli import (
    _clear_completed_vm_ha_auto_healing_recovery,
    _execute_vm_ha_auto_healing_policy,
    _run_vm_ha_auto_healing_statuses,
    _vm_ha_auto_healing_is_terminal,
    _vm_ha_auto_healing_recovery_required,
    _vm_ha_auto_healing_transaction,
)


def _initialize_pair(tmp_path: Path, generation: str = "a" * 64):
    passive = AutoHealingPolicyStore(tmp_path / "passive")
    owner = AutoHealingPolicyStore(tmp_path / "owner")
    passive_record = passive.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id=generation,
        updated_at=1.0,
    )
    owner_record = owner.initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id=generation,
        updated_at=1.0,
    )
    return passive, owner, passive_record, owner_record


def _disable_pair(tmp_path: Path):
    passive, owner, passive_initial, owner_initial = _initialize_pair(tmp_path)
    operation_id = "d" * 64
    owner_prepared = owner.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_initial,
        updated_at=2.0,
    )
    passive_prepared = passive.prepare(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=passive_initial.decision_digest,
        peer=owner_prepared,
        updated_at=2.0,
    )
    passive_committed = passive.commit(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=passive_initial.decision_digest,
        peer=owner_prepared,
        updated_at=3.0,
    )
    owner_committed = owner.commit(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_committed,
        updated_at=3.0,
    )
    return passive, owner, passive_committed, owner_committed, passive_prepared


def test_initialization_is_explicit_enabled_and_idempotent(tmp_path: Path) -> None:
    passive, owner, passive_record, owner_record = _initialize_pair(tmp_path)

    assert passive_record.desired is StandbyAutoHealing.ENABLED
    assert owner_record.desired is StandbyAutoHealing.ENABLED
    assert passive_record.phase is AutoHealingPolicyPhase.COMMITTED
    assert passive_record.operation_id == owner_record.operation_id
    assert passive_record.decision_digest == owner_record.decision_digest
    assert (
        passive.initialize(
            cluster_id="cluster-a",
            node_id="node-b",
            peer_node_id="node-a",
            generation_id="a" * 64,
            updated_at=9.0,
        )
        == passive_record
    )


def test_fresh_replacement_adopts_only_retained_terminal_enabled_policy(
    tmp_path: Path,
) -> None:
    passive, owner, passive_initial, owner_initial = _initialize_pair(tmp_path / "original")
    operation_id = "e" * 64
    owner_prepared = owner.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_initial,
        updated_at=2.0,
    )
    passive.prepare(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=passive_initial.decision_digest,
        peer=owner_prepared,
        updated_at=2.0,
    )
    passive_committed = passive.commit(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=passive_initial.decision_digest,
        peer=owner_prepared,
        updated_at=3.0,
    )
    owner_committed = owner.commit(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_committed,
        updated_at=3.0,
    )
    replacement = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        updated_at=4.0,
    )

    adopted = replacement.adopt_replacement_peer(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        peer=owner_committed,
        updated_at=5.0,
    )

    assert adopted.operation_id == owner_committed.operation_id
    assert adopted.decision_digest == owner_committed.decision_digest
    assert adopted.peer_ack_digest == adopted.decision_digest
    assert (
        replacement.adopt_replacement_peer(
            cluster_id="cluster-a",
            node_id="node-b",
            peer_node_id="node-a",
            generation_id="a" * 64,
            peer=owner_committed,
            updated_at=6.0,
        )
        == adopted
    )
    assert (
        replacement.adopt_replacement_peer(
            cluster_id="cluster-a",
            node_id="node-b",
            peer_node_id="node-a",
            generation_id="a" * 64,
            peer=owner_committed,
            updated_at=7.0,
        )
        == adopted
    )


def test_fresh_replacement_adopts_exact_default_unacknowledged_owner(
    tmp_path: Path,
) -> None:
    _passive, _owner, _passive_initial, owner_initial = _initialize_pair(tmp_path / "original")
    replacement = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        updated_at=2.0,
    )

    adopted = replacement.adopt_replacement_peer(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        peer=owner_initial,
        updated_at=3.0,
    )

    assert adopted.operation_id == owner_initial.operation_id
    assert adopted.decision_digest == owner_initial.decision_digest
    assert adopted.peer_ack_digest == adopted.decision_digest


def test_fresh_replacement_rejects_nondefault_unacknowledged_owner(
    tmp_path: Path,
) -> None:
    _passive, _owner, _passive_initial, owner_initial = _initialize_pair(tmp_path / "original")
    replacement = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        updated_at=2.0,
    )
    operation_id = "e" * 64
    decision_digest = policy_decision_digest(
        cluster_id=owner_initial.cluster_id,
        member_node_ids=(owner_initial.node_id, owner_initial.peer_node_id),
        generation_id=owner_initial.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id=owner_initial.coordinator_node_id,
        predecessor_digest=owner_initial.decision_digest,
    )
    unsafe_owner = AutoHealingPolicyRecord(
        **{
            **owner_initial.__dict__,
            "operation_id": operation_id,
            "predecessor_digest": owner_initial.decision_digest,
            "decision_digest": decision_digest,
            "peer_ack_digest": None,
        }
    )

    with pytest.raises(
        AutoHealingPolicyError,
        match="replacement peer policy is not terminal and exact",
    ):
        replacement.adopt_replacement_peer(
            cluster_id="cluster-a",
            node_id="node-b",
            peer_node_id="node-a",
            generation_id="a" * 64,
            peer=unsafe_owner,
            updated_at=3.0,
        )


def test_activation_initialize_discards_legacy_advisory_heartbeat_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = SimpleNamespace(cluster_id="cluster-a", generation_id="a" * 64)
    local = SimpleNamespace(
        node_id="node-b",
        compute_id="compute-b",
        role=SimpleNamespace(value="passive"),
    )
    peer = SimpleNamespace(node_id="node-a")
    monkeypatch.setattr(
        agent_main,
        "_read_vm_ha_config",
        lambda _path: {"enabled": True},
    )
    monkeypatch.setattr(
        agent_main,
        "_auto_healing_context",
        lambda _config: (binding, local, peer),
    )
    generation_store = AtomicGenerationStore(tmp_path / "generation-store")
    replay = ReplayState("peer-boot", 7)
    generation_store.save_replay_state(peer.node_id, replay)
    legacy_heartbeat = {
        "boot_id": "peer-boot",
        "certificate_fingerprint": "d" * 64,
        "cluster_id": binding.cluster_id,
        "configured_role": "active",
        "digests": {
            "bgp_policy": "c" * 64,
            "configuration": binding.generation_id,
            "static_routes": "b" * 64,
        },
        "generation_id": binding.generation_id,
        "mtls_epoch": 1,
        "node_id": peer.node_id,
        "observed_owner_id": peer.node_id,
        "promotion_ready": True,
        "route_ready": True,
        "schema": "nebius-vpngw/vm-ha-heartbeat-v2",
        "sent_at": "2026-08-27T00:00:00Z",
        "sequence": replay.highest_sequence,
        "service_healthy": True,
    }
    heartbeat_path = generation_store.peers / peer.node_id / "accepted-heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_envelope = json.dumps(
        {
            "payload": legacy_heartbeat,
            "schema": "nebius-vpngw/vm-ha-envelope-v1",
            "sha256": hashlib.sha256(canonical_json(legacy_heartbeat).encode("utf-8")).hexdigest(),
        }
    )
    heartbeat_path.write_text(legacy_envelope, encoding="utf-8")

    result = agent_main.vm_ha_auto_healing_policy(
        "initialize",
        config_path=tmp_path / "config.yaml",
        state_dir=tmp_path,
        clock=lambda: 1.0,
    )

    assert result == {
        "schema": "nebius-vpngw/vm-ha-auto-healing-initialize-result-v1",
        "cluster_id": binding.cluster_id,
        "node_id": local.node_id,
        "configured_role": local.role.value,
        "generation_id": binding.generation_id,
        "desired": "enabled",
        "phase": "committed",
        "operation_id": result["operation_id"],
        "decision_digest": result["decision_digest"],
    }
    reset_path = generation_store.peers / peer.node_id / "accepted-heartbeat-reset.json"
    assert reset_path.exists()
    assert not heartbeat_path.exists()
    # A still-running old controller may write its private schema again after
    # initialization. The new controller consumes the durable reset only after
    # systemd has stopped that old process.
    heartbeat_path.write_text(legacy_envelope, encoding="utf-8")
    assert generation_store.consume_accepted_peer_heartbeat_reset(
        peer.node_id,
        generation_id=binding.generation_id,
    )
    assert not heartbeat_path.exists()
    assert not reset_path.exists()
    assert generation_store.load_replay_state(peer.node_id) == replay
    assert (
        AutoHealingPolicyStore(tmp_path)
        .require_bound(
            cluster_id=binding.cluster_id,
            node_id=local.node_id,
            peer_node_id=peer.node_id,
            generation_id=binding.generation_id,
        )
        .decision_digest
        == result["decision_digest"]
    )


def test_agent_adopts_exact_replacement_policy_and_resets_peer_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = SimpleNamespace(cluster_id="cluster-a", generation_id="a" * 64)
    local = SimpleNamespace(
        node_id="node-b",
        compute_id="compute-b",
        role=SimpleNamespace(value="passive"),
    )
    peer = SimpleNamespace(node_id="node-a")
    monkeypatch.setattr(agent_main, "_read_vm_ha_config", lambda _path: {"enabled": True})
    monkeypatch.setattr(
        agent_main,
        "_auto_healing_context",
        lambda _config: (binding, local, peer),
    )
    store = AutoHealingPolicyStore(tmp_path)
    initial = store.initialize(
        cluster_id=binding.cluster_id,
        node_id=local.node_id,
        peer_node_id=peer.node_id,
        generation_id=binding.generation_id,
        updated_at=1.0,
    )
    operation_id = "e" * 64
    predecessor_digest = "b" * 64
    decision_digest = policy_decision_digest(
        cluster_id=binding.cluster_id,
        member_node_ids=(local.node_id, peer.node_id),
        generation_id=binding.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id=peer.node_id,
        predecessor_digest=predecessor_digest,
    )
    owner = AutoHealingPolicyRecord(
        cluster_id=binding.cluster_id,
        node_id=peer.node_id,
        peer_node_id=local.node_id,
        generation_id=binding.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id=peer.node_id,
        predecessor_digest=predecessor_digest,
        phase=AutoHealingPolicyPhase.COMMITTED,
        decision_digest=decision_digest,
        peer_ack_digest=decision_digest,
        updated_at=2.0,
    )
    request = encode_policy_request(
        {
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
            "apply_operation_id": "c" * 64,
            "mtls_apply_operation_id": None,
            "mtls_inhibition_operation_id": None,
            "operation_id": owner.operation_id,
            "peer_record": owner.to_dict(),
        }
    )

    with pytest.raises(
        AutoHealingPolicyError,
        match="replacement policy adoption apply lock is missing",
    ):
        agent_main.vm_ha_auto_healing_policy(
            "adopt-replacement",
            request,
            config_path=tmp_path / "config.yaml",
            state_dir=tmp_path,
            clock=lambda: 3.0,
        )
    assert (
        store.require_bound(
            cluster_id=binding.cluster_id,
            node_id=local.node_id,
            peer_node_id=peer.node_id,
            generation_id=binding.generation_id,
        )
        == initial
    )
    assert not (
        tmp_path / "generation-store" / "peers" / peer.node_id / "accepted-heartbeat-reset.json"
    ).exists()

    apply_lock = {
        "apply_locked": True,
        "cluster_id": binding.cluster_id,
        "generation_id": binding.generation_id,
        "node_id": local.node_id,
        "operation_id": "d" * 64,
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
    }
    (tmp_path / "apply.lock").write_text(json.dumps(apply_lock), encoding="utf-8")
    with pytest.raises(
        AutoHealingPolicyError,
        match="replacement policy adoption apply lock changed",
    ):
        agent_main.vm_ha_auto_healing_policy(
            "adopt-replacement",
            request,
            config_path=tmp_path / "config.yaml",
            state_dir=tmp_path,
            clock=lambda: 3.0,
        )
    assert (
        store.require_bound(
            cluster_id=binding.cluster_id,
            node_id=local.node_id,
            peer_node_id=peer.node_id,
            generation_id=binding.generation_id,
        )
        == initial
    )

    apply_lock["operation_id"] = "c" * 64
    (tmp_path / "apply.lock").write_text(json.dumps(apply_lock), encoding="utf-8")

    mtls_operation_id = "f" * 64
    monkeypatch.setattr(
        agent_main.ManagedMTLSStore,
        "status",
        lambda _self: SimpleNamespace(
            cluster_id=binding.cluster_id,
            node_id=local.node_id,
            compute_id=local.compute_id,
            operation_id=mtls_operation_id,
            operation_kind="replacement",
            phase="local-active",
            inhibition_operation_id=None,
        ),
    )
    mismatched_mtls_request = encode_policy_request(
        {
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
            "apply_operation_id": "c" * 64,
            "mtls_apply_operation_id": "0" * 64,
            "mtls_inhibition_operation_id": None,
            "operation_id": owner.operation_id,
            "peer_record": owner.to_dict(),
        }
    )
    with pytest.raises(
        AutoHealingPolicyError,
        match="policy mutation conflicts with an mTLS apply transaction",
    ):
        agent_main.vm_ha_auto_healing_policy(
            "adopt-replacement",
            mismatched_mtls_request,
            config_path=tmp_path / "config.yaml",
            state_dir=tmp_path,
            clock=lambda: 3.0,
        )

    request = encode_policy_request(
        {
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
            "apply_operation_id": "c" * 64,
            "mtls_apply_operation_id": mtls_operation_id,
            "mtls_inhibition_operation_id": None,
            "operation_id": owner.operation_id,
            "peer_record": owner.to_dict(),
        }
    )

    result = agent_main.vm_ha_auto_healing_policy(
        "adopt-replacement",
        request,
        config_path=tmp_path / "config.yaml",
        state_dir=tmp_path,
        clock=lambda: 3.0,
    )

    assert result["operation_id"] == owner.operation_id
    assert result["decision_digest"] == owner.decision_digest
    assert result["record"]["peer_ack_digest"] == owner.decision_digest
    assert (
        tmp_path / "generation-store" / "peers" / peer.node_id / "accepted-heartbeat-reset.json"
    ).exists()


def test_activation_heartbeat_reset_rejects_foreign_generation_without_deletion(
    tmp_path: Path,
) -> None:
    generation_store = AtomicGenerationStore(tmp_path / "generation-store")
    generation_store.request_accepted_peer_heartbeat_reset(
        "node-b",
        generation_id="a" * 64,
    )
    heartbeat_path = generation_store.peers / "node-b" / "accepted-heartbeat.json"
    reset_path = generation_store.peers / "node-b" / "accepted-heartbeat-reset.json"
    heartbeat_path.write_text("{legacy-writer", encoding="utf-8")

    with pytest.raises(CorruptStateError, match="stale or foreign"):
        generation_store.consume_accepted_peer_heartbeat_reset(
            "node-b",
            generation_id="b" * 64,
        )

    assert heartbeat_path.exists()
    assert reset_path.exists()


def test_disable_prepare_inhibits_until_both_exact_commits(tmp_path: Path) -> None:
    passive, owner, passive_committed, owner_committed, passive_prepared = _disable_pair(tmp_path)

    assert project_local_policy(
        AutoHealingPolicyStore(tmp_path / "prepared-only"),
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
    ) == ("blocked", "0" * 64)
    assert passive_prepared.phase is AutoHealingPolicyPhase.PREPARED
    assert passive_committed.peer_ack_digest == passive_committed.decision_digest
    assert owner_committed.peer_ack_digest == owner_committed.decision_digest
    assert project_local_policy(
        passive,
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
    ) == ("disabled", passive_committed.decision_digest)
    assert project_local_policy(
        owner,
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
    ) == ("disabled", owner_committed.decision_digest)


def test_commit_rejects_foreign_peer_acknowledgement(tmp_path: Path) -> None:
    passive, owner, passive_initial, owner_initial = _initialize_pair(tmp_path)
    operation_id = "d" * 64
    owner_prepared = owner.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_initial,
        updated_at=2.0,
    )
    passive.prepare(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=passive_initial.decision_digest,
        peer=owner_prepared,
        updated_at=2.0,
    )
    with pytest.raises(AutoHealingPolicyError, match="peer policy acknowledgement"):
        passive.commit(
            cluster_id="cluster-a",
            node_id="node-b",
            peer_node_id="node-a",
            generation_id="a" * 64,
            desired=StandbyAutoHealing.DISABLED,
            operation_id=operation_id,
            coordinator_node_id="node-a",
            predecessor_digest=passive_initial.decision_digest,
            peer=owner_initial,
            updated_at=3.0,
        )


def test_disabled_policy_rebind_preserves_choice_and_acknowledgement(tmp_path: Path) -> None:
    passive, owner, _, _, _ = _disable_pair(tmp_path)

    rebound_passive = passive.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="f" * 64,
        updated_at=4.0,
    )
    rebound_owner = owner.initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="f" * 64,
        updated_at=4.0,
    )

    assert rebound_passive.desired is StandbyAutoHealing.DISABLED
    assert rebound_passive.peer_ack_digest == rebound_passive.decision_digest
    assert rebound_passive.decision_digest == rebound_owner.decision_digest


def test_corrupt_or_extra_policy_state_fails_closed(tmp_path: Path) -> None:
    store = AutoHealingPolicyStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"schema": "wrong", "extra": True}), encoding="utf-8")

    with pytest.raises(AutoHealingPolicyError):
        store.load()
    assert project_local_policy(
        store,
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
    ) == ("blocked", "0" * 64)


def test_prepared_transaction_resumes_with_stable_coordinator(tmp_path: Path) -> None:
    _passive, owner, passive_initial, owner_initial = _initialize_pair(tmp_path)
    operation_id = "d" * 64
    owner_prepared = owner.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=passive_initial,
        updated_at=2.0,
    )
    statuses = [_status(owner_prepared, peer_agrees=False)]

    transaction = _vm_ha_auto_healing_transaction(
        desired=StandbyAutoHealing.DISABLED,
        statuses=statuses,
    )

    assert transaction.operation_id == operation_id
    assert transaction.coordinator_node_id == "node-a"
    assert transaction.predecessor_digest == owner_initial.decision_digest
    assert not _vm_ha_auto_healing_is_terminal(
        statuses,
        StandbyAutoHealing.DISABLED,
    )


def _status(record, *, peer_agrees: bool = True) -> dict[str, object]:
    return {
        "accepted_start": False,
        "decision_digest": record.decision_digest,
        "desired": record.desired.value,
        "generation_id": record.generation_id,
        "node_id": record.node_id,
        "operation_id": record.operation_id,
        "peer_agrees": peer_agrees,
        "phase": record.phase.value,
        "record": record.to_dict(),
        "recovery_phase": None,
    }


def test_new_transaction_id_does_not_depend_on_cloud_authority(tmp_path: Path) -> None:
    _passive, _owner, passive_record, owner_record = _initialize_pair(tmp_path)

    first = _vm_ha_auto_healing_transaction(
        desired=StandbyAutoHealing.DISABLED,
        statuses=[_status(owner_record), _status(passive_record)],
    )
    second = _vm_ha_auto_healing_transaction(
        desired=StandbyAutoHealing.DISABLED,
        statuses=[_status(owner_record)],
    )

    assert first == second


def test_recovery_intent_is_single_use_and_blocks_other_writers(tmp_path: Path) -> None:
    record = AutoHealingRecoveryRecord(
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
    store = AutoHealingRecoveryStore(tmp_path)

    store.arm(record)
    with pytest.raises(AutoHealingPolicyError, match="recovery is active"):
        require_auto_healing_writer_quiescent(tmp_path)
    consumed = store.consume(
        operation_id=record.operation_id,
        rearm_operation_id="rearm-a",
        updated_at=2.0,
    )
    assert consumed.phase is AutoHealingRecoveryPhase.CONSUMED
    with pytest.raises(AutoHealingPolicyError, match="another start"):
        store.consume(
            operation_id=record.operation_id,
            rearm_operation_id="rearm-b",
            updated_at=3.0,
        )
    completed = store.complete(
        operation_id=record.operation_id,
        rearm_operation_id="rearm-a",
        updated_at=4.0,
    )
    require_auto_healing_writer_quiescent(tmp_path)
    store.clear_completed(
        operation_id=record.operation_id,
        recovery_digest=auto_healing_recovery_digest(completed),
    )
    assert store.load() is None
    store.arm(record)
    store.cancel_armed(
        operation_id=record.operation_id,
        approval_digest=record.approval_digest,
    )
    assert store.load() is None


def test_completed_recovery_clear_rejects_same_operation_replacement(tmp_path: Path) -> None:
    first = AutoHealingRecoveryRecord(
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
    store = AutoHealingRecoveryStore(tmp_path)
    store.arm(first)
    store.consume(
        operation_id=first.operation_id,
        rearm_operation_id="rearm-a",
        updated_at=2.0,
    )
    observed = store.complete(
        operation_id=first.operation_id,
        rearm_operation_id="rearm-a",
        updated_at=3.0,
    )
    observed_digest = auto_healing_recovery_digest(observed)

    replacement = AutoHealingRecoveryRecord(
        **{
            **first.__dict__,
            "approval_digest": "e" * 64,
            "stopped_revision": "9",
            "updated_at": 4.0,
        }
    )
    store.arm(replacement)
    store.consume(
        operation_id=replacement.operation_id,
        rearm_operation_id="rearm-b",
        updated_at=5.0,
    )
    current = store.complete(
        operation_id=replacement.operation_id,
        rearm_operation_id="rearm-b",
        updated_at=6.0,
    )

    with pytest.raises(AutoHealingPolicyError, match="recovery state changed"):
        store.clear_completed(
            operation_id=observed.operation_id,
            recovery_digest=observed_digest,
        )
    assert store.load() == current


def test_policy_mutation_admission_rejects_active_standby_restoration(
    tmp_path: Path,
) -> None:
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    heartbeat = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=1,
        sent_at="1970-01-01T00:00:20Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest=policy.decision_digest,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=heartbeat,
        captured_at=20.0,
    )
    StandbyRestorationStore(tmp_path).arm(
        source=RestorationSource.AUTOMATIC_FAILOVER,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint=None,
        first_operation_id=None,
        updated_at=20.0,
    )

    with pytest.raises(AutoHealingPolicyError, match="standby restoration"):
        agent_main._require_auto_healing_mutation_admission(state_dir=tmp_path)


def test_policy_executor_uses_coordinator_first_and_last_cas_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    passive, owner, passive_initial, owner_initial = _initialize_pair(tmp_path)
    stores = {"node-a": owner, "node-b": passive}
    records = {"node-a": owner_initial, "node-b": passive_initial}
    transaction = _vm_ha_auto_healing_transaction(
        desired=StandbyAutoHealing.DISABLED,
        statuses=[_status(owner_initial), _status(passive_initial)],
    )
    calls: list[tuple[str, str]] = []
    authority_checks = 0

    def guard() -> None:
        nonlocal authority_checks
        authority_checks += 1

    def statuses() -> list[dict[str, object]]:
        terminal = all(
            record.phase is AutoHealingPolicyPhase.COMMITTED
            and record.operation_id == transaction.operation_id
            for record in records.values()
        )
        return [_status(records[node_id], peer_agrees=terminal) for node_id in ("node-a", "node-b")]

    def fake_action(**kwargs):
        action = kwargs["action"]
        if action == "status":
            return statuses()
        node_id = next(iter(kwargs["node_ids"]))
        request = decode_policy_request(kwargs["requests"][node_id])
        parsed_peer = records[next(item for item in records if item != node_id)]
        common = {
            "cluster_id": "cluster-a",
            "node_id": node_id,
            "peer_node_id": parsed_peer.node_id,
            "generation_id": "a" * 64,
            "desired": StandbyAutoHealing(request["desired"]),
            "operation_id": request["operation_id"],
            "coordinator_node_id": request["coordinator_node_id"],
            "predecessor_digest": request["predecessor_digest"],
            "peer": type(parsed_peer).from_mapping(request["peer_record"]),
            "updated_at": float(len(calls) + 2),
        }
        records[node_id] = getattr(stores[node_id], action)(**common)
        calls.append((action, node_id))
        return [_status(records[node_id], peer_agrees=False)]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)

    result = _execute_vm_ha_auto_healing_policy(
        config_path=tmp_path / "config.yaml",
        desired=StandbyAutoHealing.DISABLED,
        transaction=transaction,
        initial_statuses=statuses(),
        authority_guard=guard,
        timeout_seconds=1.0,
    )

    assert calls == [
        ("prepare", "node-a"),
        ("prepare", "node-b"),
        ("commit", "node-b"),
        ("commit", "node-a"),
    ]
    assert authority_checks == 4
    assert _vm_ha_auto_healing_is_terminal(result, StandbyAutoHealing.DISABLED)


def test_status_reader_falls_back_only_to_exact_disabled_owner_for_enable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _passive, _owner, _passive_record, owner_record, _prepared = _disable_pair(tmp_path)
    owner_status = {
        **_status(owner_record),
        "recovery": None,
        "recovery_authority": {
            "allocation_id": "allocation-a",
            "ownership_epoch": "7",
            "promotion_receipt_id": "receipt-a",
        },
    }
    calls: list[frozenset[str] | None] = []

    def fake_action(**kwargs):
        calls.append(kwargs.get("node_ids"))
        if kwargs.get("node_ids") is None:
            raise RuntimeError("standby is offline")
        return [owner_status]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)
    inspection = SimpleNamespace(
        snapshot=SimpleNamespace(
            authority=SimpleNamespace(owner_node_id="node-a"),
        )
    )

    assert _run_vm_ha_auto_healing_statuses(
        local_config_file=tmp_path / "config.yaml",
        desired=StandbyAutoHealing.ENABLED,
        inspection=inspection,
        require_capability=True,
    ) == [owner_status]
    assert calls == [None, frozenset({"node-a"})]


def test_status_reader_admits_exact_missing_owner_policy_for_enabled_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner_status = {
        "accepted_start": False,
        "cluster_id": "cluster-a",
        "configured_role": "active",
        "decision_digest": None,
        "desired": None,
        "generation_id": "a" * 64,
        "node_id": "node-a",
        "operation_id": None,
        "peer_agrees": False,
        "phase": "blocked",
        "record": None,
        "recovery": None,
        "recovery_authority": {
            "allocation_id": "allocation-a",
            "ownership_epoch": "7",
            "promotion_receipt_id": "receipt-a",
        },
        "recovery_phase": None,
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v2",
    }
    calls: list[frozenset[str] | None] = []

    def fake_action(**kwargs):
        calls.append(kwargs.get("node_ids"))
        if kwargs.get("node_ids") is None:
            raise RuntimeError("standby is offline")
        return [owner_status]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)
    inspection = SimpleNamespace(
        snapshot=SimpleNamespace(
            authority=SimpleNamespace(owner_node_id="node-a"),
        )
    )

    assert _run_vm_ha_auto_healing_statuses(
        local_config_file=tmp_path / "config.yaml",
        desired=StandbyAutoHealing.ENABLED,
        inspection=inspection,
        require_capability=True,
    ) == [owner_status]
    assert calls == [None, frozenset({"node-a"})]


def test_initialized_enabled_owner_can_arm_exact_offline_bootstrap_recovery(
    tmp_path: Path,
) -> None:
    _passive, _owner, _passive_record, owner_record = _initialize_pair(tmp_path)
    owner_status = {
        **_status(owner_record, peer_agrees=False),
        "recovery": None,
        "recovery_authority": {
            "allocation_id": "allocation-a",
            "ownership_epoch": "7",
            "promotion_receipt_id": "receipt-a",
        },
    }
    inspection = SimpleNamespace(
        snapshot=SimpleNamespace(
            authority=SimpleNamespace(owner_node_id=owner_record.node_id),
        )
    )

    assert _vm_ha_auto_healing_recovery_required(
        desired=StandbyAutoHealing.ENABLED,
        inspection=inspection,
        statuses=[owner_status],
    )


def test_terminal_enabled_policy_retries_exact_completed_recovery_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _passive, _owner, passive_record, owner_record = _initialize_pair(tmp_path)
    recovery = AutoHealingRecoveryRecord(
        cluster_id=owner_record.cluster_id,
        node_id=owner_record.node_id,
        target_node_id=owner_record.peer_node_id,
        generation_id=owner_record.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=owner_record.operation_id,
        approval_digest="c" * 64,
        policy_digest=owner_record.predecessor_digest,
        predecessor_digest=owner_record.predecessor_digest,
        promotion_receipt_id="receipt-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        stopped_revision="8",
        phase=AutoHealingRecoveryPhase.COMPLETED,
        rearm_operation_id="rearm-a",
        updated_at=4.0,
    )
    owner_status = {
        **_status(owner_record),
        "recovery_phase": recovery.phase.value,
        "recovery": recovery.to_dict(),
    }
    statuses = [owner_status, _status(passive_record)]
    calls: list[dict[str, object]] = []

    def fake_action(**kwargs):
        calls.append(kwargs)
        return [{**owner_status, "recovery_phase": None, "recovery": None}]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)

    assert _clear_completed_vm_ha_auto_healing_recovery(
        config_path=tmp_path / "config.yaml",
        statuses=statuses,
    )
    assert len(calls) == 1
    assert calls[0]["action"] == "clear-recovery"
    assert calls[0]["node_ids"] == frozenset({owner_record.node_id})
    request = decode_policy_request(calls[0]["requests"][owner_record.node_id])
    assert request == {
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
        "operation_id": owner_record.operation_id,
        "recovery_digest": auto_healing_recovery_digest(recovery),
    }


def test_terminal_enabled_policy_clears_every_member_local_completed_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _passive, _owner, passive_record, owner_record = _initialize_pair(tmp_path)

    def recovery_for(
        record,
        *,
        suffix: str,
    ) -> AutoHealingRecoveryRecord:
        return AutoHealingRecoveryRecord(
            cluster_id=record.cluster_id,
            node_id=record.node_id,
            target_node_id=record.peer_node_id,
            generation_id=record.generation_id,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=record.operation_id,
            approval_digest=suffix * 64,
            policy_digest=record.decision_digest,
            predecessor_digest=record.predecessor_digest,
            promotion_receipt_id=f"receipt-{suffix}",
            allocation_id=f"allocation-{suffix}",
            ownership_epoch="7",
            stopped_revision=f"revision-{suffix}",
            phase=AutoHealingRecoveryPhase.COMPLETED,
            rearm_operation_id=f"rearm-{suffix}",
            updated_at=4.0,
        )

    recoveries = {
        passive_record.node_id: recovery_for(passive_record, suffix="d"),
        owner_record.node_id: recovery_for(owner_record, suffix="e"),
    }
    statuses = [
        {
            **_status(record),
            "recovery_phase": recoveries[record.node_id].phase.value,
            "recovery": recoveries[record.node_id].to_dict(),
        }
        for record in (passive_record, owner_record)
    ]
    calls: list[dict[str, object]] = []

    def fake_action(**kwargs):
        calls.append(kwargs)
        return [{**status, "recovery_phase": None, "recovery": None} for status in statuses]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)

    assert _clear_completed_vm_ha_auto_healing_recovery(
        config_path=tmp_path / "config.yaml",
        statuses=statuses,
    )
    assert len(calls) == 1
    assert calls[0]["action"] == "clear-recovery"
    assert calls[0]["node_ids"] == frozenset(recoveries)
    requests = calls[0]["requests"]
    assert isinstance(requests, dict)
    for node_id, recovery in recoveries.items():
        assert decode_policy_request(requests[node_id]) == {
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
            "operation_id": recovery.operation_id,
            "recovery_digest": auto_healing_recovery_digest(recovery),
        }
