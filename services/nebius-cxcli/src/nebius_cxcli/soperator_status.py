"""Read-only local operation and recovery status for Soperator targets."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import ProjectPaths
from .sdk_auth import acquire_operator_access_token
from .soperator_destroy import SOPERATOR_DESTROY_SCHEMA
from .soperator_full_stack_upgrade import campaign_receipt_path, load_campaign_receipt
from .soperator_operation import SOPERATOR_RELEASE_INTENT_SCHEMA
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_release_reconciler import SOPERATOR_RECONCILE_RECEIPT_SCHEMA
from .soperator_telemetry import (
    SoperatorObservabilityFailure,
    SoperatorObservabilityReceipt,
    SoperatorObservabilityScope,
    failed_soperator_observability_receipt,
    select_soperator_observability_slurm_cluster,
    select_soperator_observability_workload,
    verify_soperator_observability,
)


@dataclass(frozen=True)
class SoperatorOperationStatus:
    operation: str
    status: str
    phase: str
    receipt_path: Path
    resume_command: str
    classification: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SoperatorCompletedUpgradeEvidence:
    """Read-only evidence retained by the last completed full-stack campaign."""

    receipt_path: Path
    ownership: str
    backend: str
    target_release: str
    target_kubernetes_version: str
    provider_compatibility: tuple[str, ...]
    gpu_runtime: tuple[str, ...]


@dataclass(frozen=True)
class SoperatorLiveStatusContext:
    snapshot: Mapping[str, Any]
    kube_context: str
    extra_env: Mapping[str, str]
    cluster_id: str


@dataclass(frozen=True)
class SoperatorObservabilityVerificationResult:
    receipt: SoperatorObservabilityReceipt
    receipt_path: Path


def _observability_receipt_path(
    *,
    paths: ProjectPaths,
    target_ref: str,
    verification_id: str,
) -> Path:
    target_token = hashlib.sha256(target_ref.encode("utf-8")).hexdigest()[:16]
    return paths.reports_dir / f"soperator-observability-{target_token}-{verification_id}.json"


def run_soperator_observability_verification(
    *,
    paths: ProjectPaths,
    target_ref: str,
    project_id: str,
    release: str,
    live_context: SoperatorLiveStatusContext,
    interactive: bool,
    command_runner: Callable[..., object],
    token_acquirer: Callable[..., str] = acquire_operator_access_token,
    verifier: Callable[..., SoperatorObservabilityReceipt] = verify_soperator_observability,
    emit: Callable[[str], None] | None = None,
) -> SoperatorObservabilityVerificationResult:
    """Run and persist one isolated read-only observability verification."""

    verification_id = secrets.token_hex(12)
    verification_started_at = time.time()
    receipt_path = _observability_receipt_path(
        paths=paths,
        target_ref=target_ref,
        verification_id=verification_id,
    )
    scope: SoperatorObservabilityScope | None = None
    try:
        if not project_id or not live_context.cluster_id:
            raise SoperatorObservabilityFailure(
                "evidence-missing",
                "Soperator observability requires exact project and cluster identity",
            )
        try:
            result = command_runner(
                [
                    "kubectl",
                    "--context",
                    live_context.kube_context,
                    "get",
                    "deployments.apps,replicasets.apps,pods",
                    "-A",
                    "-o",
                    "json",
                ],
                timeout_seconds=60,
                check=True,
                extra_env=dict(live_context.extra_env),
            )
            workload_inventory = json.loads(str(getattr(result, "stdout", "") or "{}"))
            if not isinstance(workload_inventory, Mapping):
                raise ValueError("workload inventory is not an object")
        except Exception as exc:
            raise SoperatorObservabilityFailure(
                "evidence-missing",
                "Soperator observability workload inventory is unavailable",
            ) from exc
        workload = select_soperator_observability_workload(
            workload_inventory,
            release=release,
        )
        slurm_cluster = select_soperator_observability_slurm_cluster(live_context.snapshot)
        scope = SoperatorObservabilityScope(
            project_id=project_id,
            cluster_id=live_context.cluster_id,
            namespace=workload.namespace,
            slurm_cluster=slurm_cluster,
            release=release,
            target_ref=target_ref,
            verification_started_at=verification_started_at,
            metric_not_before=verification_started_at - 300,
            log_not_before=workload.pod_started_at,
            pod_name=workload.pod_name,
            pod_uid=workload.pod_uid,
            container_name=workload.container_name,
        )
        try:
            token = token_acquirer(
                interactive=interactive,
                context="Soperator observability verification",
            )
        except Exception as exc:
            raise SoperatorObservabilityFailure(
                "authentication-unavailable",
                "Operator IAM authentication is unavailable for observability verification",
            ) from exc
        receipt = verifier(
            scope,
            verification_id=verification_id,
            token=token,
            emit=emit,
        )
    except SoperatorObservabilityFailure as exc:
        receipt = failed_soperator_observability_receipt(
            verification_id=verification_id,
            target_ref=target_ref,
            release=release,
            verification_started_at=verification_started_at,
            failure_code=exc.code,
            http_status=exc.http_status,
            attempts=exc.attempts,
            scope=scope,
        )
    except Exception:
        receipt = failed_soperator_observability_receipt(
            verification_id=verification_id,
            target_ref=target_ref,
            release=release,
            verification_started_at=verification_started_at,
            failure_code="backend-unavailable",
            scope=scope,
        )
    write_owner_only_json(receipt_path, asdict(receipt))
    return SoperatorObservabilityVerificationResult(
        receipt=receipt,
        receipt_path=receipt_path,
    )


def _read_owner_only_json(path: Path) -> Mapping[str, object]:
    payload = read_owner_only_json(
        path,
        label=f"Soperator operation receipt {path.name}",
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"Soperator operation receipt is malformed: {path.name}")
    return payload


def _command(*items: object) -> str:
    return " ".join(shlex.quote(str(item)) for item in items)


def _target_matches(payload: Mapping[str, object], target_ref: str) -> bool:
    target = payload.get("target")
    if isinstance(target, Mapping):
        return str(target.get("ref") or "").strip() == target_ref
    return str(payload.get("target_ref") or "").strip() == target_ref


def _latest_matching_receipt(
    paths: ProjectPaths,
    *,
    pattern: str,
    target_ref: str,
    schema: str,
) -> tuple[Path, Mapping[str, object]] | None:
    candidates = sorted(
        paths.reports_dir.glob(pattern),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for path in candidates:
        payload = _read_owner_only_json(path)
        if payload.get("schema") == schema and _target_matches(payload, target_ref):
            return path, payload
    return None


def _reconcile_phase(payload: Mapping[str, object]) -> str:
    supervisor = payload.get("supervisor")
    if isinstance(supervisor, Mapping) and str(supervisor.get("phase") or "").strip():
        return str(supervisor["phase"]).strip()
    irreversible = payload.get("irreversibleIntent")
    if isinstance(irreversible, Mapping) and str(irreversible.get("phase") or "").strip():
        return str(irreversible["phase"]).strip()
    transitions = payload.get("transitions")
    if isinstance(transitions, list):
        for item in reversed(transitions):
            if not isinstance(item, Mapping):
                continue
            phase = str(item.get("phase") or "").strip()
            if phase and item.get("status") != "complete":
                return phase
        for item in reversed(transitions):
            if isinstance(item, Mapping) and str(item.get("phase") or "").strip():
                return str(item["phase"]).strip()
    return "operation-admission"


_SUPERVISOR_CLASSIFICATIONS = frozenset({"retrying", "safety-paused", "terminal-failed"})
_TRANSITION_CLASSIFICATIONS = frozenset({"operation-error"})


def _allowlisted_classification(value: object, *, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in allowed else "operation-error"


def _required_classification(value: object, *, operation: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", normalized):
        raise RuntimeError(
            f"failed Soperator {operation} receipt has no safe failure classification"
        )
    return normalized


def _reconcile_classification(payload: Mapping[str, object], *, status: str) -> str:
    supervisor = payload.get("supervisor")
    if isinstance(supervisor, Mapping):
        disposition = str(supervisor.get("disposition") or "").strip()
        if disposition:
            return _allowlisted_classification(
                disposition,
                allowed=_SUPERVISOR_CLASSIFICATIONS,
            )
    transitions = payload.get("transitions")
    if isinstance(transitions, list):
        for item in reversed(transitions):
            if not isinstance(item, Mapping):
                continue
            failure_type = str(item.get("failureType") or "").strip()
            if failure_type:
                return _allowlisted_classification(
                    failure_type,
                    allowed=_TRANSITION_CLASSIFICATIONS,
                )
    if status in {"recovery-required", "retrying", "safety-paused", "terminal-failed"}:
        return status
    if status == "failed":
        return "operation-error"
    return ""


def read_soperator_operation_status(
    *,
    paths: ProjectPaths,
    target_ref: str,
    include_destroy: bool = True,
) -> SoperatorOperationStatus | None:
    """Return the active local operation without modifying any receipt or cluster state."""

    normalized_target = str(target_ref or "").strip()
    config_path = paths.config_path
    if include_destroy:
        destroy = _latest_matching_receipt(
            paths,
            pattern="soperator-destroy-*.json",
            target_ref=normalized_target,
            schema=SOPERATOR_DESTROY_SCHEMA,
        )
        if destroy is not None:
            path, payload = destroy
            status = str(payload.get("status") or "unknown").strip()
            if status != "complete":
                checkpoints = payload.get("checkpoints")
                last_checkpoint = (
                    str(checkpoints[-1]).strip()
                    if isinstance(checkpoints, list) and checkpoints
                    else "planned"
                )
                detail = (
                    "The last destroy step failed; inspect the owner-only receipt before resuming."
                    if status == "failed"
                    else ""
                )
                classification = (
                    _required_classification(
                        payload.get("failure_classification"),
                        operation="destroy",
                    )
                    if status == "failed"
                    else ""
                )
                return SoperatorOperationStatus(
                    operation="destroy",
                    status=status,
                    phase=last_checkpoint,
                    receipt_path=path,
                    resume_command=_command(
                        "nebius-cxcli",
                        "soperator",
                        "destroy",
                        config_path,
                        "--target",
                        normalized_target,
                    ),
                    classification=classification,
                    detail=detail,
                )

    campaign_path = campaign_receipt_path(paths.project_dir, target_ref=normalized_target)
    campaign = load_campaign_receipt(campaign_path)
    if campaign is not None and (
        campaign.status != "complete"
        or str(campaign.supervisor.get("state") or "").strip() != "complete"
    ):
        supervisor = campaign.supervisor
        supervisor_state = str(supervisor.get("state") or "active").strip()
        status = (
            supervisor_state
            if supervisor_state
            in {"active", "pending", "running", "retrying", "safety-paused", "terminal-failed"}
            else "active"
        )
        phase = str(supervisor.get("current_segment") or "").strip()
        if not phase:
            phase = next(
                (segment.name for segment in campaign.segments if segment.status != "complete"),
                "maintenance-restoration"
                if campaign.maintenance == "restoring"
                else "campaign-admission",
            )
        disposition = str(supervisor.get("disposition") or "").strip()
        classification = (
            _allowlisted_classification(
                disposition,
                allowed=_SUPERVISOR_CLASSIFICATIONS,
            )
            if disposition
            else status
            if status in _SUPERVISOR_CLASSIFICATIONS
            else ""
        )
        campaign_intent = campaign.intent
        resume_items: list[object] = [
            "nebius-cxcli",
            "soperator",
            "upgrade",
            config_path,
            "--target",
            normalized_target,
        ]
        requested_release = str(campaign_intent.get("requested_release_selector") or "").strip()
        requested_kubernetes = str(
            campaign_intent.get("requested_kubernetes_selector") or ""
        ).strip()
        if requested_release:
            resume_items.extend(("--to-release", requested_release))
        if requested_kubernetes:
            resume_items.extend(("--to-k8s-version", requested_kubernetes))
        resume_items.extend(("--execute", "--approve", "--no-interactive"))
        ownership = str(campaign_intent.get("ownership") or "unknown").strip()
        backend = str(campaign_intent.get("backend") or "unknown").strip()
        raw_compatibility = campaign_intent.get("compatibility_rows")
        compatibility_rows = raw_compatibility if isinstance(raw_compatibility, list) else []
        compatibility_summary = ", ".join(
            f"{str(item.get('group_key') or 'unknown')}@"
            f"{str(item.get('kubernetes_version') or 'unknown')}="
            f"{str(item.get('os') or 'unknown')}/"
            f"{str(item.get('drivers_preset') or 'driverless/operator-managed')}"
            for item in compatibility_rows
            if isinstance(item, Mapping)
        )
        final_readiness = next(
            (segment for segment in campaign.segments if segment.name == "final-readiness"),
            None,
        )
        runtime_summary = ""
        if final_readiness is not None:
            raw_runtime = final_readiness.evidence.get("gpuRuntimeEvidence")
            if isinstance(raw_runtime, Mapping):
                runtime_summary = ", ".join(
                    f"{key}={value}" for key, value in sorted(raw_runtime.items())
                )
        detail_parts = (
            [
                "The completed campaign's last-known-good evidence is retained, but its "
                "final readiness revalidation is not complete; resume the same frozen intent.",
                f"Backend: {ownership}/{backend}.",
            ]
            if campaign.status == "complete"
            else [
                "The full-stack campaign remains authoritative; resume the same frozen "
                "backend, release, Kubernetes path, GPU stack, CUDA target, and "
                "maintenance journal.",
                f"Backend: {ownership}/{backend}.",
            ]
        )
        if compatibility_summary:
            detail_parts.append(f"Frozen provider compatibility: {compatibility_summary}.")
        if runtime_summary:
            detail_parts.append(f"GPU runtime evidence: {runtime_summary}.")
        detail = " ".join(detail_parts)
        return SoperatorOperationStatus(
            operation="upgrade",
            status=status,
            phase=phase,
            receipt_path=campaign_path,
            resume_command=_command(*resume_items),
            classification=classification,
            detail=detail,
        )

    reconcile = _latest_matching_receipt(
        paths,
        pattern="soperator-release-reconcile-*.json",
        target_ref=normalized_target,
        schema=SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
    )
    intent_receipt = _latest_matching_receipt(
        paths,
        pattern="soperator-release-intent-*.json",
        target_ref=normalized_target,
        schema=SOPERATOR_RELEASE_INTENT_SCHEMA,
    )
    intent: Mapping[str, object] | None = None
    intent_path: Path | None = None
    if intent_receipt is not None:
        intent_path, intent = intent_receipt
    if reconcile is not None:
        path, payload = reconcile
        status = str(payload.get("status") or "unknown").strip()
        if status != "complete":
            selector = str((intent or {}).get("requested_selector") or "").strip()
            resume = _command(
                "nebius-cxcli",
                "soperator",
                "upgrade",
                config_path,
                "--target",
                normalized_target,
                "--to-release",
                selector or "<frozen-release>",
                "--execute",
                "--approve",
            )
            detail = (
                "Mutation is safety-paused; re-prove authority and immutable identity before resuming."
                if status in {"safety-paused", "recovery-required"}
                else ""
            )
            return SoperatorOperationStatus(
                operation="upgrade",
                status=status,
                phase=_reconcile_phase(payload),
                receipt_path=path,
                resume_command=resume,
                classification=_reconcile_classification(payload, status=status),
                detail=detail,
            )
    if intent is not None and intent_path is not None and intent.get("status") == "active":
        selector = str(intent.get("requested_selector") or "").strip()
        return SoperatorOperationStatus(
            operation="upgrade",
            status="active",
            phase=(
                "operation-bound"
                if str(intent.get("operation_spec_sha256") or "").strip()
                else "intent-frozen"
            ),
            receipt_path=intent_path,
            resume_command=_command(
                "nebius-cxcli",
                "soperator",
                "upgrade",
                config_path,
                "--target",
                normalized_target,
                "--to-release",
                selector,
                "--execute",
                "--approve",
            ),
        )

    install_path = paths.reports_dir / "soperator-install-plan.json"
    if install_path.is_file():
        payload = _read_owner_only_json(install_path)
        if payload.get("schema") == "nebius-cxcli.soperator-install-plan.v1" and _target_matches(
            payload, normalized_target
        ):
            status = str(payload.get("status") or "unknown").strip()
            if status != "complete":
                return SoperatorOperationStatus(
                    operation="install",
                    status=status,
                    phase="saved-plan",
                    receipt_path=install_path,
                    resume_command=_command(
                        "nebius-cxcli",
                        "soperator",
                        "install",
                        config_path,
                        "--resume",
                        "--dry-run",
                    ),
                    classification=("operation-error" if status == "failed" else ""),
                    detail="Review the saved approval fingerprint before execution.",
                )
    return None


def read_soperator_completed_upgrade_evidence(
    *,
    paths: ProjectPaths,
    target_ref: str,
) -> SoperatorCompletedUpgradeEvidence | None:
    """Return last completed upgrade proof without presenting it as an active operation."""

    receipt_path = campaign_receipt_path(paths.project_dir, target_ref=target_ref)
    campaign = load_campaign_receipt(receipt_path)
    if campaign is None or campaign.status != "complete":
        return None
    intent = campaign.intent
    raw_compatibility = intent.get("compatibility_rows")
    compatibility_rows = raw_compatibility if isinstance(raw_compatibility, list) else []
    provider_compatibility = tuple(
        f"{str(item.get('group_key') or 'unknown')}@"
        f"{str(item.get('kubernetes_version') or 'unknown')}="
        f"{str(item.get('os') or 'unknown')}/"
        f"{str(item.get('drivers_preset') or 'driverless/operator-managed')}"
        for item in compatibility_rows
        if isinstance(item, Mapping)
    )
    final_readiness = next(
        (segment for segment in campaign.segments if segment.name == "final-readiness"),
        None,
    )
    runtime_entries: list[str] = []
    if final_readiness is not None:
        raw_runtime = final_readiness.evidence.get("gpuRuntimeEvidence")
        runtime = raw_runtime if isinstance(raw_runtime, Mapping) else {}
        raw_reports = final_readiness.evidence.get("gpuRuntimeValidationReports")
        reports = raw_reports if isinstance(raw_reports, Mapping) else {}
        for group, state in sorted(runtime.items()):
            entry = f"{group}={state}"
            report = reports.get(group)
            if isinstance(report, Mapping):
                selected = report.get("selectedNodeCount")
                passed = report.get("passedNodeCount")
                report_file = str(report.get("reportFile") or "").strip()
                report_sha256 = str(report.get("reportSha256") or "").strip()
                details = []
                if isinstance(selected, int) and isinstance(passed, int):
                    details.append(f"nodes {passed}/{selected}")
                if report_file:
                    details.append(f"report {report_file}")
                if report_sha256:
                    details.append(f"digest {report_sha256}")
                if details:
                    entry += " (" + "; ".join(details) + ")"
            runtime_entries.append(entry)
    return SoperatorCompletedUpgradeEvidence(
        receipt_path=receipt_path,
        ownership=str(intent.get("ownership") or "unknown").strip(),
        backend=str(intent.get("backend") or "unknown").strip(),
        target_release=str(intent.get("target_release") or "unknown").strip(),
        target_kubernetes_version=str(intent.get("target_kubernetes_version") or "unknown").strip(),
        provider_compatibility=provider_compatibility,
        gpu_runtime=tuple(runtime_entries),
    )


__all__ = [
    "SoperatorLiveStatusContext",
    "SoperatorObservabilityVerificationResult",
    "SoperatorOperationStatus",
    "read_soperator_operation_status",
    "run_soperator_observability_verification",
]
