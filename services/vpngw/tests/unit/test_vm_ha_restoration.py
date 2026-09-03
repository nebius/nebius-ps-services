from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    StandbyAutoHealing,
    policy_decision_digest,
)
from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.promotion_receipt import (
    PROMOTION_RECEIPT_SCHEMA,
    promotion_receipt_id_v1,
)
from nebius_vpngw.agent.vm_ha.restoration import (
    PolicyAgreementStore,
    RestorationPhase,
    RestorationSource,
    StandbyRestorationAuthorization,
    StandbyRestorationError,
    StandbyRestorationStore,
    policy_authorizes_restoration,
    require_standby_restoration_writer_quiescent,
)

DIGESTS = {
    "configuration": "a" * 64,
    "static_routes": "b" * 64,
    "bgp_policy": "c" * 64,
}


def _receipt(
    *,
    source: RestorationSource = RestorationSource.PLANNED_FAILBACK,
    first_operation_id: str = "stop-former-owner",
    **overrides: object,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": PROMOTION_RECEIPT_SCHEMA,
        "intent": source.value,
        "cluster_id": "cluster-a",
        "owner_node_id": "node-a",
        "former_owner_node_id": "node-b",
        "allocation_id": "allocation-a",
        "ownership_epoch": "revision-10",
        "generation_id": "a" * 64,
        "digests": DIGESTS,
        "route_operation_id": "route-operation",
        "committed_at": 103.0,
        "common_cutover_seconds": 2.0,
    }
    receipt.update(overrides)
    receipt["receipt_id"] = promotion_receipt_id_v1(
        allocation_id=str(receipt["allocation_id"]),
        first_operation_id=first_operation_id,
        generation_id=str(receipt["generation_id"]),
        intent=str(receipt["intent"]),
        owner_node_id=str(receipt["owner_node_id"]),
        ownership_epoch=str(receipt["ownership_epoch"]),
        route_operation_id=str(receipt["route_operation_id"]),
    )
    return receipt


RECEIPT_ID = str(_receipt()["receipt_id"])


def _certificate(tmp_path: Path):
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=90.0,
    )
    heartbeat = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=7,
        sent_at="1970-01-01T00:01:40Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet.from_mapping(DIGESTS),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest=policy.decision_digest,
    )
    return PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=heartbeat,
        captured_at=100.0,
    )


def _committed(
    tmp_path: Path,
    *,
    source: RestorationSource = RestorationSource.PLANNED_FAILBACK,
):
    store = StandbyRestorationStore(tmp_path)
    armed = store.arm(
        source=source,
        certificate=_certificate(tmp_path),
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=DIGESTS,
        request_fingerprint=(None if source is RestorationSource.AUTOMATIC_FAILOVER else "e" * 64),
        first_operation_id=(
            "stop-former-owner" if source is RestorationSource.AUTOMATIC_FAILOVER else None
        ),
        updated_at=101.0,
    )
    if source is not RestorationSource.AUTOMATIC_FAILOVER:
        store.bind_first_effect(
            authorization_id=armed.authorization_id,
            operation_id="stop-former-owner",
            updated_at=102.0,
        )
    receipt = _receipt(source=source)
    store.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    committed = store.commit(receipt=receipt, updated_at=103.0)
    assert committed is not None
    return store, committed


def _completed(tmp_path: Path) -> StandbyRestorationStore:
    store, _committed_record = _committed(tmp_path)
    store.accept_start(
        receipt_id=RECEIPT_ID,
        stopped_revision="revision-20",
        rearm_operation_id="rearm-operation",
        updated_at=104.0,
    )
    store.record_submission(receipt_id=RECEIPT_ID, updated_at=105.0)
    store.mark_running(receipt_id=RECEIPT_ID, updated_at=106.0)
    store.await_standby(receipt_id=RECEIPT_ID, updated_at=107.0)
    store.complete(receipt_id=RECEIPT_ID, updated_at=108.0)
    return store


def _prepared_disable(
    tmp_path: Path,
    *,
    predecessor_digest: str,
) -> AutoHealingPolicyRecord:
    current = AutoHealingPolicyStore(tmp_path).load()
    assert current is not None
    operation_id = "f" * 64
    decision_digest = policy_decision_digest(
        cluster_id=current.cluster_id,
        member_node_ids=(current.node_id, current.peer_node_id),
        generation_id=current.generation_id,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id=current.coordinator_node_id,
        predecessor_digest=predecessor_digest,
    )
    return AutoHealingPolicyRecord(
        cluster_id=current.cluster_id,
        node_id=current.node_id,
        peer_node_id=current.peer_node_id,
        generation_id=current.generation_id,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id=current.coordinator_node_id,
        predecessor_digest=predecessor_digest,
        phase=AutoHealingPolicyPhase.PREPARED,
        decision_digest=decision_digest,
        peer_ack_digest=None,
        updated_at=104.0,
    )


