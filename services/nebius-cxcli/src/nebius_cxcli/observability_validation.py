"""Deploy-time Kubernetes observability validation helpers."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

OBSERVABILITY_INGESTION_VALIDATION_KIND = "mk8s_observability_ingestion"


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _as_text(value).lower() in {"true", "t", "1", "yes", "y"}


def _kubectl_json(args: list[str], *, extra_env: dict[str, str] | None) -> dict[str, Any]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["kubectl", *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"kubectl exited with status {result.returncode}"
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {detail}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kubectl {' '.join(args)} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"kubectl {' '.join(args)} returned a non-object JSON payload")
    return payload


def _kubectl_raw_json(path: str, *, extra_env: dict[str, str] | None) -> dict[str, Any]:
    return _kubectl_json(["get", "--raw", path], extra_env=extra_env)


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _report_path(spec: Mapping[str, Any], *, inventory_dir: Path) -> Path:
    report_file = _as_text(spec.get("report_file"))
    if report_file:
        return inventory_dir / report_file
    return inventory_dir / "observability-ingestion-report.json"


def _check(
    name: str,
    *,
    passed: bool,
    summary: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "summary": summary,
        "details": dict(details or {}),
    }


def _condition(payload: Mapping[str, Any], condition_type: str) -> dict[str, Any]:
    status = _mapping(payload.get("status"))
    for condition in _list(status.get("conditions")):
        if isinstance(condition, Mapping) and _as_text(condition.get("type")) == condition_type:
            return dict(condition)
    return {}


def _helmrelease_ready_check(
    payload: Mapping[str, Any],
    *,
    condition_type: str,
) -> dict[str, Any]:
    condition = _condition(payload, condition_type)
    status = _as_text(condition.get("status"))
    reason = _as_text(condition.get("reason"))
    message = _as_text(condition.get("message"))
    passed = status.lower() == "true"
    summary = (
        f"{condition_type}=True ({reason or 'no reason'})"
        if passed
        else f"{condition_type} condition is not True"
    )
    if not passed and (reason or message):
        summary = (
            f"{condition_type}={status or 'missing'} "
            f"({reason or 'no reason'}): {message or 'no message'}"
        )
    return _check(
        "HelmRelease Ready",
        passed=passed,
        summary=summary,
        details={
            "status": status,
            "reason": reason,
            "message": message,
            "observed_generation": condition.get("observedGeneration"),
        },
    )


def _path_value(payload: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for raw_segment in dotted_path.split("."):
        segment = raw_segment.strip()
        if not segment or not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _required_text(spec: Mapping[str, Any], key: str) -> str:
    value = _as_text(spec.get(key))
    if not value:
        raise RuntimeError(f"Observability validation spec is missing required field '{key}'")
    return value


def _required_positive_int(spec: Mapping[str, Any], key: str) -> int:
    raw = spec.get(key)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Observability validation spec field '{key}' must be a positive integer"
        ) from exc
    if value < 1:
        raise RuntimeError(f"Observability validation spec field '{key}' must be a positive integer")
    return value


def _chart_signal_config_check(
    payload: Mapping[str, Any],
    *,
    expected_signals: Mapping[str, Any],
    signal_value_paths: Mapping[str, Any],
    cluster_metric_targets_path: str,
) -> dict[str, Any]:
    failures: list[str] = []
    details: dict[str, Any] = {"expected": dict(expected_signals), "actual": {}}
    for signal in ("logs", "metrics", "traces"):
        path = _as_text(signal_value_paths.get(signal))
        if not path:
            raise RuntimeError(
                f"Observability validation spec is missing signal_value_paths.{signal}"
            )
        expected_enabled = _bool(expected_signals.get(signal))
        actual_value = _path_value(payload, path)
        details["actual"][signal] = actual_value
        actual_enabled = _bool(actual_value)
        if actual_enabled != expected_enabled:
            expected_label = "enabled" if expected_enabled else "disabled"
            actual_label = "enabled" if actual_enabled else "disabled"
            failures.append(
                f"{signal} expected {expected_label} but HelmRelease values are {actual_label}"
            )
    if _bool(expected_signals.get("collect_k8s_cluster_metrics")):
        additional_targets = _path_value(payload, cluster_metric_targets_path)
        details["actual"]["metrics_additional_target_count"] = (
            len(additional_targets) if isinstance(additional_targets, list) else 0
        )
        if not isinstance(additional_targets, list) or not additional_targets:
            failures.append("cluster metrics enabled but no metrics.additionalTargets are configured")
    if failures:
        return _check(
            "Agent signal config",
            passed=False,
            summary="; ".join(failures),
            details=details,
        )
    enabled = [signal for signal in ("logs", "metrics", "traces") if _bool(expected_signals.get(signal))]
    summary = "Enabled signals: " + (", ".join(enabled) if enabled else "none")
    if _bool(expected_signals.get("collect_k8s_cluster_metrics")):
        summary += f"; cluster metric targets {details['actual'].get('metrics_additional_target_count', 0)}"
    return _check("Agent signal config", passed=True, summary=summary, details=details)


def _namespace_api_path(namespace: str, resource: str, *, query: Mapping[str, Any]) -> str:
    query_text = "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
        for key, value in query.items()
        if str(value)
    )
    path = f"/api/v1/namespaces/{quote(namespace, safe='')}/{resource}"
    return f"{path}?{query_text}" if query_text else path


def _discovery_namespace_api_path(
    namespace: str,
    resource: str,
    *,
    query: Mapping[str, Any],
) -> str:
    query_text = "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
        for key, value in query.items()
        if str(value)
    )
    path = f"/apis/discovery.k8s.io/v1/namespaces/{quote(namespace, safe='')}/{resource}"
    return f"{path}?{query_text}" if query_text else path


def _pod_failure_sample(
    *,
    namespace: str,
    pod_selector: str,
    limit: int,
    extra_env: dict[str, str] | None,
) -> list[dict[str, Any]]:
    if not pod_selector:
        return []
    payload = _kubectl_raw_json(
        _namespace_api_path(
            namespace,
            "pods",
            query={
                "labelSelector": pod_selector,
                "fieldSelector": "status.phase!=Running",
                "limit": limit,
            },
        ),
        extra_env=extra_env,
    )
    sample: list[dict[str, Any]] = []
    for item in _list(payload.get("items"))[:limit]:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        spec = _mapping(item.get("spec"))
        status = _mapping(item.get("status"))
        sample.append(
            {
                "name": _as_text(metadata.get("name")),
                "node_name": _as_text(spec.get("nodeName")),
                "phase": _as_text(status.get("phase")),
                "reason": _as_text(status.get("reason")),
                "message": _as_text(status.get("message")),
            }
        )
    return sample


def _daemonset_ready_check(
    *,
    namespace: str,
    daemonset_name: str,
    pod_selector: str,
    pod_failure_sample_limit: int,
    extra_env: dict[str, str] | None,
) -> dict[str, Any]:
    payload = _kubectl_json(
        ["-n", namespace, "get", "daemonset.apps", daemonset_name, "-o", "json"],
        extra_env=extra_env,
    )
    daemonsets: list[dict[str, Any]] = []
    failures: list[str] = []
    metadata = _mapping(payload.get("metadata"))
    status = _mapping(payload.get("status"))
    name = _as_text(metadata.get("name"))
    desired = int(status.get("desiredNumberScheduled", 0) or 0)
    ready = int(status.get("numberReady", 0) or 0)
    updated = int(status.get("updatedNumberScheduled", 0) or 0)
    available = int(status.get("numberAvailable", 0) or 0)
    misscheduled = int(status.get("numberMisscheduled", 0) or 0)
    passed = bool(desired > 0 and ready == desired and updated == desired and misscheduled == 0)
    if not passed:
        failures.append(f"{name or daemonset_name} ready {ready}/{desired}, updated {updated}")
    daemonsets.append(
        {
            "name": name,
            "desired": desired,
            "ready": ready,
            "updated": updated,
            "available": available,
            "misscheduled": misscheduled,
            "passed": passed,
        }
    )
    if failures:
        summary = "; ".join(failures)
        passed = False
    else:
        total_ready = sum(item["ready"] for item in daemonsets)
        total_desired = sum(item["desired"] for item in daemonsets)
        summary = f"DaemonSet pods ready {total_ready}/{total_desired}"
        passed = True
    return _check(
        "Agent DaemonSet Ready",
        passed=passed,
        summary=summary,
        details={
            "daemonset": daemonset_name,
            "pod_selector": pod_selector,
            "daemonsets": daemonsets,
            "non_running_pod_sample": []
            if passed
            else _pod_failure_sample(
                namespace=namespace,
                pod_selector=pod_selector,
                limit=pod_failure_sample_limit,
                extra_env=extra_env,
            ),
        },
    )


def _otlp_service_check(
    *,
    namespace: str,
    service_name: str,
    service_port: int,
    endpoint_slice_selector: str,
    endpoint_slice_check_limit: int,
    extra_env: dict[str, str] | None,
) -> dict[str, Any]:
    service = _kubectl_json(
        ["-n", namespace, "get", "service", service_name, "-o", "json"],
        extra_env=extra_env,
    )
    ports = _list(_mapping(service.get("spec")).get("ports"))
    grpc_ports = [
        port
        for port in ports
        if isinstance(port, Mapping)
        and int(port.get("port", 0) or 0) == service_port
    ]
    slices = _kubectl_raw_json(
        _discovery_namespace_api_path(
            namespace,
            "endpointslices",
            query={
                "labelSelector": endpoint_slice_selector,
                "limit": endpoint_slice_check_limit,
            },
        ),
        extra_env=extra_env,
    )
    ready_endpoint_found = False
    endpoint_sample_count = 0
    for item in _list(slices.get("items")):
        if not isinstance(item, Mapping):
            continue
        for endpoint in _list(item.get("endpoints")):
            if not isinstance(endpoint, Mapping):
                continue
            endpoint_sample_count += 1
            conditions = _mapping(endpoint.get("conditions"))
            if conditions.get("ready") is not False:
                ready_endpoint_found = True
                break
        if ready_endpoint_found:
            break
    passed = bool(grpc_ports and ready_endpoint_found)
    if not grpc_ports:
        summary = f"Service {service_name} has no expected port {service_port}"
    else:
        summary = "OTLP/gRPC ready endpoint found" if ready_endpoint_found else "No ready OTLP/gRPC endpoint found"
    return _check(
        "Trace OTLP Service Ready",
        passed=passed,
        summary=summary,
        details={
            "service": service_name,
            "grpc_ports": [dict(port) for port in grpc_ports],
            "service_port": service_port,
            "endpoint_slice_selector": endpoint_slice_selector,
            "endpoint_slice_check_limit": endpoint_slice_check_limit,
            "endpoint_sample_count": endpoint_sample_count,
            "ready_endpoint_found": ready_endpoint_found,
        },
    )


def _validation_error_report(
    *,
    spec: Mapping[str, Any],
    inventory_dir: Path,
    error: BaseException,
) -> None:
    report_path = _report_path(spec, inventory_dir=inventory_dir)
    if report_path.exists():
        return
    _write_report(
        report_path,
        {
            "validation": _as_text(spec.get("name")) or "Observability ingestion",
            "kind": OBSERVABILITY_INGESTION_VALIDATION_KIND,
            "target_ref": _as_text(spec.get("target_ref")),
            "passed": False,
            "error": str(error),
            "checked_at": datetime.now(UTC).isoformat(),
            "checks": [],
        },
    )


def _run_observability_ingestion_validation(
    spec: Mapping[str, Any],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> Path:
    namespace = _required_text(spec, "namespace")
    release_name = _required_text(spec, "helmrelease_name")
    validation_name = _as_text(spec.get("name")) or "Observability ingestion"
    ready_condition = _required_text(spec, "helmrelease_ready_condition")
    signal_value_paths = _mapping(spec.get("signal_value_paths"))
    cluster_metric_targets_path = _required_text(spec, "cluster_metric_targets_path")
    daemonset_name = _required_text(spec, "daemonset_name")
    pod_selector = _required_text(spec, "pod_selector")
    pod_failure_sample_limit = _required_positive_int(spec, "pod_failure_sample_limit")
    trace_otlp_service = _mapping(spec.get("trace_otlp_service"))
    expected_signals = _mapping(spec.get("signals"))
    checks: list[dict[str, Any]] = []

    if emit:
        emit(f"Checking Observability Agent HelmRelease {namespace}/{release_name}.")
    helmrelease = _kubectl_json(
        [
            "-n",
            namespace,
            "get",
            "helmrelease.helm.toolkit.fluxcd.io",
            release_name,
            "-o",
            "json",
        ],
        extra_env=extra_env,
    )
    checks.append(_helmrelease_ready_check(helmrelease, condition_type=ready_condition))
    checks.append(
        _chart_signal_config_check(
            helmrelease,
            expected_signals=expected_signals,
            signal_value_paths=signal_value_paths,
            cluster_metric_targets_path=cluster_metric_targets_path,
        )
    )

    if emit:
        emit("Checking Observability Agent DaemonSet readiness.")
    checks.append(
        _daemonset_ready_check(
            namespace=namespace,
            daemonset_name=daemonset_name,
            pod_selector=pod_selector,
            pod_failure_sample_limit=pod_failure_sample_limit,
            extra_env=extra_env,
        )
    )

    if _bool(expected_signals.get("traces")):
        service_name = _required_text(trace_otlp_service, "name")
        service_port = _required_positive_int(trace_otlp_service, "port")
        endpoint_slice_selector = _required_text(trace_otlp_service, "endpoint_slice_selector")
        endpoint_slice_check_limit = _required_positive_int(
            trace_otlp_service,
            "endpoint_slice_check_limit",
        )
        if emit:
            emit(f"Checking Observability Agent OTLP service {namespace}/{service_name}.")
        checks.append(
            _otlp_service_check(
                namespace=namespace,
                service_name=service_name,
                service_port=service_port,
                endpoint_slice_selector=endpoint_slice_selector,
                endpoint_slice_check_limit=endpoint_slice_check_limit,
                extra_env=extra_env,
            )
        )

    passed = all(bool(check.get("passed")) for check in checks)
    report_path = _report_path(spec, inventory_dir=inventory_dir)
    report = {
        "validation": validation_name,
        "kind": OBSERVABILITY_INGESTION_VALIDATION_KIND,
        "target_ref": _as_text(spec.get("target_ref")),
        "namespace": namespace,
        "helmrelease_name": release_name,
        "daemonset_name": daemonset_name,
        "pod_selector": pod_selector,
        "trace_otlp_service": dict(trace_otlp_service),
        "signals": dict(expected_signals),
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "checks": checks,
    }
    _write_report(report_path, report)
    if not passed:
        failures = [str(check.get("summary")) for check in checks if not bool(check.get("passed"))]
        raise RuntimeError(
            f"{validation_name} failed: " + "; ".join(failures or ["one or more checks failed"])
        )
    return report_path


def run_observability_validations(
    validations: list[dict[str, Any]],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    written_reports: list[Path] = []
    total = len(validations)
    for index, spec in enumerate(validations, start=1):
        kind = _as_text(spec.get("kind"))
        name = _as_text(spec.get("name")) or kind or f"validation-{index}"
        if emit:
            emit(f"Starting validation {index}/{total}: {name}.")
        try:
            if kind == OBSERVABILITY_INGESTION_VALIDATION_KIND:
                written_reports.append(
                    _run_observability_ingestion_validation(
                        spec,
                        inventory_dir=inventory_dir,
                        extra_env=extra_env,
                        emit=emit,
                    )
                )
        except Exception as exc:
            _validation_error_report(spec=spec, inventory_dir=inventory_dir, error=exc)
            raise
    return written_reports


__all__ = [
    "OBSERVABILITY_INGESTION_VALIDATION_KIND",
    "run_observability_validations",
]
