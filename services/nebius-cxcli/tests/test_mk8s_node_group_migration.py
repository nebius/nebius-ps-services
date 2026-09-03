from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from nebius_cxcli.mk8s_node_group_migration import (
    MIGRATION_PHASES,
    MigrationPhaseResult,
    _from_payload,
    _new_receipt,
    build_node_group_migration_intent,
    legacy_node_group_checkpoint_path,
    load_node_group_migration_receipt,
    node_group_migration_intent_from_payload,
    node_group_migration_receipt_path,
    refuse_legacy_node_group_checkpoint,
    run_node_group_migration,
    validate_node_group_migration_intent,
)


def _intent():
    return build_node_group_migration_intent(
        target_selector="infra:mk8s@cluster-a",
        instance_id="cluster-a",
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        source_key="worker",
        source_provider_name="worker",
        replacement_key="worker-b200",
        replacement_provider_name="worker-b200",
        source_platform="gpu-h100-sxm",
        target_platform="gpu-b200-sxm",
        source_preset="8gpu-128vcpu-1600gb",
        target_preset="8gpu-160vcpu-1792gb",
        source_os="ubuntu22.04",
        target_os="ubuntu24.04",
        source_gpu_stack_preset="cuda12.8",
        target_gpu_stack_preset="cuda13.0",
        source_gpu_cluster_key="workers",
        target_gpu_cluster_key="workers-b200",
        source_fabric="fabric-4",
        target_fabric="fabric-6",
        reservation_policy="STRICT",
        reservation_ids=("capacityblock-1",),
        desired_node_count=2,
        autoscaling_preimage={"min_node_count": 0, "max_node_count": 4},
        soperator_managed=True,
        placement_preimage={"worker": ("worker",)},
        shared_storage_evidence=("sfs_filesystem_keys:jail",),
        source_group={
            "name": "worker",
            "platform": "gpu-h100-sxm",
            "preset": "8gpu-128vcpu-1600gb",
        },
        job_policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout="0s",
        job_refresh_interval="30s",
        source_config_sha256="sha256:" + "a" * 64,
        source_project_snapshot_sha256="sha256:" + "c" * 64,
    )


def _enter_maintenance(_record_event, _existing_evidence):
    return {"mode": "test"}


def _restore_maintenance(_evidence):
    return {"mode": "test"}


def test_intent_requires_permanent_replacement_identity() -> None:
    with pytest.raises(ValueError, match="replacement node-group key"):
        validate_node_group_migration_intent(replace(_intent(), replacement_key="worker"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("desired_node_count", 0, "at least one"),
        ("reservation_policy", "UNKNOWN", "AUTO, FORBID, or STRICT"),
        ("placement_preimage", {}, "frozen placement"),
        ("placement_preimage", {"worker": ("another",)}, "frozen placement"),
    ),
)
def test_intent_rejects_unsafe_execution_contracts(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_node_group_migration_intent(replace(_intent(), **{field: value}))


def test_embedded_intent_rejects_scalar_sequence_fields() -> None:
    payload = _intent().__dict__.copy()
    payload["placement_preimage"] = {"worker": "worker"}

    with pytest.raises(RuntimeError, match="embedded intent is invalid"):
        node_group_migration_intent_from_payload(payload)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"schema": "wrong"}, "unsupported schema"),
        ({"target_selector": ""}, "requires target selector"),
        ({"replacement_provider_name": "worker"}, "source name"),
        ({"source_config_sha256": "bad"}, "source config digest"),
        ({"source_project_snapshot_sha256": "bad"}, "project snapshot digest"),
        ({"shared_storage_evidence": ()}, "shared-storage continuity"),
        ({"source_group": {}}, "source group preimage"),
        ({"job_policy": ""}, "Slurm job controls"),
        ({"reservation_ids": ()}, "STRICT.*reservation ids"),
    ),
)
def test_migration_intent_rejects_invalid_frozen_authority(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_node_group_migration_intent(replace(_intent(), **changes))


def test_migration_intent_payload_round_trip_and_non_soperator_contract() -> None:
    intent = replace(
        _intent(),
        soperator_managed=False,
        placement_preimage={},
        shared_storage_evidence=(),
        reservation_policy="AUTO",
        reservation_ids=(),
    )

    validate_node_group_migration_intent(intent)
    assert node_group_migration_intent_from_payload(intent.__dict__) == intent


@pytest.mark.parametrize("unsafe", (".", "..", "***"))
def test_migration_receipt_path_rejects_unsafe_identity(
    tmp_path: Path,
    unsafe: str,
) -> None:
    with pytest.raises(ValueError, match="safe path"):
        node_group_migration_receipt_path(
            tmp_path,
            instance_id=unsafe,
            source_key="worker",
        )


def _migration_receipt_payload() -> dict[str, object]:
    return json.loads(json.dumps(asdict(_new_receipt(_intent()))))


def test_migration_receipt_parser_rejects_non_object_and_missing_ledger() -> None:
    with pytest.raises(RuntimeError, match="must be an object"):
        _from_payload([])
    payload = _migration_receipt_payload()
    payload["phases"] = "invalid"
    with pytest.raises(RuntimeError, match="has no phase ledger"):
        _from_payload(payload)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload.update(schema="wrong"),
        lambda payload: payload.update(status="wrong"),
        lambda payload: payload.update(recovery_mode="wrong"),
        lambda payload: payload.update(maintenance="wrong"),
        lambda payload: payload.update(intent_sha256="bad"),
        lambda payload: payload["intent"].update(target_selector="foreign"),
        lambda payload: payload["phases"].append("invalid"),
        lambda payload: payload["phases"][0].update(status="wrong"),
    ),
)
def test_migration_receipt_parser_rejects_invalid_contract(mutate) -> None:
    payload = _migration_receipt_payload()
    mutate(payload)

    with pytest.raises(RuntimeError, match="invalid contract"):
        _from_payload(payload)