def test_planned_restoration_is_armed_before_effect_and_committed_by_receipt(
    tmp_path: Path,
) -> None:
    store, committed = _committed(tmp_path)

    assert committed.phase is RestorationPhase.COMMITTED
    assert committed.first_operation_id == "stop-former-owner"
    assert committed.promotion_receipt_id == RECEIPT_ID
    assert store.load() == committed

    with pytest.raises(StandbyRestorationError, match="active"):
        require_standby_restoration_writer_quiescent(tmp_path)


def test_prepared_disable_predecessor_is_automatic_failover_only(tmp_path: Path) -> None:
    planned_dir = tmp_path / "planned"
    automatic_dir = tmp_path / "automatic"
    planned_dir.mkdir()
    automatic_dir.mkdir()
    _planned_store, planned = _committed(planned_dir)
    _automatic_store, automatic = _committed(
        automatic_dir,
        source=RestorationSource.AUTOMATIC_FAILOVER,
    )

    assert not policy_authorizes_restoration(
        _prepared_disable(
            planned_dir,
            predecessor_digest=planned.policy_digest,
        ),
        planned,
    )
    assert policy_authorizes_restoration(
        _prepared_disable(
            automatic_dir,
            predecessor_digest=automatic.policy_digest,
        ),
        automatic,
    )


def test_authorization_v1_shape_remains_readable(tmp_path: Path) -> None:
    store, committed = _committed(tmp_path)

    assert committed.to_dict()["schema"] == (
        "nebius-vpngw/vm-ha-standby-restoration-authorization-v1"
    )
    assert store.load() == committed


def test_restoration_reuses_one_start_identity_and_exhausts_five_attempts(
    tmp_path: Path,
) -> None:
    store, _committed_record = _committed(tmp_path)
    accepted = store.accept_start(
        receipt_id=RECEIPT_ID,
        stopped_revision="revision-20",
        rearm_operation_id="rearm-operation",
        updated_at=104.0,
    )

    due_at = 104.0
    for attempt, delay in enumerate((5.0, 15.0, 30.0, 60.0), start=1):
        submitted = store.record_submission(
            receipt_id=RECEIPT_ID,
            updated_at=due_at,
        )
        assert submitted.attempt_count == attempt
        scheduled = store.schedule_retry(
            receipt_id=RECEIPT_ID,
            reason="RetryableHACloudError",
            updated_at=due_at,
        )
        assert scheduled.phase is RestorationPhase.START_ACCEPTED
        assert scheduled.rearm_operation_id == accepted.rearm_operation_id
        assert scheduled.next_attempt_at == due_at + delay
        due_at += delay

    fifth = store.record_submission(receipt_id=RECEIPT_ID, updated_at=due_at)
    assert fifth.attempt_count == 5
    blocked = store.schedule_retry(
        receipt_id=RECEIPT_ID,
        reason="AmbiguousHACloudError",
        updated_at=due_at,
    )
    assert blocked.phase is RestorationPhase.BLOCKED
    assert blocked.blocked_reason == "automatic-retry-exhausted"


def test_running_restoration_waits_for_standby_before_completion(tmp_path: Path) -> None:
    store, _committed_record = _committed(tmp_path)
    store.accept_start(
        receipt_id=RECEIPT_ID,
        stopped_revision="revision-20",
        rearm_operation_id="rearm-operation",
        updated_at=104.0,
    )
    store.record_submission(receipt_id=RECEIPT_ID, updated_at=105.0)
    running = store.mark_running(receipt_id=RECEIPT_ID, updated_at=106.0)
    assert running.phase is RestorationPhase.RUNNING

    awaiting = store.await_standby(receipt_id=RECEIPT_ID, updated_at=107.0)
    assert awaiting.phase is RestorationPhase.AWAITING_STANDBY
    assert awaiting.standby_deadline_at == 407.0

    completed = store.complete(receipt_id=RECEIPT_ID, updated_at=108.0)
    assert completed.phase is RestorationPhase.COMPLETED
    require_standby_restoration_writer_quiescent(tmp_path)


def test_terminal_restoration_superseded_by_exact_apply_owner_adoption_is_retired(
    tmp_path: Path,
) -> None:
    store = _completed(tmp_path)
    adoption = _receipt(intent="apply-owner-adoption")
    store.receipt_path.write_text(json.dumps(adoption), encoding="utf-8")

    with pytest.raises(
        StandbyRestorationError,
        match="promotion receipt does not match restoration authority",
    ):
        store.load()

    assert store.retire_terminal_for_apply_owner_adoption()
    assert not store.path.exists()
    assert not store.retire_terminal_for_apply_owner_adoption()


def test_apply_owner_adoption_repair_preserves_active_or_foreign_restoration(
    tmp_path: Path,
) -> None:
    active_store, _record = _committed(tmp_path / "active")
    active_store.receipt_path.write_text(
        json.dumps(_receipt(intent="apply-owner-adoption")),
        encoding="utf-8",
    )

    assert not active_store.retire_terminal_for_apply_owner_adoption()
    assert active_store.path.exists()

    foreign_store = _completed(tmp_path / "foreign")
    foreign_store.receipt_path.write_text(
        json.dumps(
            _receipt(
                intent="apply-owner-adoption",
                owner_node_id="node-b",
                former_owner_node_id="node-a",
            )
        ),
        encoding="utf-8",
    )

    assert not foreign_store.retire_terminal_for_apply_owner_adoption()
    assert foreign_store.path.exists()


