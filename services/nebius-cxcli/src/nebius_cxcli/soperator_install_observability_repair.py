"""Cluster-sealed native observability correction before initial check submission."""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import checks_digest, compile_checks_policy
from .soperator_install_checks_repair import _publish_binding_repair
from .soperator_install_render_repair import (
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
)
from .soperator_observability_transition import REASON, validate_render_transition
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_resolver import frozen_soperator_release_from_snapshot


def _read_receipt(path: Path, *, label: str) -> Mapping[str, Any]:
    value = read_owner_only_json(path, label=label)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


def assert_same_diagnostic_policy(previous: Any, successor: Any, values: Mapping[str, Any]) -> None:
    from .soperator_checks_policy import CHECKS_POLICY_ENV

    execution = copy.deepcopy(previous.execution_specs)

    def rebind(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("name") == CHECKS_POLICY_ENV:
                if value != {"name": CHECKS_POLICY_ENV, "value": previous.sha256}:
                    raise RuntimeError("Observability predecessor diagnostic marker changed")
                value["value"] = successor.sha256
            else:
                for child in value.values():
                    rebind(child)
        elif isinstance(value, list):
            for child in value:
                rebind(child)

    rebind(execution)
    if (
        replace(previous, values_sha256=checks_digest(values), execution_specs=execution)
        != successor
    ):
        raise RuntimeError("Observability recovery changed native diagnostic policy")


def validate_observability_frontier(receipt: Mapping[str, Any]) -> None:
    from .soperator_release_reconciler import (
        _FULL_TRANSITION_PLAN,
        SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
        _validate_existing_transition_chain,
    )

    spec = receipt.get("operation", {}).get("spec", {})
    rows = receipt.get("transitions", [])
    phases = [
        "resolve-immutable-sources",
        "establish-boot-storage-barrier",
        "apply-declarative-release",
        "wait-flux-graph",
        "apply-post-flux-manifests",
        "wait-pre-restore-product-readiness",
        "restore-infrastructure-and-scheduling-preimages",
        "wait-infrastructure-convergence",
        "validate-target-active-checks",
    ]
    if (
        receipt.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or receipt.get("status") != "recovery-required"
        or spec.get("strategy") != "install"
        or spec.get("current_release") != ""
        or spec.get("intervention_generation") != 0
        or not receipt.get("operationId")
        or len(rows) != len(phases)
        or [(r.get("phase"), r.get("status")) for r in rows]
        != [(p, "failed" if i == 8 else "complete") for i, p in enumerate(phases)]
        or rows[-1].get("failureType") != "operation-error"
        or type(rows[-1].get("failureAttempts")) is not int
        or rows[-1]["failureAttempts"] < 1
        or rows[-1].get("receiptSha256") is not None
        or receipt.get("irreversibleIntent") is not None
    ):
        raise RuntimeError("Observability recovery requires interrupted initial acceptance")
    _validate_existing_transition_chain(rows, operation_id=receipt["operationId"])
    for index, (row, (phase, mode, _, _)) in enumerate(
        zip(rows, _FULL_TRANSITION_PLAN[:9], strict=True)
    ):
        expected_id = hashlib.sha256(
            f"{receipt['operationId']}|{index}|{phase}|{mode.value}".encode()
        ).hexdigest()
        if row.get("id") != expected_id or row.get("mode") != mode.value:
            raise RuntimeError("Observability recovery transition identity changed")
    applied = rows[2]
    if receipt.get("irreversibleFrontier") != {
        "phase": "apply-declarative-release",
        "disposition": "forward-only",
        "transitionId": applied["id"],
        "transitionReceiptSha256": applied["receiptSha256"],
    }:
        raise RuntimeError("Observability recovery lost its forward-only apply receipt")


def validate_predecessor(
    receipt: Mapping[str, Any],
    scheduling: Mapping[str, Any],
    checks: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    target_ref: str,
) -> None:
    validate_observability_frontier(receipt)
    spec = receipt["operation"]["spec"]
    sha = _digest(spec)
    if (
        spec.get("target_ref") != target_ref
        or spec.get("nebius_cluster_id") != identity["cluster_id"]
        or spec.get("kubernetes_uid") != identity["kubernetes_uid"]
        or scheduling.get("schema") != "nebius-cxcli.soperator-slurm-recovery.v3"
        or scheduling.get("operationSpecSha256") != sha
        or scheduling.get("targetRef") != target_ref
        or scheduling.get("status") not in {"infrastructure-restored", "recovery-required"}
        or scheduling.get("lastCompletedStage") != "infrastructure-restored"
        or scheduling.get("actions") != []
        or not scheduling.get("infrastructureRestoreReceipt")
        or checks.get("schema") != "nebius-cxcli.soperator-checks-execution.v2"
        or checks.get("operation") != sha
        or checks.get("policy") != spec.get("checks_policy_sha256")
        or checks.get("phase") != "planned"
        or checks.get("jobs") != {}
        or checks.get("lifecyclePhase") != "maintenance"
        or checks.get("installReservationIntent") is not True
        or not checks.get("reservation")
        or not checks.get("reservationFingerprint")
        or any(k in checks for k in ("acceptance", "scheduleRelease", "installReservationHandoff"))
    ):
        raise RuntimeError("Observability recovery lost unstarted checks or scheduling authority")


def prepare_install_observability_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    local_scheduling_journal: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    slurm: Callable[[str], str] | None = None,
    candidate: Mapping[str, bytes] | None = None,
    config: Mapping[str, Any] | None = None,
    render_inputs: Mapping[str, Any] | None = None,
    identity: Mapping[str, str] | None = None,
    chart_inputs: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    repair_path = paths.reports_dir / f"soperator-install-observability-repair-{target_ref}.json"
    if candidate is None and not repair_path.exists():
        return None
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = _read_receipt(repair_path, label="Observability install repair")
        if (
            saved.get("schema") != REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("Observability repair lost its exact replacement files")
        _bind_repair_admission(
            saved,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )
        return saved
    if candidate is None:
        return None
    if (
        scheduling_journal != local_scheduling_journal
        or slurm is None
        or config is None
        or identity is None
    ):
        raise RuntimeError("Observability recovery lost its cluster scheduling predecessor")
    sha = scheduling_journal["operationSpecSha256"]
    receipts = [
        row
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if _digest(
            (row := _read_receipt(path, label="Observability predecessor"))
            .get("operation", {})
            .get("spec")
        )
        == sha
    ]
    if len(receipts) != 1:
        raise RuntimeError("Observability recovery requires one exact release predecessor")
    predecessor = receipts[0]
    spec = predecessor["operation"]["spec"]
    check_path = paths.reports_dir / f"soperator-checks-{sha.removeprefix('sha256:')[:24]}.json"
    checks = _read_receipt(check_path, label="Observability checks predecessor")
    validate_predecessor(
        predecessor, scheduling_journal, checks, target_ref=target_ref, identity=identity
    )
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    hashes = _file_hashes(existing)
    if (
        spec["desired_values_sha256"] != hashes[VALUES_FILE]
        or spec["adapter_sha256"] != hashes["soperator-nebius-adapter.yaml"]
        or spec["release_snapshot_sha256"] != snapshot.snapshot_sha256
    ):
        raise RuntimeError("Observability recovery changed operation-bound artifacts")
    validate_render_transition(
        existing,
        candidate,
        frozen=frozen,
        config=config,
        target_ref=target_ref,
        render_inputs=render_inputs or {},
        chart_inputs=chart_inputs,
    )
    before = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    after = yaml.safe_load(_documents(candidate[VALUES_FILE])[0]["data"]["values.yaml"])
    policy = compile_checks_policy(Path(frozen.source.source_dir), before)
    successor_policy = compile_checks_policy(Path(frozen.source.source_dir), after)
    assert_same_diagnostic_policy(policy, successor_policy, after)

    def no_kubernetes(*args: Any) -> Any:
        raise RuntimeError("Observability reservation admission cannot mutate Kubernetes")

    runner = SoperatorChecksExecution(
        policy=policy,
        operation_id=sha,
        receipt_path=check_path,
        kubernetes=no_kubernetes,
        slurm=slurm,
        assert_authority=assert_authority,
    )
    reservation = runner._reservation(checks["reservation"])
    if (
        reservation["users"] != ["root"]
        or reservation["fingerprint"] != checks["reservationFingerprint"]
    ):
        raise RuntimeError("Observability recovery lost the retained reservation")
    if _read_receipt(check_path, label="Observability checks predecessor") != checks:
        raise RuntimeError("Observability checks changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=None,
        reason=REASON,
        repair_path=repair_path,
        child_key="observability",
        child_evidence={},
        hook_evidence=None,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
        additional_evidence={
            "predecessorFiles": {k: base64.b64encode(v).decode() for k, v in existing.items()},
            "checksPredecessor": copy.deepcopy(checks),
            "reservationHandoff": {
                "operation": sha,
                "receiptSha256": _digest(checks),
                "policy": policy.sha256,
                "predecessorPolicy": policy.sha256,
                "successorPolicy": successor_policy.sha256,
                "reservation": reservation["name"],
                "fingerprint": reservation["fingerprint"],
            },
        },
    )


def observability_reservation_handoff(repair: Mapping[str, Any], policy: Any) -> Mapping[str, Any]:
    handoff = repair["reservationHandoff"]
    if handoff["successorPolicy"] != policy.sha256:
        raise RuntimeError("Observability successor diagnostic policy changed")
    return {**handoff, "policy": policy.sha256}


def _validate_successor(spec: Mapping[str, Any], repair: Mapping[str, Any]) -> None:
    previous = repair["predecessorReceipt"]["operation"]["spec"]
    mutable = {
        "desired_values_sha256",
        "admission_sha256",
        "stage_plan_sha256",
        "checks_policy_sha256",
        "intervention_generation",
    }
    if (
        repair.get("schema") != REASON
        or repair["predecessorReceiptSha256"] != _digest(repair["predecessorReceipt"])
        or repair["previousOperationSpecSha256"] != _digest(previous)
        or {k: v for k, v in spec.items() if k not in mutable}
        != {k: v for k, v in previous.items() if k not in mutable}
        or spec["intervention_generation"] != previous["intervention_generation"] + 1
        or spec["desired_values_sha256"] != repair["replacementFiles"][VALUES_FILE]
        or spec["checks_policy_sha256"] != repair["reservationHandoff"]["successorPolicy"]
        or spec["admission_sha256"] != _digest({"installObservabilityRepair": repair})
    ):
        raise RuntimeError("Observability successor intent changed its sealed operation lineage")


def historical_observability_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
) -> bool:
    """Keep a completed repair historical when a separate install owns recovery."""
    from .soperator_release_reconciler import (
        _FULL_TRANSITION_PLAN,
        SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
        _validate_existing_transition_chain,
    )

    path = paths.reports_dir / f"soperator-install-observability-repair-{target_ref}.json"
    if not path.exists():
        return False
    repair = _read_receipt(path, label="Observability install repair")
    current_sha = scheduling_journal.get("operationSpecSha256")
    if current_sha == repair.get("previousOperationSpecSha256"):
        return False
    intent_path = (
        paths.reports_dir / f"soperator-recovery-observability-successor-{target_ref}.json"
    )
    intent = _read_receipt(intent_path, label="Observability successor intent")
    successor = intent.get("spec", {})
    if (
        intent.get("schema") != REASON + ".successor"
        or intent.get("repairSha256") != _digest(repair)
        or repair.get("targetRef") != target_ref
        or successor.get("target_ref") != target_ref
    ):
        raise RuntimeError("Historical observability repair lost its successor seal")
    _validate_successor(successor, repair)
    successor_sha = _digest(successor)
    if current_sha == successor_sha:
        return False
    receipts = [
        _read_receipt(p, label="Install operation receipt")
        for p in paths.reports_dir.glob("soperator-release-reconcile-*.json")
    ]
    completed = [
        r for r in receipts if _digest(r.get("operation", {}).get("spec")) == successor_sha
    ]
    current = [r for r in receipts if _digest(r.get("operation", {}).get("spec")) == current_sha]
    if len(completed) != 1 or len(current) != 1:
        raise RuntimeError(
            "Historical observability repair requires exact distinct operation receipts"
        )
    previous, active = completed[0], current[0]
    active_spec = active["operation"]["spec"]
    independent = {
        "admission_sha256",
        "infrastructure_plan_sha256",
        "intervention_generation",
        "scheduling_sha256",
    }
    if (
        previous.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or active.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or previous.get("status") != "complete"
        or active.get("status") not in {"running", "recovery-required"}
        or active_spec.get("strategy") != "install"
        or active_spec.get("current_release") != ""
        or active_spec.get("intervention_generation") != 0
        or active_spec.get("admission_sha256") != _digest({"mode": "not-required"})
        or {k: v for k, v in active_spec.items() if k not in independent}
        != {k: v for k, v in successor.items() if k not in independent}
        or previous.get("repairLineage", {}).get("predecessorOperationSpecSha256")
        != repair["previousOperationSpecSha256"]
        or previous.get("repairLineage", {}).get("reason") != REASON
        or [(r.get("phase"), r.get("status")) for r in previous.get("transitions", [])]
        != [(phase, "complete") for phase, *_ in _FULL_TRANSITION_PLAN]
        or repair.get("replacementFiles") != _file_hashes(_files(paths.flux_dir))
    ):
        raise RuntimeError(
            "Historical observability repair does not prove completed matching inputs"
        )
    _validate_existing_transition_chain(
        previous["transitions"], operation_id=previous["operationId"]
    )
    _validate_existing_transition_chain(active["transitions"], operation_id=active["operationId"])
    _bind_repair_admission(
        repair, env=env, kube_context=kube_context, assert_authority=assert_authority, create=False
    )
    return True


def seal_successor_intent(
    paths: ProjectPaths,
    spec: Any,
    repair: Mapping[str, Any],
    assert_authority: Callable[[], object],
) -> None:
    """Checkpoint before rebinding scheduling, so an interrupted handoff is recoverable."""
    from .soperator_receipt_io import write_owner_only_json

    payload = asdict(spec)
    _validate_successor(payload, repair)
    path = paths.reports_dir / f"soperator-recovery-observability-successor-{spec.target_ref}.json"
    intent = {"schema": REASON + ".successor", "spec": payload, "repairSha256": _digest(repair)}
    if path.exists():
        if _read_receipt(path, label="Observability successor intent") != intent:
            raise RuntimeError("Observability successor intent differs from its checkpoint")
        return
    assert_authority()
    write_owner_only_json(path, intent)


def pending_infrastructure_identity(
    reports_dir: Path, *, target_ref: str, cluster_id: str, operation_spec_sha256: str
) -> str | None:
    """Recover the gap after scheduling rebind and before successor receipt creation."""
    path = reports_dir / f"soperator-recovery-observability-successor-{target_ref}.json"
    if not path.exists():
        return None
    intent = _read_receipt(path, label="Observability successor intent")
    repair = _read_receipt(
        reports_dir / f"soperator-install-observability-repair-{target_ref}.json",
        label="Observability repair",
    )
    spec = intent.get("spec", {})
    if (
        intent.get("schema") != REASON + ".successor"
        or intent.get("repairSha256") != _digest(repair)
        or _digest(spec) != operation_spec_sha256
        or spec.get("target_ref") != target_ref
        or spec.get("nebius_cluster_id") != cluster_id
    ):
        raise RuntimeError("Observability successor intent lost its scheduling identity")
    _validate_successor(spec, repair)
    return str(spec["infrastructure_plan_sha256"])
