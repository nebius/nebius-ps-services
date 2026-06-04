"""Deploy validation result aggregation helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEPLOY_REPORT_FILENAME = "deploy-report.md"
_LEGACY_VALIDATION_MARKDOWN_FILENAME = "deploy-validation-report.md"
_NCCL_AVG_BUS_BANDWIDTH_MARKDOWN_RE = re.compile(
    r"(average bus bandwidth )([0-9]+(?:\.[0-9]+)?)( Gbps)"
)


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


def deploy_report_path(inventory_dir: Path) -> Path:
    return inventory_dir / DEPLOY_REPORT_FILENAME


def clear_deploy_validation_artifacts(
    validations: Sequence[Mapping[str, Any]],
    *,
    inventory_dir: Path,
) -> None:
    """Remove stale validation artifacts before a new deploy run."""
    deploy_report_path(inventory_dir).unlink(missing_ok=True)
    (inventory_dir / _LEGACY_VALIDATION_MARKDOWN_FILENAME).unlink(missing_ok=True)
    for spec in validations:
        _validation_report_path(spec, inventory_dir=inventory_dir).unlink(missing_ok=True)


def build_deploy_validation_report(
    validations: Sequence[Mapping[str, Any]],
    *,
    inventory_dir: Path,
    markdown_path: Path | None = None,
) -> DeployValidationReport:
    """Aggregate validation JSON detail files into one summary structure."""
    results = tuple(
        _build_validation_result(spec, inventory_dir=inventory_dir) for spec in validations
    )
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
        markdown_path=markdown_path or deploy_report_path(inventory_dir),
        overall_status=overall_status,
        total_count=len(results),
        completed_count=completed_count,
        passed_count=passed_count,
        failed_count=failed_count,
        not_run_count=not_run_count,
        results=results,
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
        summary = _validation_markdown_summary(item)
        lines.extend(
            [
                f"### {item.name}",
                "",
                f"- Status: `{status_label(item.status)}`",
                f"- Detail report: `{detail_name}`",
                f"- Summary: {summary}",
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
        "not_run": "NOT RUN",
        "incomplete": "INCOMPLETE",
    }.get(status, status.upper() if status else "UNKNOWN")


def _validation_markdown_summary(item: DeployValidationResult) -> str:
    if item.kind != "mk8s_nccl":
        return item.summary
    return _NCCL_AVG_BUS_BANDWIDTH_MARKDOWN_RE.sub(
        r"\1**\2**\3",
        item.summary,
        count=1,
    )


def _validation_report_path(spec: Mapping[str, Any], *, inventory_dir: Path) -> Path:
    report_file = str(spec.get("report_file", "") or "").strip()
    if report_file:
        return inventory_dir / report_file
    kind = str(spec.get("kind", "") or "").strip() or "validation"
    return inventory_dir / f"{kind}-report.json"


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
    inventory_dir: Path,
) -> DeployValidationResult:
    kind = str(spec.get("kind", "") or "").strip()
    report_path = _validation_report_path(spec, inventory_dir=inventory_dir)
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
    if kind == "mk8s_gpu_visibility":
        return _gpu_visibility_summary(payload)
    if kind == "mk8s_nccl":
        return _nccl_summary(payload)
    if kind == "mk8s_observability_ingestion":
        return _observability_ingestion_summary(payload)
    if kind == "mysterybox_eso_connectivity":
        return _mysterybox_eso_connectivity_summary(payload)
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
    if kind == "mk8s_nccl":
        return _nccl_footer_summary(payload)
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


def _nccl_footer_summary(payload: Mapping[str, Any]) -> str:
    phase = str(payload.get("launcher_phase", "") or "").strip() or "unknown"
    avg = float(payload.get("avg_bus_bandwidth_gbps", 0.0) or 0.0)
    threshold = float(payload.get("threshold_gbps", 0.0) or 0.0)
    worker_count = int(payload.get("selected_worker_node_count", 0) or 0)
    worker_label = f"{worker_count} {_plural_word(worker_count, 'worker')}"
    transport = str(payload.get("transport_label", "") or "").strip() or "auto"
    parts = [phase]
    if bool(payload.get("single_rank_smoke")) and not bool(payload.get("bandwidth_observed")):
        parts.append(f"{transport} single-rank smoke across {worker_label}")
        parts.append("no collective bandwidth observed")
        if not bool(payload.get("threshold_enforced", True)):
            parts.append("RDMA threshold not enforced")
        return "; ".join(parts) + "."
    if bool(payload.get("threshold_enforced", True)):
        parts.append(
            f"{transport} {avg:.1f} Gbps (threshold {threshold:.1f}) across {worker_label}"
        )
    else:
        parts.append(f"{transport} {avg:.1f} Gbps across {worker_label}")
        parts.append("RDMA threshold not enforced")
    env_name = str(payload.get("nccl_dmabuf_env_name", "") or "").strip()
    env_value = str(payload.get("nccl_dmabuf_enable", "") or "").strip()
    gpudirect_mode = str(payload.get("gpudirect_mode", "") or "").strip()
    if env_name == "NCCL_DMABUF_ENABLE" and env_value == "1":
        parts.append("DMA-BUF enabled")
    elif gpudirect_mode:
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


def _nccl_summary(payload: Mapping[str, Any]) -> str:
    if bool(payload.get("skipped")):
        reason = str(payload.get("skip_reason", "") or "").strip()
        total = int(payload.get("total_gpu_node_count", 0) or 0)
        summary = f"Skipped: {reason or 'no schedulable NCCL GPU slot was available'}"
        if total:
            summary += f"; total Ready GPU nodes {total}"
        return summary + "."
    phase = str(payload.get("launcher_phase", "") or "").strip() or "unknown"
    avg = float(payload.get("avg_bus_bandwidth_gbps", 0.0) or 0.0)
    threshold = float(payload.get("threshold_gbps", 0.0) or 0.0)
    worker_count = int(payload.get("selected_worker_node_count", 0) or 0)
    transport = str(payload.get("transport_label", "") or "").strip() or "auto"
    dmabuf = _nccl_dmabuf_summary(payload)
    if bool(payload.get("single_rank_smoke")) and not bool(payload.get("bandwidth_observed")):
        dmabuf_text = f"; {dmabuf}" if dmabuf else ""
        threshold_text = (
            "; RDMA threshold not enforced for this run"
            if not bool(payload.get("threshold_enforced", True))
            else ""
        )
        return (
            f"Launcher phase {phase}; {transport} single-rank smoke run across "
            f"{worker_count} worker node(s); no collective bus bandwidth observed"
            f"{dmabuf_text}{threshold_text}."
        )
    if not bool(payload.get("threshold_enforced", True)):
        dmabuf_text = f"; {dmabuf}" if dmabuf else ""
        return (
            f"Launcher phase {phase}; {transport} average bus bandwidth {avg:.1f} Gbps "
            f"across {worker_count} worker node(s){dmabuf_text}; "
            "RDMA threshold not enforced for this run."
        )
    dmabuf_text = f"; {dmabuf}" if dmabuf else ""
    return (
        f"Launcher phase {phase}; {transport} average bus bandwidth {avg:.1f} Gbps "
        f"vs threshold {threshold:.1f} Gbps across {worker_count} worker node(s)"
        f"{dmabuf_text}."
    )


def _nccl_dmabuf_summary(payload: Mapping[str, Any]) -> str:
    env_name = str(payload.get("nccl_dmabuf_env_name", "") or "").strip()
    env_value = str(payload.get("nccl_dmabuf_enable", "") or "").strip()
    if not env_name or not env_value:
        return ""
    source = str(payload.get("nccl_dmabuf_enable_source", "") or "").strip()
    gpudirect_mode = str(payload.get("gpudirect_mode", "") or "").strip()
    notes = []
    if source and source != "unset":
        notes.append(source)
    if gpudirect_mode:
        notes.append(f"GPUDirect mode {gpudirect_mode}")
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return f"{env_name}={env_value}{suffix}"


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
