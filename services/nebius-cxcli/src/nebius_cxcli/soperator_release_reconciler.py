"""One evidence-producing in-cluster reconciler for Soperator releases."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from pathlib import Path

from .paths import ProjectPaths
from .soperator_failures import SoperatorFailureDisposition
from .soperator_operation import SoperatorOperationSpec, soperator_stage_plan_sha256
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_release import SoperatorReleaseSnapshot
from .soperator_release_artifacts import SoperatorArtifactReceipt
from .soperator_release_source import SOPERATOR_SOURCE_CACHE_SCHEMA, SoperatorSourceReceipt
from .soperator_strategy import SoperatorStrategy, SoperatorStrategyPlan, plan_soperator_strategy

SOPERATOR_RECONCILE_RECEIPT_SCHEMA = "nebius-cxcli.soperator-reconcile-receipt.v6"
SOPERATOR_RECONCILE_RECEIPT_FILENAME = "soperator-release-reconcile.json"
SOPERATOR_RECONCILE_MAX_FAILURES = 3
SOPERATOR_RECONCILE_REPAIR_LINEAGE_SCHEMA = "nebius-cxcli.soperator-reconcile-repair-lineage.v1"
_ADMITTED_RENDER_REPAIR_REASONS = frozenset(
    {
        "controller-spool-post-render-default-removal-v1",
        "controller-spool-upstream-default-shadow-v1",
        "controller-storage-render-contract-v2",
        "controller-storage-render-contract-v3",
        "controller-spool-receipt-writer-v1",
        "registration-topology-repair-v1",
        "registered-operator-capability-repair-v1",
        "registered-partition-output-sentinel-repair-v1",
        "registered-runtime-shape-repair-v1",
        "registered-scheduling-runtime-repair-v1",
        "registered-static-partition-repair-v1",
        "registered-static-worker-rollout-v1",
        "registered-topology-disable-repair-v1",
        "victoria-metrics-install-retry-v1",
    }
)


@dataclass(frozen=True)
class SoperatorReconcileExecutionPolicy:
    """Caller-selected execution semantics for a release reconciliation."""

    forward_until_complete: bool = False
    retry_initial_seconds: float = 5.0
    retry_max_seconds: float = 60.0
    sleep: Callable[[float], None] = time.sleep
    classify_failure: Callable[[str, Exception], SoperatorFailureDisposition] | None = None
    observe_advisory: Callable[[str], object] | None = None


@dataclass(frozen=True)
class SoperatorReconcileRepairLineage:
    """Authenticated predecessor authority for one admitted repair generation."""

    predecessor_receipt: Mapping[str, object]
    previous_operation_spec_sha256: str
    resume_phase: str
    reason: str
    discarded_replay_receipt_sha256: str = ""


@dataclass(frozen=True)
class SoperatorReconcileCallbacks:
    assert_authority: Callable[[], object]
    resolve_sources: Callable[[], object]
    establish_storage: Callable[[], object]
    apply_desired_state: Callable[[], object]
    wait_flux: Callable[[], object]
    apply_post_flux: Callable[[], object]
    wait_pre_restore_product: Callable[[], object]
    restore_infrastructure: Callable[[], object]
    wait_infrastructure: Callable[[], object]
    wait_restored_product: Callable[[], object]
    release_requeued_jobs: Callable[[], object]
    wait_requeued_product: Callable[[], object]
    release_held_jobs: Callable[[], object]
    wait_final_product: Callable[[], object]
    capture_protected_state: Callable[[], object] | None = None
    classify_rootfs: Callable[[], object] | None = None
    enforce_retention: Callable[[], object] | None = None
    prepare_passive_rootfs: Callable[[], object] | None = None
    quiesce_legacy_owners: Callable[[], object] | None = None
    verify_single_writer: Callable[[], object] | None = None
    adopt_protected_state: Callable[[], object] | None = None
    verify_protected_state: Callable[[], object] | None = None
    verify_rootfs_consumers: Callable[[], object] | None = None
    retire_legacy_owners: Callable[[], object] | None = None
    rollback_before_frontier: Callable[[], object] | None = None
    completed_postconditions: Mapping[str, Callable[[Mapping[str, object]], object]] = field(
        default_factory=dict
    )
    interrupted_recovery: Mapping[str, Callable[[Mapping[str, object]], object]] = field(
        default_factory=dict
    )
    result_hydrators: Mapping[str, Callable[[Mapping[str, object]], object]] = field(
        default_factory=dict
    )


class TransitionMode(StrEnum):
    """Execution semantics for one receipt-bound release transition."""

    OBSERVE = "observe"
    VERIFY = "verify"
    RECONCILE_FORWARD = "reconcile-forward"
    MUTATE_ONCE = "mutate-once"


@dataclass(frozen=True)
class _TransitionDefinition:
    phase: str
    mode: TransitionMode
    action: Callable[[], object]
    irreversible: bool = False


_FULL_TRANSITION_PLAN = (
    ("resolve-immutable-sources", TransitionMode.VERIFY, "resolve_sources", False),
    (
        "establish-boot-storage-barrier",
        TransitionMode.RECONCILE_FORWARD,
        "establish_storage",
        False,
    ),
    (
        "apply-declarative-release",
        TransitionMode.RECONCILE_FORWARD,
        "apply_desired_state",
        True,
    ),
    ("wait-flux-graph", TransitionMode.OBSERVE, "wait_flux", False),
    (
        "apply-post-flux-manifests",
        TransitionMode.RECONCILE_FORWARD,
        "apply_post_flux",
        True,
    ),
    (
        "wait-pre-restore-product-readiness",
        TransitionMode.OBSERVE,
        "wait_pre_restore_product",
        False,
    ),
    (
        "restore-infrastructure-and-scheduling-preimages",
        TransitionMode.MUTATE_ONCE,
        "restore_infrastructure",
        True,
    ),
    (
        "wait-infrastructure-convergence",
        TransitionMode.OBSERVE,
        "wait_infrastructure",
        False,
    ),
    (
        "wait-restored-product-readiness",
        TransitionMode.OBSERVE,
        "wait_restored_product",
        False,
    ),
    (
        "release-requeued-running-jobs",
        TransitionMode.MUTATE_ONCE,
        "release_requeued_jobs",
        True,
    ),
    (
        "wait-post-requeue-product-readiness",
        TransitionMode.OBSERVE,
        "wait_requeued_product",
        False,
    ),
    (
        "release-other-operation-held-jobs",
        TransitionMode.MUTATE_ONCE,
        "release_held_jobs",
        True,
    ),
    ("wait-final-product-readiness", TransitionMode.OBSERVE, "wait_final_product", False),
)
_NOOP_TRANSITION_PLAN = (
    (
        "reconcile-sources-and-wait-flux-graph",
        TransitionMode.RECONCILE_FORWARD,
        "wait_flux",
        False,
    ),
    (
        "wait-infrastructure-convergence",
        TransitionMode.OBSERVE,
        "wait_infrastructure",
        False,
    ),
    (
        "wait-final-product-readiness",
        TransitionMode.OBSERVE,
        "wait_final_product",
        False,
    ),
)

_PROTECTED_DATA_PLANE_TRANSITION_PLAN = (
    ("resolve-immutable-sources", TransitionMode.VERIFY, "resolve_sources", False),
    (
        "capture-protected-data-plane",
        TransitionMode.VERIFY,
        "capture_protected_state",
        False,
    ),
    (
        "enforce-protected-volume-retention",
        TransitionMode.RECONCILE_FORWARD,
        "enforce_retention",
        False,
    ),
    (
        "establish-boot-storage-barrier",
        TransitionMode.RECONCILE_FORWARD,
        "establish_storage",
        False,
    ),
    (
        "verify-sealed-jail-rootfs-classification",
        TransitionMode.VERIFY,
        "classify_rootfs",
        False,
    ),
    (
        "populate-passive-jail-rootfs",
        TransitionMode.MUTATE_ONCE,
        "prepare_passive_rootfs",
        False,
    ),
    (
        "quiesce-legacy-release-owners",
        TransitionMode.RECONCILE_FORWARD,
        "quiesce_legacy_owners",
        False,
    ),
    (
        "apply-declarative-release",
        TransitionMode.RECONCILE_FORWARD,
        "apply_desired_state",
        True,
    ),
    ("wait-flux-graph", TransitionMode.OBSERVE, "wait_flux", False),
    (
        "apply-post-flux-manifests",
        TransitionMode.RECONCILE_FORWARD,
        "apply_post_flux",
        False,
    ),
    ("verify-single-writer", TransitionMode.VERIFY, "verify_single_writer", False),
    (
        "adopt-protected-data-plane",
        TransitionMode.VERIFY,
        "adopt_protected_state",
        False,
    ),
    (
        "wait-pre-restore-product-readiness",
        TransitionMode.OBSERVE,
        "wait_pre_restore_product",
        False,
    ),
    (
        "restore-infrastructure-and-scheduling-preimages",
        TransitionMode.MUTATE_ONCE,
        "restore_infrastructure",
        False,
    ),
    (
        "wait-infrastructure-convergence",
        TransitionMode.OBSERVE,
        "wait_infrastructure",
        False,
    ),
    (
        "verify-protected-data-plane",
        TransitionMode.VERIFY,
        "verify_protected_state",
        False,
    ),
    (
        "verify-jail-rootfs-consumers",
        TransitionMode.VERIFY,
        "verify_rootfs_consumers",
        False,
    ),
    (
        "wait-restored-product-readiness",
        TransitionMode.OBSERVE,
        "wait_restored_product",
        False,
    ),
    (
        "retire-legacy-release-owners",
        TransitionMode.MUTATE_ONCE,
        "retire_legacy_owners",
        False,
    ),
    (
        "release-requeued-running-jobs",
        TransitionMode.MUTATE_ONCE,
        "release_requeued_jobs",
        False,
    ),
    (
        "wait-post-requeue-product-readiness",
        TransitionMode.OBSERVE,
        "wait_requeued_product",
        False,
    ),
    (
        "release-other-operation-held-jobs",
        TransitionMode.MUTATE_ONCE,
        "release_held_jobs",
        False,
    ),
    ("wait-final-product-readiness", TransitionMode.OBSERVE, "wait_final_product", False),
)


def _transition_plan(
    strategy: SoperatorStrategy,
) -> tuple[tuple[str, TransitionMode, str, bool], ...]:
    if strategy is SoperatorStrategy.NOOP:
        return _NOOP_TRANSITION_PLAN
    if strategy is SoperatorStrategy.PROTECTED_DATA_PLANE:
        return _PROTECTED_DATA_PLANE_TRANSITION_PLAN
    return _FULL_TRANSITION_PLAN


def soperator_reconcile_stage_plan_sha256(*, strategy: str, rendered_graph_sha256: str) -> str:
    """Bind the rendered release graph and exact reconcile phase order."""

    try:
        resolved_strategy = SoperatorStrategy(strategy)
    except ValueError as exc:
        raise ValueError(f"unknown Soperator reconcile strategy: {strategy}") from exc
    return _stable_sha256(
        {
            "renderedGraphSha256": rendered_graph_sha256,
            "transitions": [
                {"phase": phase, "mode": mode.value, "irreversible": irreversible}
                for phase, mode, _callback, irreversible in _transition_plan(resolved_strategy)
            ],
        }
    )


def _transition_definitions(
    callbacks: SoperatorReconcileCallbacks,
    strategy: SoperatorStrategy,
) -> tuple[_TransitionDefinition, ...]:
    definitions: list[_TransitionDefinition] = []
    for phase, mode, callback_name, irreversible in _transition_plan(strategy):
        action = getattr(callbacks, callback_name)
        if action is None:
            raise ValueError(
                f"Soperator strategy {strategy.value} requires callback {callback_name}"
            )
        definitions.append(
            _TransitionDefinition(
                phase=phase,
                mode=mode,
                action=action,
                irreversible=irreversible,
            )
        )
    return tuple(definitions)


def resolve_soperator_reconcile_strategy(
    *,
    current_release: str | None,
    target_release: str,
    source_contract: str | None,
    target_contract: str,
) -> SoperatorStrategyPlan:
    """Resolve one reviewed capability transition; reject unknown contracts."""

    return plan_soperator_strategy(
        source_release=current_release,
        target_release=target_release,
        source_contract=source_contract,
        target_contract=target_contract,
    )


def _safe_target_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return token[:48] or "default"


def _receipt_path(paths: ProjectPaths, target_ref: str, operation_id: str) -> Path:
    return paths.reports_dir / (
        f"soperator-release-reconcile-{_safe_target_token(target_ref)}-{operation_id[:16]}.json"
    )


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    write_owner_only_json(path, payload)


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


_EVIDENCE_FORBIDDEN_KEYS = (
    "cache",
    "command",
    "credential",
    "error",
    "secret",
    "source_dir",
    "stderr",
    "stdout",
    "url",
)


def _sanitized_evidence(value: object) -> object:
    if value is None:
        return {}
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        for key, item in value.items():
            normalized = str(key)
            lowered = normalized.lower()
            if any(token in lowered for token in _EVIDENCE_FORBIDDEN_KEYS):
                continue
            output[normalized] = _sanitized_evidence(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitized_evidence(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__}


def _transition_receipt_sha256(transition: Mapping[str, object]) -> str:
    return _stable_sha256(
        {
            key: value
            for key, value in transition.items()
            if key
            not in {
                "receiptSha256",
                "status",
                "attempts",
                "failureAttempts",
                "verificationAttempts",
            }
        }
    )


def _validate_operation_spec(spec: SoperatorOperationSpec) -> None:
    for field_name in (
        "target_ref",
        "ownership",
        "strategy",
        "target_release",
        "source_contract",
        "target_contract",
        "nebius_cluster_id",
        "kubernetes_uid",
    ):
        if not str(getattr(spec, field_name) or "").strip():
            raise ValueError(f"Soperator operation spec field {field_name} is required")
    for field_name in (
        "infrastructure_plan_sha256",
        "desired_values_sha256",
        "adapter_sha256",
        "protected_state_sha256",
        "scheduling_sha256",
        "admission_sha256",
        "release_snapshot_sha256",
        "source_capability_sha256",
        "target_capability_sha256",
        "stage_plan_sha256",
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(getattr(spec, field_name) or "")):
            raise ValueError(
                f"Soperator operation spec field {field_name} must be an exact SHA-256"
            )
    if spec.intervention_generation < 0:
        raise ValueError("Soperator operation intervention_generation must not be negative")


def _validate_existing_transition_chain(transitions: list[object], *, operation_id: str) -> None:
    predecessor_id = ""
    predecessor_receipt = _stable_sha256({"operationId": operation_id})
    for item in transitions:
        if not isinstance(item, Mapping):
            raise ValueError("existing Soperator transition evidence is invalid")
        if str(item.get("predecessorId") or "") != predecessor_id:
            raise ValueError("existing Soperator transition predecessor chain is invalid")
        if str(item.get("predecessorReceiptSha256") or "") != predecessor_receipt:
            raise ValueError("existing Soperator transition predecessor evidence is stale")
        receipt_sha = str(item.get("receiptSha256") or "")
        if item.get("status") == "complete" and receipt_sha != _transition_receipt_sha256(item):
            raise ValueError("existing Soperator transition evidence was modified")
        predecessor_id = str(item.get("id") or "")
        predecessor_receipt = receipt_sha or _transition_receipt_sha256(item)


def _repair_successor_seed(
    *,
    repair: SoperatorReconcileRepairLineage,
    payload: Mapping[str, object],
    steps: tuple[_TransitionDefinition, ...],
    operation_spec: SoperatorOperationSpec,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Validate one exact predecessor frontier and rebuild its completed prefix."""

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", repair.previous_operation_spec_sha256):
        raise ValueError("Soperator repair predecessor operation digest is invalid")
    try:
        resume_index = next(
            index for index, step in enumerate(steps) if step.phase == repair.resume_phase
        )
    except StopIteration as exc:
        raise ValueError("Soperator repair resume phase is not in the transition plan") from exc
    if repair.resume_phase != "apply-declarative-release":
        raise ValueError("Soperator repair may resume only at apply-declarative-release")
    predecessor = repair.predecessor_receipt
    predecessor_operation = predecessor.get("operation")
    predecessor_spec = (
        predecessor_operation.get("spec") if isinstance(predecessor_operation, Mapping) else None
    )
    predecessor_target = predecessor.get("target")
    predecessor_transitions = predecessor.get("transitions")
    predecessor_operation_id = str(predecessor.get("operationId") or "")
    if (
        predecessor.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or not isinstance(predecessor_operation, Mapping)
        or not isinstance(predecessor_spec, Mapping)
        or not isinstance(predecessor_target, Mapping)
        or not isinstance(predecessor_transitions, list)
        or not predecessor_operation_id
        or _stable_sha256(predecessor_spec) != repair.previous_operation_spec_sha256
    ):
        raise ValueError("Soperator repair predecessor receipt identity is invalid")
    try:
        predecessor_generation = int(predecessor_spec.get("intervention_generation"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Soperator repair predecessor generation is invalid") from exc
    if (
        operation_spec.intervention_generation < 1
        or predecessor_generation != operation_spec.intervention_generation - 1
    ):
        raise ValueError("Soperator repair predecessor generation is not contiguous")
    current_operation = payload.get("operation")
    predecessor_release = predecessor.get("release")
    current_release = payload.get("release")
    if (
        not isinstance(current_operation, Mapping)
        or not isinstance(predecessor_release, Mapping)
        or not isinstance(current_release, Mapping)
    ):
        raise ValueError("Soperator repair successor operation identity is invalid")
    if repair.reason not in _ADMITTED_RENDER_REPAIR_REASONS:
        raise ValueError("Soperator repair reason is not an admitted render repair")
    predecessor_artifact_sha256 = str(predecessor_operation.get("artifactReceiptSha256") or "")
    replacement_artifact_sha256 = str(current_operation.get("artifactReceiptSha256") or "")
    predecessor_render_sha256 = str(predecessor_release.get("umbrellaRenderSha256") or "")
    replacement_render_sha256 = str(current_release.get("umbrellaRenderSha256") or "")
    if any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        for digest in (
            predecessor_artifact_sha256,
            replacement_artifact_sha256,
            predecessor_render_sha256,
            replacement_render_sha256,
        )
    ):
        raise ValueError("Soperator repair artifact identity is invalid")
    if (
        {
            key: value
            for key, value in predecessor_operation.items()
            if key not in {"spec", "artifactReceiptSha256"}
        }
        != {
            key: value
            for key, value in current_operation.items()
            if key not in {"spec", "artifactReceiptSha256"}
        }
        or {
            key: value
            for key, value in predecessor_release.items()
            if key != "umbrellaRenderSha256"
        }
        != {key: value for key, value in current_release.items() if key != "umbrellaRenderSha256"}
        or any(
            predecessor.get(key) != payload.get(key)
            for key in ("target", "strategy", "sourceReceipt", "graph")
        )
    ):
        raise ValueError("Soperator repair predecessor immutable release identity changed")
    _validate_existing_transition_chain(
        predecessor_transitions,
        operation_id=predecessor_operation_id,
    )
    predecessor_frontier = ""
    predecessor_status = predecessor.get("status")
    running_failed_adoption_frontier = False
    if predecessor_status == "running":
        try:
            running_adoption_index = next(
                index
                for index, step in enumerate(steps)
                if step.phase == "adopt-protected-data-plane"
            )
        except StopIteration:
            running_adoption_index = -1
        running_failed_adoption_frontier = (
            running_adoption_index >= 0
            and len(predecessor_transitions) == running_adoption_index + 1
            and isinstance(predecessor_transitions[-1], Mapping)
            and predecessor_transitions[-1].get("status") == "failed"
        )
    if predecessor_status == "running" and not running_failed_adoption_frontier:
        wait_flux_index = next(
            index for index, step in enumerate(steps) if step.phase == "wait-flux-graph"
        )
        running_wait_flux_frontier = len(predecessor_transitions) == wait_flux_index + 1
        frontier_index = wait_flux_index if running_wait_flux_frontier else resume_index
        if len(predecessor_transitions) != frontier_index + 1:
            raise ValueError("Soperator repair predecessor is not at the declared resume frontier")
        for index, (item, step) in enumerate(
            zip(predecessor_transitions, steps[: frontier_index + 1], strict=True)
        ):
            if not isinstance(item, Mapping):
                raise ValueError("Soperator repair predecessor transition is invalid")
            expected_id = hashlib.sha256(
                f"{predecessor_operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
            ).hexdigest()
            expected_status = "running" if index == frontier_index else "complete"
            if (
                item.get("id") != expected_id
                or item.get("phase") != step.phase
                or item.get("mode") != step.mode.value
                or item.get("status") != expected_status
                or (
                    expected_status == "complete"
                    and item.get("receiptSha256") != _transition_receipt_sha256(item)
                )
                or (expected_status == "running" and item.get("receiptSha256") is not None)
            ):
                raise ValueError("Soperator repair predecessor transition frontier changed")
        apply_transition = predecessor_transitions[resume_index]
        assert isinstance(apply_transition, Mapping)
        if running_wait_flux_frontier:
            irreversible_frontier = predecessor.get("irreversibleFrontier")
            if (
                predecessor.get("irreversibleIntent") is not None
                or not isinstance(irreversible_frontier, Mapping)
                or irreversible_frontier.get("transitionId") != apply_transition.get("id")
                or irreversible_frontier.get("phase") != repair.resume_phase
                or irreversible_frontier.get("disposition") != "forward-only"
                or irreversible_frontier.get("transitionReceiptSha256")
                != apply_transition.get("receiptSha256")
            ):
                raise ValueError(
                    "Soperator repair predecessor running Flux-wait frontier evidence is invalid"
                )
            predecessor_frontier = "running-wait-flux-graph"
        else:
            irreversible_intent = predecessor.get("irreversibleIntent")
            if (
                predecessor.get("irreversibleFrontier") is not None
                or not isinstance(irreversible_intent, Mapping)
                or irreversible_intent.get("transitionId") != apply_transition.get("id")
                or irreversible_intent.get("phase") != repair.resume_phase
                or irreversible_intent.get("disposition") != "pending-forward-only"
            ):
                raise ValueError("Soperator repair predecessor crossed its admitted frontier")
            predecessor_frontier = "running-declarative-release"
    elif predecessor_status in {"recovery-required", "running"}:
        failed_apply_frontier = (
            predecessor_status == "recovery-required"
            and len(predecessor_transitions) == resume_index + 1
        )
        if failed_apply_frontier:
            for index, (item, step) in enumerate(
                zip(
                    predecessor_transitions,
                    steps[: resume_index + 1],
                    strict=True,
                )
            ):
                if not isinstance(item, Mapping):
                    raise ValueError("Soperator repair predecessor transition is invalid")
                expected_id = hashlib.sha256(
                    f"{predecessor_operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
                ).hexdigest()
                expected_status = "failed" if index == resume_index else "complete"
                if (
                    item.get("id") != expected_id
                    or item.get("phase") != step.phase
                    or item.get("mode") != step.mode.value
                    or item.get("status") != expected_status
                    or (
                        expected_status == "complete"
                        and item.get("receiptSha256") != _transition_receipt_sha256(item)
                    )
                    or (
                        expected_status == "failed"
                        and (
                            item.get("failureType") != "operation-error"
                            or item.get("receiptSha256") is not None
                            or int(item.get("failureAttempts") or 0) < 1
                        )
                    )
                ):
                    raise ValueError("Soperator repair predecessor transition frontier changed")
            failed_transition = predecessor_transitions[resume_index]
            assert isinstance(failed_transition, Mapping)
            irreversible_intent = predecessor.get("irreversibleIntent")
            if irreversible_intent is not None and (
                not isinstance(irreversible_intent, Mapping)
                or irreversible_intent.get("transitionId") != failed_transition.get("id")
                or irreversible_intent.get("phase") != repair.resume_phase
                or irreversible_intent.get("disposition") != "pending-forward-only"
            ):
                raise ValueError("Soperator repair predecessor failed apply intent changed")
            if predecessor.get("irreversibleFrontier") is not None:
                raise ValueError("Soperator repair predecessor crossed its failed apply frontier")
            predecessor_frontier = "failed-declarative-release-after-quiescence"
        else:
            try:
                adoption_index = next(
                    index
                    for index, step in enumerate(steps)
                    if step.phase == "adopt-protected-data-plane"
                )
            except StopIteration as exc:
                raise ValueError(
                    "Soperator repair predecessor has no protected-adoption frontier"
                ) from exc
            try:
                pre_restore_index = next(
                    index
                    for index, step in enumerate(steps)
                    if step.phase == "wait-pre-restore-product-readiness"
                )
            except StopIteration:
                pre_restore_index = -1
            try:
                restore_index = next(
                    index
                    for index, step in enumerate(steps)
                    if step.phase == "restore-infrastructure-and-scheduling-preimages"
                )
            except StopIteration:
                restore_index = -1
            failed_restore_frontier = (
                repair.reason
                in {
                    "registered-scheduling-runtime-repair-v1",
                    "registered-static-partition-repair-v1",
                }
                and restore_index >= 0
                and len(predecessor_transitions) == restore_index + 1
            )
            failed_pre_restore_frontier = (
                repair.reason == "registered-static-worker-rollout-v1"
                and pre_restore_index >= 0
                and len(predecessor_transitions) == pre_restore_index + 1
            )
            if failed_restore_frontier:
                frontier_index = restore_index
            elif failed_pre_restore_frontier:
                frontier_index = pre_restore_index
            else:
                frontier_index = adoption_index
            if len(predecessor_transitions) != frontier_index + 1:
                raise ValueError(
                    "Soperator repair predecessor is not at an admitted post-apply frontier"
                )
            for index, (item, step) in enumerate(
                zip(predecessor_transitions, steps[: frontier_index + 1], strict=True)
            ):
                if not isinstance(item, Mapping):
                    raise ValueError("Soperator repair predecessor transition is invalid")
                expected_id = hashlib.sha256(
                    f"{predecessor_operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
                ).hexdigest()
                expected_status = "failed" if index == frontier_index else "complete"
                if (
                    item.get("id") != expected_id
                    or item.get("phase") != step.phase
                    or item.get("mode") != step.mode.value
                    or item.get("status") != expected_status
                    or (
                        expected_status == "complete"
                        and item.get("receiptSha256") != _transition_receipt_sha256(item)
                    )
                    or (
                        expected_status == "failed"
                        and (
                            item.get("failureType") != "operation-error"
                            or item.get("receiptSha256") is not None
                            or (
                                failed_restore_frontier
                                and (
                                    int(item.get("failureAttempts") or 0) < 1
                                    or item.get("intentSha256") is not None
                                )
                            )
                        )
                    )
                ):
                    raise ValueError("Soperator repair predecessor transition frontier changed")
            apply_transition = predecessor_transitions[resume_index]
            assert isinstance(apply_transition, Mapping)
            irreversible_frontier = predecessor.get("irreversibleFrontier")
            if (
                predecessor.get("irreversibleIntent") is not None
                or not isinstance(irreversible_frontier, Mapping)
                or irreversible_frontier.get("transitionId") != apply_transition.get("id")
                or irreversible_frontier.get("phase") != repair.resume_phase
                or irreversible_frontier.get("disposition") != "forward-only"
                or irreversible_frontier.get("transitionReceiptSha256")
                != apply_transition.get("receiptSha256")
            ):
                raise ValueError(
                    "Soperator repair predecessor post-apply frontier evidence is invalid"
                )
            predecessor_frontier = "failed-protected-adoption-after-apply"
            if failed_restore_frontier:
                predecessor_frontier = "failed-preimage-restore-after-apply"
            elif failed_pre_restore_frontier:
                predecessor_frontier = "failed-pre-restore-readiness-after-apply"
    else:
        raise ValueError("Soperator repair predecessor receipt status is invalid")
    predecessor_receipt_sha256 = _stable_sha256(predecessor)
    lineage: dict[str, object] = {
        "schema": SOPERATOR_RECONCILE_REPAIR_LINEAGE_SCHEMA,
        "predecessorOperationId": predecessor_operation_id,
        "predecessorOperationSpecSha256": repair.previous_operation_spec_sha256,
        "predecessorReceiptSha256": predecessor_receipt_sha256,
        "predecessorFrontier": predecessor_frontier,
        "resumePhase": repair.resume_phase,
        "reason": repair.reason,
        "predecessorArtifactReceiptSha256": predecessor_artifact_sha256,
        "replacementArtifactReceiptSha256": replacement_artifact_sha256,
        "predecessorUmbrellaRenderSha256": predecessor_render_sha256,
        "replacementUmbrellaRenderSha256": replacement_render_sha256,
        "interventionGeneration": operation_spec.intervention_generation,
    }
    if repair.discarded_replay_receipt_sha256:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", repair.discarded_replay_receipt_sha256):
            raise ValueError("Soperator discarded replay receipt digest is invalid")
        lineage["discardedReplayReceiptSha256"] = repair.discarded_replay_receipt_sha256

    operation_id = str(payload.get("operationId") or "")
    predecessor_id = ""
    predecessor_receipt = _stable_sha256({"operationId": operation_id})
    imported: list[dict[str, object]] = []
    for index, (source_transition, step) in enumerate(
        zip(predecessor_transitions[:resume_index], steps[:resume_index], strict=True)
    ):
        assert isinstance(source_transition, Mapping)
        transition_id = hashlib.sha256(
            f"{operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
        ).hexdigest()
        transition: dict[str, object] = {
            "id": transition_id,
            "phase": step.phase,
            "mode": step.mode.value,
            "predecessorId": predecessor_id,
            "predecessorReceiptSha256": predecessor_receipt,
            "status": "complete",
            "attempts": 0,
            "failureAttempts": 0,
            "verificationAttempts": [],
            "evidence": _sanitized_evidence(source_transition.get("evidence")),
            "repairPredecessor": {
                "operationId": predecessor_operation_id,
                "transitionId": source_transition.get("id"),
                "transitionReceiptSha256": source_transition.get("receiptSha256"),
            },
        }
        transition["receiptSha256"] = _transition_receipt_sha256(transition)
        imported.append(transition)
        predecessor_id = transition_id
        predecessor_receipt = str(transition["receiptSha256"])
    return lineage, imported


def _validate_discarded_successor_replay(
    receipt: Mapping[str, object],
    *,
    operation_id: str,
    steps: tuple[_TransitionDefinition, ...],
) -> None:
    """Accept only the pre-fix replay that stopped inside read-only preflight."""

    transitions = receipt.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError("discarded Soperator replay transition evidence is invalid")
    _validate_existing_transition_chain(transitions, operation_id=operation_id)
    population_index = next(
        index for index, step in enumerate(steps) if step.phase == "populate-passive-jail-rootfs"
    )
    if (
        receipt.get("status") != "running"
        or receipt.get("repairLineage") is not None
        or receipt.get("irreversibleIntent") is not None
        or receipt.get("irreversibleFrontier") is not None
        or len(transitions) != population_index + 1
    ):
        raise ValueError("discarded Soperator replay crossed the safe pre-apply boundary")
    for index, (item, step) in enumerate(
        zip(transitions, steps[: population_index + 1], strict=True)
    ):
        if not isinstance(item, Mapping):
            raise ValueError("discarded Soperator replay transition is invalid")
        expected_id = hashlib.sha256(
            f"{operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
        ).hexdigest()
        expected_status = "running" if index == population_index else "complete"
        if (
            item.get("id") != expected_id
            or item.get("phase") != step.phase
            or item.get("mode") != step.mode.value
            or item.get("status") != expected_status
            or (
                expected_status == "complete"
                and item.get("receiptSha256") != _transition_receipt_sha256(item)
            )
        ):
            raise ValueError("discarded Soperator replay transition frontier changed")


def _reconcile_soperator_release_once(
    *,
    paths: ProjectPaths,
    target_ref: str,
    ownership: str,
    strategy: SoperatorStrategyPlan,
    snapshot: SoperatorReleaseSnapshot,
    source: SoperatorSourceReceipt,
    artifacts: SoperatorArtifactReceipt,
    callbacks: SoperatorReconcileCallbacks,
    operation_spec: SoperatorOperationSpec,
    emit: Callable[[str], None] | None = None,
    max_failures: int | None = SOPERATOR_RECONCILE_MAX_FAILURES,
    rollback_on_failure: bool = True,
    repair_lineage: SoperatorReconcileRepairLineage | None = None,
) -> Path:
    """Run the single ordered in-cluster path and seal its non-secret receipt."""

    normalized_ownership = str(ownership or "").strip().lower()
    if normalized_ownership not in {"managed", "onboarded"}:
        raise ValueError("Soperator reconcile ownership must be managed or onboarded")
    expected_source = (
        SOPERATOR_SOURCE_CACHE_SCHEMA,
        snapshot.release,
        snapshot.commit,
        snapshot.tree,
        snapshot.archive_sha256,
        snapshot.source_manifest_sha256,
    )
    actual_source = (
        source.schema,
        source.release,
        source.commit,
        source.tree,
        source.archive_sha256,
        source.manifest_sha256,
    )
    if actual_source != expected_source:
        raise ValueError("Soperator source receipt does not match the release snapshot")
    expected_packages = tuple(
        sorted(
            [chart.package_sha256 for chart in snapshot.charts.values()]
            + [chart.package_sha256 for chart in snapshot.third_party_charts.values()]
        )
    )
    if (
        artifacts.release != snapshot.release
        or artifacts.source_manifest_sha256 != snapshot.source_manifest_sha256
        or artifacts.chart_package_sha256 != expected_packages
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifacts.umbrella_render_sha256)
    ):
        raise ValueError("Soperator artifact receipt does not match the release snapshot")
    effective_spec = operation_spec
    _validate_operation_spec(effective_spec)
    if (
        effective_spec.target_ref != (target_ref or "default")
        or effective_spec.ownership != normalized_ownership
        or effective_spec.strategy != strategy.strategy.value
        or effective_spec.current_release != strategy.source_release
        or effective_spec.target_release != snapshot.release
        or effective_spec.source_contract != strategy.source_contract
        or effective_spec.target_contract != strategy.target_contract
        or effective_spec.target_capability_sha256 != snapshot.capability_sha256
        or (
            strategy.strategy is SoperatorStrategy.NOOP
            and effective_spec.source_capability_sha256 != effective_spec.target_capability_sha256
        )
        or effective_spec.stage_plan_sha256
        != soperator_reconcile_stage_plan_sha256(
            strategy=strategy.strategy.value,
            rendered_graph_sha256=soperator_stage_plan_sha256(paths),
        )
        or effective_spec.release_snapshot_sha256 != snapshot.snapshot_sha256
    ):
        raise ValueError("Soperator operation spec does not match the requested release operation")
    immutable_operation = {
        "spec": asdict(effective_spec),
        "releaseSnapshotSha256": _stable_sha256(
            {
                "snapshotSha256": snapshot.snapshot_sha256,
                "release": snapshot.release,
                "commit": snapshot.commit,
                "tree": snapshot.tree,
                "sourceManifestSha256": snapshot.source_manifest_sha256,
                "packages": expected_packages,
            }
        ),
        "artifactReceiptSha256": _stable_sha256(asdict(artifacts)),
    }
    operation_id = hashlib.sha256(
        json.dumps(immutable_operation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt_path = _receipt_path(paths, target_ref, operation_id)
    payload: dict[str, object] = {
        "schema": SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
        "operationId": operation_id,
        "operation": immutable_operation,
        "target": {
            "ref": target_ref or "default",
            "ownership": ownership or "unspecified",
        },
        "strategy": asdict(strategy),
        "release": {
            "version": snapshot.release,
            "tag": snapshot.tag,
            "commit": snapshot.commit,
            "tree": snapshot.tree,
            "archiveSha256": snapshot.archive_sha256,
            "sourceManifestSha256": snapshot.source_manifest_sha256,
            "snapshotSha256": snapshot.snapshot_sha256,
            "capabilityContract": snapshot.capability_contract,
            "capabilitySha256": snapshot.capability_sha256,
            "umbrellaDigest": snapshot.umbrella.digest,
            "umbrellaRenderSha256": artifacts.umbrella_render_sha256,
        },
        "sourceReceipt": {
            key: value for key, value in asdict(source).items() if key != "source_dir"
        },
        "graph": [
            {
                "releaseName": node.release_name,
                "namespace": node.namespace,
                "owner": node.owner,
                "stage": node.stage,
                "chartKey": node.chart_key,
                "dependencies": list(node.dependencies),
            }
            for node in snapshot.release_graph
        ],
        "transitions": [],
        "irreversibleFrontier": None,
        "status": "running",
    }
    steps = _transition_definitions(callbacks, strategy.strategy)
    if (
        strategy.strategy is SoperatorStrategy.PROTECTED_DATA_PLANE
        and callbacks.rollback_before_frontier is None
    ):
        raise ValueError(
            "Soperator strategy protected-data-plane requires callback rollback_before_frontier"
        )

    if repair_lineage is not None:
        if strategy.strategy is not SoperatorStrategy.PROTECTED_DATA_PLANE:
            raise ValueError("Soperator repair lineage requires protected-data-plane strategy")
        lineage_payload, imported_transitions = _repair_successor_seed(
            repair=repair_lineage,
            payload=payload,
            steps=steps,
            operation_spec=effective_spec,
        )
        payload["repairLineage"] = lineage_payload
        payload["transitions"] = imported_transitions

    if receipt_path.is_file():
        try:
            existing = read_owner_only_json(
                receipt_path,
                label="Soperator reconcile receipt",
            )
        except json.JSONDecodeError as exc:
            raise ValueError("existing Soperator reconcile receipt is invalid") from exc
        immutable_keys = (
            "schema",
            "operationId",
            "operation",
            "target",
            "strategy",
            "release",
            "graph",
        )
        if not isinstance(existing, dict) or any(
            existing.get(key) != payload.get(key) for key in immutable_keys
        ):
            raise ValueError(
                "existing Soperator reconcile receipt belongs to different immutable inputs"
            )
        if repair_lineage is not None:
            expected_lineage = payload.get("repairLineage")
            if existing.get("repairLineage") == expected_lineage:
                existing_transitions = existing.get("transitions")
                if not isinstance(existing_transitions, list):
                    raise ValueError("existing Soperator reconcile receipt transitions are invalid")
                _validate_existing_transition_chain(
                    existing_transitions,
                    operation_id=operation_id,
                )
                payload = existing
                payload["status"] = "running"
            elif (
                repair_lineage.discarded_replay_receipt_sha256
                and _stable_sha256(existing) == repair_lineage.discarded_replay_receipt_sha256
            ):
                _validate_discarded_successor_replay(
                    existing,
                    operation_id=operation_id,
                    steps=steps,
                )
            else:
                raise ValueError(
                    "existing Soperator reconcile receipt lacks authenticated repair lineage"
                )
        else:
            existing_transitions = existing.get("transitions")
            if not isinstance(existing_transitions, list):
                raise ValueError("existing Soperator reconcile receipt transitions are invalid")
            _validate_existing_transition_chain(
                existing_transitions,
                operation_id=operation_id,
            )
            payload = existing
            payload["status"] = "running"

    transitions = payload["transitions"]
    assert isinstance(transitions, list)
    transition_by_id: dict[str, dict[str, object]] = {
        str(item.get("id")): item
        for item in transitions
        if isinstance(item, dict) and str(item.get("id", ""))
    }
    _write_receipt(receipt_path, payload)
    predecessor_id = ""
    predecessor_receipt = _stable_sha256({"operationId": operation_id})
    for index, step in enumerate(steps):
        transition_id = hashlib.sha256(
            f"{operation_id}|{index}|{step.phase}|{step.mode.value}".encode()
        ).hexdigest()
        transition = transition_by_id.get(transition_id)
        if transition is None:
            transition = {
                "id": transition_id,
                "phase": step.phase,
                "mode": step.mode.value,
                "predecessorId": predecessor_id,
                "predecessorReceiptSha256": predecessor_receipt,
                "status": "pending",
                "attempts": 0,
                "failureAttempts": 0,
                "verificationAttempts": [],
            }
            transitions.append(transition)
            transition_by_id[transition_id] = transition
        elif (
            str(transition.get("predecessorId") or "") != predecessor_id
            or str(transition.get("predecessorReceiptSha256") or "") != predecessor_receipt
        ):
            raise RuntimeError(
                "recovery-required: Soperator transition predecessor authority changed"
            )
        elif (
            str(transition.get("phase") or "") != step.phase
            or str(transition.get("mode") or "") != step.mode.value
        ):
            raise RuntimeError(
                "recovery-required: Soperator transition plan changed after it was recorded"
            )

        completed = transition.get("status") == "complete"
        if completed:
            if emit is not None:
                emit(step.phase)
            verification_attempts = transition.setdefault("verificationAttempts", [])
            if not isinstance(verification_attempts, list):
                raise ValueError("existing Soperator transition verification evidence is invalid")
            completion_evidence = transition.get("evidence")
            evidence_map = completion_evidence if isinstance(completion_evidence, Mapping) else {}
            verifier = callbacks.completed_postconditions.get(step.phase)
            if (
                step.mode in {TransitionMode.RECONCILE_FORWARD, TransitionMode.MUTATE_ONCE}
                and verifier is None
            ):
                raise RuntimeError(
                    "recovery-required: completed Soperator mutation has no live "
                    f"postcondition verifier: {step.phase}"
                )
            try:
                callbacks.assert_authority()
                observed = verifier(evidence_map) if verifier is not None else step.action()
                hydration = callbacks.result_hydrators.get(step.phase)
                if hydration is not None:
                    hydration(evidence_map)
            except Exception:
                verification_attempts.append(
                    {
                        "status": "failed",
                        "kind": "live-postcondition",
                        "failureType": "operation-error",
                    }
                )
                payload["status"] = (
                    "recovery-required"
                    if step.mode in {TransitionMode.RECONCILE_FORWARD, TransitionMode.MUTATE_ONCE}
                    or payload.get("irreversibleFrontier") is not None
                    else "failed"
                )
                _write_receipt(receipt_path, payload)
                raise
            verification_attempts.append(
                {
                    "status": "passed",
                    "kind": "live-postcondition",
                    "evidence": _sanitized_evidence(observed),
                }
            )
            _write_receipt(receipt_path, payload)
            predecessor_id = transition_id
            predecessor_receipt = str(transition["receiptSha256"])
            continue

        mutating = step.mode in {TransitionMode.RECONCILE_FORWARD, TransitionMode.MUTATE_ONCE}
        if (
            mutating
            and max_failures is not None
            and int(transition.get("failureAttempts", 0)) >= max_failures
        ):
            raise RuntimeError(
                f"Soperator transition {step.phase} exhausted its bounded retry budget"
            )
        if step.irreversible and payload.get("irreversibleFrontier") is None:
            payload["irreversibleIntent"] = {
                "transitionId": transition_id,
                "phase": step.phase,
                "disposition": "pending-forward-only",
            }
        if mutating:
            transition["intent"] = {
                "mode": step.mode.value,
                "predecessorReceiptSha256": predecessor_receipt,
            }
        transition["status"] = "running"
        if mutating:
            transition["attempts"] = int(transition.get("attempts", 0)) + 1
        _write_receipt(receipt_path, payload)
        if emit is not None:
            emit(step.phase)
        try:
            callbacks.assert_authority()
            recover = callbacks.interrupted_recovery.get(step.phase)
            if step.mode is TransitionMode.MUTATE_ONCE and int(transition.get("attempts", 0)) > 1:
                if recover is None:
                    raise RuntimeError(
                        "recovery-required: interrupted one-shot Soperator mutation has no "
                        f"recovery classifier: {step.phase}"
                    )
                evidence = recover(transition)
            else:
                evidence = step.action()
        except Exception:
            transition["status"] = "failed"
            if mutating:
                transition["failureAttempts"] = int(transition.get("failureAttempts", 0)) + 1
            transition["failureType"] = "operation-error"
            payload["status"] = (
                "recovery-required"
                if payload.get("irreversibleIntent") is not None
                or payload.get("irreversibleFrontier") is not None
                else "failed"
            )
            if (
                rollback_on_failure
                and callbacks.rollback_before_frontier is not None
                and payload.get("irreversibleFrontier") is None
                and payload.get("irreversibleIntent") is None
            ):
                try:
                    callbacks.assert_authority()
                    transition["rollbackEvidence"] = _sanitized_evidence(
                        callbacks.rollback_before_frontier()
                    )
                    payload["status"] = "failed-rolled-back"
                except Exception:
                    transition["rollbackFailureType"] = "rollback-error"
                    payload["status"] = "recovery-required"
            _write_receipt(receipt_path, payload)
            raise
        transition.pop("failureType", None)
        transition["failureAttempts"] = 0
        transition["evidence"] = _sanitized_evidence(evidence)
        transition["status"] = "complete"
        transition["receiptSha256"] = _transition_receipt_sha256(transition)
        hydration = callbacks.result_hydrators.get(step.phase)
        if hydration is not None:
            hydrated_evidence = transition.get("evidence")
            hydration(hydrated_evidence if isinstance(hydrated_evidence, Mapping) else {})
        if step.irreversible and payload.get("irreversibleFrontier") is None:
            payload["irreversibleFrontier"] = {
                "transitionId": transition_id,
                "phase": step.phase,
                "disposition": "forward-only",
                "transitionReceiptSha256": transition["receiptSha256"],
            }
            payload.pop("irreversibleIntent", None)
        _write_receipt(receipt_path, payload)
        predecessor_id = transition_id
        predecessor_receipt = str(transition["receiptSha256"])
    payload["status"] = "complete"
    _write_receipt(receipt_path, payload)
    return receipt_path


def reconcile_soperator_release(
    *,
    paths: ProjectPaths,
    target_ref: str,
    ownership: str,
    strategy: SoperatorStrategyPlan,
    snapshot: SoperatorReleaseSnapshot,
    source: SoperatorSourceReceipt,
    artifacts: SoperatorArtifactReceipt,
    callbacks: SoperatorReconcileCallbacks,
    operation_spec: SoperatorOperationSpec,
    emit: Callable[[str], None] | None = None,
    execution_policy: SoperatorReconcileExecutionPolicy | None = None,
    repair_lineage: SoperatorReconcileRepairLineage | None = None,
) -> Path:
    """Run one durable reconciliation attempt under the caller-owned supervisor."""

    policy = execution_policy or SoperatorReconcileExecutionPolicy()
    return _reconcile_soperator_release_once(
        paths=paths,
        target_ref=target_ref,
        ownership=ownership,
        strategy=strategy,
        snapshot=snapshot,
        source=source,
        artifacts=artifacts,
        callbacks=callbacks,
        operation_spec=operation_spec,
        emit=emit,
        max_failures=None if policy.forward_until_complete else SOPERATOR_RECONCILE_MAX_FAILURES,
        rollback_on_failure=not policy.forward_until_complete,
        repair_lineage=repair_lineage,
    )


__all__ = [
    "SOPERATOR_RECONCILE_RECEIPT_FILENAME",
    "SOPERATOR_RECONCILE_MAX_FAILURES",
    "SOPERATOR_RECONCILE_RECEIPT_SCHEMA",
    "SoperatorReconcileCallbacks",
    "SoperatorReconcileExecutionPolicy",
    "SoperatorReconcileRepairLineage",
    "TransitionMode",
    "reconcile_soperator_release",
    "resolve_soperator_reconcile_strategy",
    "soperator_reconcile_stage_plan_sha256",
]