def test_migration_receipt_parser_rejects_missing_or_mismatched_evidence() -> None:
    missing = _migration_receipt_payload()
    missing["phases"][0].update(status="complete", evidence_sha256="")
    with pytest.raises(RuntimeError, match="has no evidence digest"):
        _from_payload(missing)

    mismatched = _migration_receipt_payload()
    mismatched["phases"][0].update(
        status="complete",
        evidence={"ready": True},
        evidence_sha256="sha256:" + "f" * 64,
    )
    with pytest.raises(RuntimeError, match="evidence digest does not match"):
        _from_payload(mismatched)


def test_migration_receipt_parser_requires_maintenance_before_cutover() -> None:
    payload = _migration_receipt_payload()
    payload["phases"][3].update(status="running")

    with pytest.raises(RuntimeError, match="without maintenance authority"):
        _from_payload(payload)


def _migration_executors():
    return {
        name: (lambda name=name: MigrationPhaseResult(evidence={"phase": name}))
        for name in MIGRATION_PHASES
    }


def test_migration_runner_rejects_missing_executor_and_invalid_phase(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    with pytest.raises(RuntimeError, match="has no executor"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors={},
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )
    executors = _migration_executors()
    executors[MIGRATION_PHASES[0]] = lambda: "invalid"
    with pytest.raises(RuntimeError, match="returned invalid evidence"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors=executors,
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )


def test_migration_runner_rejects_invalid_maintenance_and_restoration(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    with pytest.raises(RuntimeError, match="maintenance returned invalid evidence"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors=_migration_executors(),
            enter_maintenance=lambda _record, _existing: "invalid",
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )

    second_path = node_group_migration_receipt_path(
        tmp_path / "second",
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    with pytest.raises(RuntimeError, match="restoration is invalid"):
        run_node_group_migration(
            path=second_path,
            intent=intent,
            phase_executors=_migration_executors(),
            enter_maintenance=_enter_maintenance,
            restore_maintenance=lambda _evidence: "invalid",
            assert_fence=lambda: None,
        )


def test_legacy_checkpoint_is_refused_without_translation(tmp_path: Path) -> None:
    legacy = legacy_node_group_checkpoint_path(
        tmp_path,
        instance_id="cluster-a",
        source_key="worker",
    )
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"phase":"approved-pre-mutation"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="read-only historical evidence"):
        refuse_legacy_node_group_checkpoint(
            tmp_path,
            instance_id="cluster-a",
            source_key="worker",
        )


def test_migration_switches_to_forward_only_after_cutover_and_resumes(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    fail_once = {"value": True}
    calls: dict[str, int] = {}

    def _phase(name: str):
        def _run() -> MigrationPhaseResult:
            calls[name] = calls.get(name, 0) + 1
            if name == "source-retired" and fail_once["value"]:
                fail_once["value"] = False
                raise RuntimeError("injected retirement failure")
            return MigrationPhaseResult(
                evidence={"phase": name},
                irreversible_frontier="scheduling-cutover" if name == "cutover-complete" else "",
            )

        return _run

    executors = {name: _phase(name) for name in MIGRATION_PHASES}
    with pytest.raises(RuntimeError, match="injected retirement failure"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors=executors,
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )

    failed = load_node_group_migration_receipt(path)
    assert failed is not None
    assert failed.recovery_mode == "forward-only"
    assert failed.phases[4].status == "complete"
    assert failed.phases[5].status == "failed"

    completed = run_node_group_migration(
        path=path,
        intent=intent,
        phase_executors=executors,
        enter_maintenance=_enter_maintenance,
        restore_maintenance=_restore_maintenance,
        assert_fence=lambda: None,
    )
    assert completed.status == "complete"
    assert completed.recovery_mode == "complete"
    assert calls["cutover-complete"] == 1
    assert calls["source-retired"] == 2


def test_migration_is_forward_only_before_cutover_executor_can_mutate(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    cutover_started = False

    def _phase(name: str):
        def _run() -> MigrationPhaseResult:
            nonlocal cutover_started
            if name == "cutover-complete":
                cutover_started = True
                raise RuntimeError("injected cutover interruption")
            return MigrationPhaseResult(evidence={"phase": name})

        return _run

    with pytest.raises(RuntimeError, match="injected cutover interruption"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors={name: _phase(name) for name in MIGRATION_PHASES},
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )

    interrupted = load_node_group_migration_receipt(path)
    assert cutover_started is True
    assert interrupted is not None
    assert interrupted.recovery_mode == "forward-only"
    assert interrupted.phases[4].status == "failed"


def test_migration_refuses_changed_intent_on_resume(tmp_path: Path) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    executors = {
        name: (lambda name=name: MigrationPhaseResult(evidence={"phase": name}))
        for name in MIGRATION_PHASES
    }
    run_node_group_migration(
        path=path,
        intent=intent,
        phase_executors=executors,
        enter_maintenance=_enter_maintenance,
        restore_maintenance=_restore_maintenance,
        assert_fence=lambda: None,
    )

    with pytest.raises(RuntimeError, match="different frozen intent"):
        run_node_group_migration(
            path=path,
            intent=replace(intent, target_preset="different"),
            phase_executors=executors,
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore_maintenance,
            assert_fence=lambda: None,
        )


def test_migration_owns_maintenance_from_dual_placement_through_final_readiness(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    order: list[str] = []

    def _phase(name: str):
        def _run() -> MigrationPhaseResult:
            order.append(name)
            return MigrationPhaseResult(evidence={"phase": name})

        return _run

    receipt = run_node_group_migration(
        path=path,
        intent=intent,
        phase_executors={name: _phase(name) for name in MIGRATION_PHASES},
        enter_maintenance=lambda _record, _existing: (
            order.append("maintenance-enter") or {"mode": "source-scoped"}
        ),
        restore_maintenance=lambda _evidence: (
            order.append("maintenance-restore") or {"mode": "restored"}
        ),
        assert_fence=lambda: None,
    )

    assert order == [
        "replacement-configured",
        "replacement-applied",
        "replacement-ready",
        "maintenance-enter",
        "dual-placement-ready",
        "cutover-complete",
        "source-retired",
        "final-readiness",
        "maintenance-restore",
    ]
    assert receipt.maintenance == "restored"


def test_migration_retries_interrupted_maintenance_restoration_without_rerunning_phases(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = node_group_migration_receipt_path(
        tmp_path,
        instance_id=intent.instance_id,
        source_key=intent.source_key,
    )
    phase_calls: list[str] = []
    restoration_calls = 0

    def _phase(name: str):
        def _run() -> MigrationPhaseResult:
            phase_calls.append(name)
            return MigrationPhaseResult(evidence={"phase": name})

        return _run

    def _restore(_evidence):
        nonlocal restoration_calls
        restoration_calls += 1
        if restoration_calls == 1:
            raise RuntimeError("injected migration restoration interruption")
        return {"state": "restored"}

    executors = {name: _phase(name) for name in MIGRATION_PHASES}
    with pytest.raises(RuntimeError, match="restoration interruption"):
        run_node_group_migration(
            path=path,
            intent=intent,
            phase_executors=executors,
            enter_maintenance=_enter_maintenance,
            restore_maintenance=_restore,
            assert_fence=lambda: None,
        )

    interrupted = load_node_group_migration_receipt(path)
    assert interrupted is not None
    assert interrupted.status == "active"
    assert interrupted.recovery_mode == "forward-only"
    assert interrupted.maintenance == "restoring"
    assert all(phase.status == "complete" for phase in interrupted.phases)

    completed = run_node_group_migration(
        path=path,
        intent=intent,
        phase_executors=executors,
        enter_maintenance=lambda _record, _existing: pytest.fail(
            "maintenance entry must not repeat"
        ),
        restore_maintenance=_restore,
        assert_fence=lambda: None,
    )

    assert completed.status == "complete"
    assert completed.recovery_mode == "complete"
    assert completed.maintenance == "restored"
    assert restoration_calls == 2
    assert phase_calls == list(MIGRATION_PHASES)
