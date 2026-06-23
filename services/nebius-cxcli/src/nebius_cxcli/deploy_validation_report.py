"""Deploy validation result aggregation helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEPLOY_REPORT_FILENAME = "deploy-report.md"
_LEGACY_VALIDATION_MARKDOWN_FILENAME = "deploy-validation-report.md"
_DEPLOY_VALIDATION_REPORT_KINDS = {
    "mk8s_cluster_smoke",
    "mk8s_gpu_operator_readiness",
    "mk8s_gpu_visibility",
    "mk8s_observability_ingestion",
    "mysterybox_eso_connectivity",
    "soperator_cluster_smoke",
}


@dataclass(frozen=True)
class DeployValidationCheck:
    name: str
    status: str
    summary: str


@dataclass(frozen=True)
class DeployValidationResult:
    kind: str
    name: str
    status: str
    report_path: Path
    report_exists: bool
    summary: str
    target_ref: str = ""
    footer_summary: str = ""
    checks: tuple[DeployValidationCheck, ...] = ()


@dataclass(frozen=True)
class DeployValidationReport:
    markdown_path: Path
    overall_status: str
    total_count: int
    completed_count: int
    passed_count: int
    failed_count: int
    not_run_count: int
    results: tuple[DeployValidationResult, ...]


def deploy_report_path(reports_dir: Path) -> Path:
    return reports_dir / DEPLOY_REPORT_FILENAME


def clear_deploy_validation_artifacts(
    validations: Sequence[Mapping[str, Any]],
    *,
    reports_dir: Path,
    include_markdown: bool = True,
) -> None:
    """Remove stale validation detail artifacts before a validation run."""
    if include_markdown:
        deploy_report_path(reports_dir).unlink(missing_ok=True)
        (reports_dir / _LEGACY_VALIDATION_MARKDOWN_FILENAME).unlink(missing_ok=True)
    for spec in validations:
        _validation_report_path(spec, reports_dir=reports_dir).unlink(missing_ok=True)


def build_deploy_validation_report(
    validations: Sequence[Mapping[str, Any]],
    *,
    reports_dir: Path,
    markdown_path: Path | None = None,
) -> DeployValidationReport:
    """Aggregate validation JSON detail files into one summary structure."""
    _raise_if_non_deploy_validation_report_kinds(validations)
    results = tuple(_build_validation_result(spec, reports_dir=reports_dir) for spec in validations)
    completed_count = sum(1 for item in results if item.status != "not_run")
    passed_count = sum(1 for item in results if item.status == "passed")
    failed_count = sum(1 for item in results if item.status == "failed")
    not_run_count = sum(1 for item in results if item.status == "not_run")
    if not results or completed_count == 0:
        overall_status = "not_run"
    elif failed_count:
        overall_status = "failed"
    elif not_run_count:
        overall_status = "incomplete"
    else:
        overall_status = "passed"
    return DeployValidationReport(
        markdown_path=markdown_path or deploy_report_path(reports_dir),
        overall_status=overall_status,
        total_count=len(results),
        completed_count=completed_count,
        passed_count=passed_count,
        failed_count=failed_count,
        not_run_count=not_run_count,
        results=results,
    )


def _raise_if_non_deploy_validation_report_kinds(
    validations: Sequence[Mapping[str, Any]],
) -> None:
    unsupported = sorted(
        {
            str(item.get("kind", "") or "").strip() or "<missing>"
            for item in validations
            if (str(item.get("kind", "") or "").strip() or "<missing>")
            not in _DEPLOY_VALIDATION_REPORT_KINDS
        }
    )
    if not unsupported:
        return
    raise ValueError(
        "Deploy validation reports only accept deploy-time validation kinds; "
        "unsupported kind(s): "
        + ", ".join(unsupported)
        + ". Run acceptance-test smoke or acceptance-test benchmark for acceptance-only suites."
    )


def format_deploy_validation_summary_lines(
    report: DeployValidationReport,
) -> list[str]:
    """Format a detailed text summary for diagnostics, not the deploy CLI footer."""
    lines = [
        "Deploy validation summary:",
        (
            "  Overall: "
            f"{status_label(report.overall_status)} "
            f"({report.completed_count}/{report.total_count} completed, "
            f"{report.not_run_count} not run)"
        ),
    ]
    for item in report.results:
        lines.append(f"  {status_label(item.status)} {item.name}: {item.summary}")
    lines.append(f"  Combined report: {report.markdown_path}")
    for item in report.results:
        if item.report_exists:
            lines.append(f"  JSON detail: {item.report_path}")
    return lines


def validation_section_lines(report: DeployValidationReport) -> list[str]:
    lines = [
        "## Validations",
        "",
        f"- Overall status: `{status_label(report.overall_status)}`",
        f"- Completed validations: `{report.completed_count}/{report.total_count}`",
        f"- Passed: `{report.passed_count}`",
        f"- Failed: `{report.failed_count}`",
        f"- Not run: `{report.not_run_count}`",
        "",
    ]
    for item in report.results:
        detail_name = item.report_path.name if item.report_exists else "n/a"
        lines.extend(
            [
                f"### {item.name}",
                "",
                f"- Status: `{status_label(item.status)}`",
                f"- Detail report: `{detail_name}`",
                f"- Summary: {item.summary}",
            ]
        )
        if item.checks:
            lines.append(f"- Checks ({len(item.checks)}):")
            for index, check in enumerate(item.checks, start=1):
                suffix = f": {check.summary}" if check.summary else ""
                lines.append(f"  {index}. `{status_label(check.status)}` {check.name}{suffix}")
        lines.append("")
    return lines


def status_label(status: str) -> str:
    return {
        "passed": "PASS",
        "failed": "FAIL",
        "skipped": "SKIPPED",
        "not_run": "NOT RUN",
        "incomplete": "INCOMPLETE",
    }.get(status, status.upper() if status else "UNKNOWN")


def _validation_report_path(spec: Mapping[str, Any], *, reports_dir: Path) -> Path:
    report_file = str(spec.get("report_file", "") or "").strip()
    if report_file:
        return reports_dir / report_file
    kind = str(spec.get("kind", "") or "").strip() or "validation"
    return reports_dir / f"{kind}-report.json"


def _validation_target_ref(
    spec: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
) -> str:
    for source in (payload, spec):
        if not isinstance(source, Mapping):
            continue
        target_ref = str(source.get("target_ref") or "").strip()
        if target_ref:
            return target_ref
    return ""


def _build_validation_result(
    spec: Mapping[str, Any],
    *,
    reports_dir: Path,
) -> DeployValidationResult:
    kind = str(spec.get("kind", "") or "").strip()
    report_path = _validation_report_path(spec, reports_dir=reports_dir)
    target_ref = _validation_target_ref(spec)
    if not report_path.exists():
        return DeployValidationResult(
            kind=kind,
            name=_validation_display_name(kind, spec=spec),
            status="not_run",
            report_path=report_path,
            report_exists=False,
            summary="No deploy validation results recorded yet.",
            target_ref=target_ref,
            footer_summary="No deploy validation results recorded yet.",
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        summary = f"Report could not be loaded: {exc}"
        return DeployValidationResult(
            kind=kind,
            name=_validation_display_name(kind, spec=spec),
            status="failed",
            report_path=report_path,
            report_exists=True,
            summary=summary,
            target_ref=target_ref,
            footer_summary=summary,
        )
    passed = bool(payload.get("passed"))
    target_ref = _validation_target_ref(spec, payload=payload)
    summary = _validation_summary(kind, payload)
    return DeployValidationResult(
        kind=kind,
        name=_validation_display_name(kind, spec=spec, payload=payload),
        status="passed" if passed else "failed",
        report_path=report_path,
        report_exists=True,
        summary=summary,
        target_ref=target_ref,
        footer_summary=_validation_footer_summary(kind, payload, fallback=summary),
        checks=_validation_checks(payload),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _join_summary_parts(parts: Sequence[str]) -> str:
    cleaned = [str(part).strip().rstrip(".") for part in parts if str(part).strip()]
    if not cleaned:
        return ""
    return "; ".join(cleaned) + "."


def _validation_checks(payload: Mapping[str, Any]) -> tuple[DeployValidationCheck, ...]:
    checks: list[DeployValidationCheck] = []
    for item in _list(payload.get("checks")):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        raw_status = str(item.get("status") or "").strip().lower()
        status = raw_status if raw_status in {"passed", "failed", "skipped"} else ""
        if not status:
            status = "passed" if bool(item.get("passed")) else "failed"
        checks.append(
            DeployValidationCheck(
                name=name,
                status=status,
                summary=str(item.get("summary") or "").strip(),
            )
        )
    return tuple(checks)


def _validation_summary(kind: str, payload: Mapping[str, Any]) -> str:
    error = str(payload.get("error", "") or "").strip()
    if error:
        return f"Failed before completion: {error}"
    if kind == "mk8s_gpu_operator_readiness":
        return _operator_readiness_summary(payload)
    if kind == "mk8s_cluster_smoke":
        return _cluster_smoke_summary(payload)
    if kind == "mk8s_gpu_visibility":
        return _gpu_visibility_summary(payload)
    if kind == "mk8s_observability_ingestion":
        return _observability_ingestion_summary(payload)
    if kind == "mysterybox_eso_connectivity":
        return _mysterybox_eso_connectivity_summary(payload)
    if kind == "soperator_cluster_smoke":
        return str(payload.get("summary", "") or "Soperator cluster smoke test completed.")
    validation_name = str(payload.get("validation", "") or "").strip() or "Validation"
    return f"{validation_name} completed with passed={bool(payload.get('passed'))}."


def _validation_footer_summary(
    kind: str,
    payload: Mapping[str, Any],
    *,
    fallback: str,
) -> str:
    error = str(payload.get("error", "") or "").strip()
    if error or bool(payload.get("skipped")):
        return fallback
    if kind == "mk8s_gpu_operator_readiness":
        return _operator_readiness_footer_summary(payload)
    if kind == "mk8s_cluster_smoke":
        return _cluster_smoke_summary(payload)
    return fallback


def _plural_word(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _operator_readiness_footer_summary(payload: Mapping[str, Any]) -> str:
    gpu_operator = _mapping(payload.get("gpu_operator"))
    network_operator = _mapping(payload.get("network_operator"))
    gpu_nodes = _list(gpu_operator.get("gpu_nodes"))
    gpu_node_count = len(gpu_nodes)
    node_label = f"{gpu_node_count} GPU {_plural_word(gpu_node_count, 'node')}"
    if bool(network_operator.get("required")):
        parts = [f"GPU/Network operators ready on {node_label}"]
    else:
        parts = [f"GPU Operator ready on {node_label}"]
    if bool(network_operator.get("required")):
        snapshot = _mapping(network_operator.get("device_plugin_snapshot"))
        rdma_keys = [
            str(item).strip()
            for item in _list(snapshot.get("rdma_resource_keys"))
            if str(item).strip()
        ]
        if bool(network_operator.get("rdma_required")) and rdma_keys:
            parts.append(f"RDMA {', '.join(rdma_keys)}")
    gpudirect_mode = str(payload.get("gpudirect_mode", "") or "").strip()
    if gpudirect_mode:
        parts.append(f"GPUDirect {gpudirect_mode}")
    return "; ".join(parts) + "."


def _validation_display_name(
    kind: str,
    *,
    spec: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> str:
    spec_name = str(spec.get("name", "") or "").strip()
    if kind == "mk8s_gpu_operator_readiness" and spec_name:
        return spec_name
    validation_name = (
        str(payload.get("validation", "") or "").strip() if isinstance(payload, Mapping) else ""
    )
    if kind == "mk8s_gpu_operator_readiness":
        return validation_name or spec_name or "GPU stack readiness"
    if kind == "mk8s_gpu_visibility":
        return validation_name or spec_name or "GPU visibility probe"
    return spec_name or validation_name or kind or "Validation"


def _operator_readiness_summary(payload: Mapping[str, Any]) -> str:
    gpu_operator = _mapping(payload.get("gpu_operator"))
    network_operator = _mapping(payload.get("network_operator"))
    gpu_nodes = _list(gpu_operator.get("gpu_nodes"))
    if bool(network_operator.get("required")):
        parts = [f"GPU Operator and Network Operator ready on {len(gpu_nodes)} Ready GPU node(s)"]
    else:
        parts = [f"GPU Operator ready on {len(gpu_nodes)} Ready GPU node(s)"]
    if bool(network_operator.get("required")):
        snapshot = _mapping(network_operator.get("device_plugin_snapshot"))
        rdma_keys = [
            str(item).strip()
            for item in _list(snapshot.get("rdma_resource_keys"))
            if str(item).strip()
        ]
        if bool(network_operator.get("rdma_required")):
            keys_text = ", ".join(rdma_keys) if rdma_keys else "none"
            node_count = int(snapshot.get("rdma_resource_node_count", 0) or 0)
            parts.append(f"RDMA resources {keys_text} on {node_count} Ready GPU node(s)")
        else:
            parts.append(
                "Network Operator "
                + ("ready" if bool(network_operator.get("ready")) else "not ready")
            )
    gpudirect_mode = str(payload.get("gpudirect_mode", "") or "").strip()
    if gpudirect_mode:
        parts.append(f"GPUDirect mode {gpudirect_mode}")
    return "; ".join(parts) + "."


def _cluster_smoke_summary(payload: Mapping[str, Any]) -> str:
    total = int(payload.get("total_node_count", 0) or 0)
    ready = int(payload.get("ready_node_count", 0) or 0)
    cpu_nodes = int(payload.get("cpu_node_count", 0) or 0)
    gpu_nodes = int(payload.get("ready_gpu_node_count", 0) or 0)
    allocatable_gpus = int(payload.get("allocatable_gpu_count", 0) or 0)
    parts = [
        f"{ready}/{total} Kubernetes node(s) Ready",
        f"{cpu_nodes} CPU-only node(s)",
        f"{gpu_nodes} Ready GPU node(s) advertise {allocatable_gpus} allocatable GPU(s)",
    ]
    expected_gpu_nodes = int(payload.get("expected_gpu_node_count", 0) or 0)
    if expected_gpu_nodes > 0:
        parts.append(f"configured minimum expected Ready GPU nodes: {expected_gpu_nodes}")
    return "; ".join(parts) + "."


def _gpu_visibility_summary(payload: Mapping[str, Any]) -> str:
    if bool(payload.get("skipped")):
        reason = str(payload.get("skip_reason", "") or "").strip()
        total = int(payload.get("total_gpu_node_count", 0) or 0)
        summary = f"Skipped: {reason or 'no schedulable GPU workload slot was available'}"
        if total:
            summary += f"; total Ready GPU nodes {total}"
        return summary + "."
    selected = int(payload.get("selected_node_count", 0) or 0)
    total = int(payload.get("total_gpu_node_count", 0) or 0)
    passed = int(payload.get("passed_node_count", 0) or 0)
    skipped = int(payload.get("skipped_node_count", 0) or 0)
    summary = f"{passed}/{selected} selected node(s) passed"
    if total:
        summary += f"; total Ready GPU nodes {total}"
    if skipped:
        summary += f"; skipped {skipped}"
    return summary + "."


def _observability_ingestion_summary(payload: Mapping[str, Any]) -> str:
    checks = [
        item
        for item in _list(payload.get("checks"))
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    ]
    passed = sum(1 for item in checks if bool(item.get("passed")))
    total = len(checks)
    parts = [f"{passed}/{total} check(s) passed"] if total else ["No checks recorded"]
    for check_name in ("Agent DaemonSet Ready", "Trace OTLP Service Ready"):
        summary = next(
            (
                str(item.get("summary") or "").strip()
                for item in checks
                if str(item.get("name") or "").strip() == check_name
            ),
            "",
        )
        if summary:
            parts.append(summary)
    return _join_summary_parts(parts)


def _mysterybox_eso_connectivity_summary(payload: Mapping[str, Any]) -> str:
    checks = [
        item
        for item in _list(payload.get("checks"))
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    ]
    passed = sum(1 for item in checks if bool(item.get("passed")))
    total = len(checks)
    parts = [f"{passed}/{total} check(s) passed"] if total else ["No checks recorded"]
    for check_name in ("Nebius API TLS", "ClusterSecretStore Ready", "ESO controller log scan"):
        summary = next(
            (
                str(item.get("summary") or "").strip()
                for item in checks
                if str(item.get("name") or "").strip() == check_name
            ),
            "",
        )
        if summary:
            parts.append(summary)
    external_secret_checks = [
        item
        for item in checks
        if str(item.get("name") or "").strip().startswith("ExternalSecret Ready")
    ]
    if external_secret_checks:
        ready_count = sum(1 for item in external_secret_checks if bool(item.get("passed")))
        parts.append(f"ExternalSecrets Ready {ready_count}/{len(external_secret_checks)}")
    return _join_summary_parts(parts)


__all__ = [
    "DEPLOY_REPORT_FILENAME",
    "DeployValidationCheck",
    "DeployValidationReport",
    "DeployValidationResult",
    "build_deploy_validation_report",
    "clear_deploy_validation_artifacts",
    "deploy_report_path",
    "format_deploy_validation_summary_lines",
    "status_label",
    "validation_section_lines",
]