def test_corrupt_restoration_authority_fails_closed(tmp_path: Path) -> None:
    store = StandbyRestorationStore(tmp_path)
    store.path.write_text('{"schema":"wrong"}', encoding="utf-8")

    with pytest.raises(StandbyRestorationError):
        store.load()


def test_operator_recovery_restarts_only_the_same_blocked_receipt(tmp_path: Path) -> None:
    store, _committed_record = _committed(tmp_path)
    store.accept_start(
        receipt_id=RECEIPT_ID,
        stopped_revision="revision-20",
        rearm_operation_id="rearm-operation",
        updated_at=104.0,
    )
    blocked = store.block(
        receipt_id=RECEIPT_ID,
        reason="automatic-retry-exhausted",
        updated_at=105.0,
    )

    restarted = store.restart_blocked(receipt_id=RECEIPT_ID, updated_at=106.0)

    assert restarted.phase is RestorationPhase.START_ACCEPTED
    assert restarted.source is RestorationSource.OPERATOR_RESTORATION
    assert restarted.authorization_id != blocked.authorization_id
    assert restarted.attempt_count == 0
    assert restarted.stopped_revision == blocked.stopped_revision
    assert restarted.rearm_operation_id == blocked.rearm_operation_id
    assert StandbyRestorationStore(tmp_path).load() == restarted


def test_non_armed_authorization_parser_remains_filesystem_independent(tmp_path: Path) -> None:
    _store, committed = _committed(tmp_path)

    assert StandbyRestorationAuthorization.from_mapping(committed.to_dict()) == committed


def test_commit_and_reload_reprove_the_same_durable_receipt(tmp_path: Path) -> None:
    store, committed = _committed(tmp_path)
    receipt = json.loads(store.receipt_path.read_text(encoding="utf-8"))

    assert store.commit(receipt=receipt, updated_at=104.0) == committed
    assert StandbyRestorationStore(tmp_path).load() == committed


def test_commit_rejects_receipt_argument_that_differs_from_durable_receipt(
    tmp_path: Path,
) -> None:
    store, _committed_record = _committed(tmp_path)
    foreign = _receipt(route_operation_id="route-foreign")

    with pytest.raises(StandbyRestorationError, match="changed before restoration commit"):
        store.commit(receipt=foreign, updated_at=104.0)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("intent", "planned-failover"),
        ("cluster_id", "cluster-foreign"),
        ("owner_node_id", "node-c"),
        ("former_owner_node_id", "node-c"),
        ("allocation_id", "allocation-foreign"),
        ("ownership_epoch", "revision-foreign"),
        ("generation_id", "f" * 64),
        (
            "digests",
            {
                "configuration": "f" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
        ),
        ("route_operation_id", "route-foreign"),
    ],
)
def test_reload_rejects_foreign_receipt_fields(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    store, _committed_record = _committed(tmp_path)
    foreign = _receipt(**{field: replacement})
    store.receipt_path.write_text(json.dumps(foreign), encoding="utf-8")

    with pytest.raises(StandbyRestorationError, match="promotion receipt"):
        store.load()


@pytest.mark.parametrize("mode", ["missing", "unreadable", "malformed", "bad-digest"])
def test_reload_fails_closed_when_durable_receipt_is_unusable(
    tmp_path: Path,
    mode: str,
) -> None:
    store, _committed_record = _committed(tmp_path)
    if mode == "missing":
        store.receipt_path.unlink()
    elif mode == "unreadable":
        store.receipt_path.write_bytes(b"\xff")
    elif mode == "malformed":
        store.receipt_path.write_text("{}", encoding="utf-8")
    else:
        receipt = _receipt()
        receipt["receipt_id"] = "f" * 64
        store.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(StandbyRestorationError, match="promotion receipt"):
        store.load()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("cluster_id", "cluster-foreign"),
        ("owner_node_id", "node-c"),
        ("former_owner_node_id", "node-c"),
        ("allocation_id", "allocation-foreign"),
        ("generation_id", "f" * 64),
        (
            "digests",
            {
                "configuration": "f" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
        ),
        ("first_operation_id", "different-first-effect"),
        ("promotion_receipt_id", "f" * 64),
        ("ownership_epoch", "revision-foreign"),
        ("route_operation_id", "route-foreign"),
    ],
)
def test_reload_rejects_tampered_authorization_receipt_binding(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    store, committed = _committed(tmp_path)
    payload = committed.to_dict()
    payload[field] = replacement
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        StandbyRestorationError,
        match="authorization digest|promotion receipt|first effect",
    ):
        store.load()
