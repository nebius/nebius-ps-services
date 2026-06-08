"""Soperator and Slurm runtime validation helpers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data

SOPERATOR_CLUSTER_VALIDATION_KIND = "soperator_cluster_smoke"
SOPERATOR_CLUSTER_VALIDATION_SCHEMA = "nebius-cxcli-soperator-cluster-validation/v1"
SOPERATOR_NAMESPACE = "soperator"

_SMOKE_MARKER = "cxcli-soperator-srun-ok"


def _smoke_script(*, partition: str = "") -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-smoke"
            f"{partition_arg} --nodes=1 --ntasks=1 "
            "--cpus-per-task=1 --time=00:02:00 --immediate=60 "
            "bash -lc 'set -euo pipefail; echo cxcli-soperator-srun-ok; hostname'",
        ]
    )


@dataclass(frozen=True)
class SoperatorValidationCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SoperatorValidationCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        """Run an external validation command."""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(value)


def _command_text(args: Sequence[str]) -> str:
    return " ".join(str(item) for item in args)


def _command_detail(result: SoperatorValidationCommandResult) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"


def _compact_output(value: str, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... output truncated ..."


def _default_command_runner(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
    extra_env: Mapping[str, str] | None = None,
) -> SoperatorValidationCommandResult:
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    command = [str(item) for item in args]
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    result = SoperatorValidationCommandResult(
        args=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{_command_text(command)} failed: {_command_detail(result)}")
    return result


def _validation_report_file(target_ref: str) -> str:
    normalized = normalize_component_token(target_ref) or "soperator"
    return f"soperator-cluster-validation-report-{normalized}.json"


def _soperator_cluster_name(row: Mapping[str, Any], *, target_ref: str) -> str:
    values = _as_mapping(row.get("values"))
    cluster_name = str(values.get("clusterName", "") or values.get("cluster_name", "") or "").strip()
    return cluster_name or target_ref


def soperator_cluster_validation_specs(payload_or_config: Any) -> list[dict[str, Any]]:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        return []
    deploy_targets = _as_sequence(_as_mapping(payload.get("deploy")).get("targets"))
    target_context_by_ref: dict[str, str] = {}
    for row in deploy_targets:
        if not isinstance(row, Mapping):
            continue
        target_ref = normalize_component_token(
            row.get("instance_id") or row.get("target_ref") or row.get("id")
        )
        if not target_ref:
            continue
        target_context_by_ref[target_ref] = str(row.get("kube_context", "") or "").strip()
    charts = _as_sequence(_as_mapping(payload.get("apps")).get("charts"))
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("id", "") or "").strip() != "soperator":
            continue
        if row.get("enabled") is False:
            continue
        target_ref = normalize_component_token(row.get("instance_id") or row.get("target_ref"))
        if not target_ref or target_ref in seen:
            continue
        seen.add(target_ref)
        specs.append(
            {
                "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
                "name": f"Soperator cluster smoke test ({target_ref})",
                "target_ref": target_ref,
                "namespace": SOPERATOR_NAMESPACE,
                "cluster_name": _soperator_cluster_name(row, target_ref=target_ref),
                "kube_context": target_context_by_ref.get(target_ref, ""),
                "report_file": _validation_report_file(target_ref),
                "required": True,
            }
        )
    return specs


def _kubectl_args(spec: Mapping[str, Any], *args: str) -> list[str]:
    command = ["kubectl"]
    context = str(spec.get("kube_context", "") or "").strip()
    if context:
        command.extend(["--context", context])
    command.extend(args)
    return command


def _json_command(
    runner: SoperatorValidationCommandRunner,
    args: Sequence[str],
    *,
    timeout_seconds: int = 120,
) -> Mapping[str, Any]:
    result = runner(args, timeout_seconds=timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result))
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(args)} returned invalid JSON: {exc}") from exc
    return payload if isinstance(payload, Mapping) else {}


def _pod_ready(item: Mapping[str, Any]) -> bool:
    conditions = _as_sequence(_as_mapping(item.get("status")).get("conditions"))
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if str(condition.get("type", "") or "").strip() != "Ready":
            continue
        return str(condition.get("status", "") or "").strip().lower() == "true"
    phase = str(_as_mapping(item.get("status")).get("phase", "") or "").strip()
    return phase == "Running"


def _login_pod_name(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> str:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    payload = _json_command(
        runner,
        _kubectl_args(spec, "-n", namespace, "get", "pods", "-o", "json"),
    )
    best_name = ""
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        metadata = _as_mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _as_mapping(metadata.get("labels"))
        label_text = " ".join(str(value) for value in labels.values())
        if not name or ("login" not in name and "login" not in label_text):
            continue
        if _pod_ready(item):
            return name
        if not best_name:
            best_name = name
    return best_name


def _exec_login(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    args: Sequence[str],
    *,
    timeout_seconds: int = 300,
) -> SoperatorValidationCommandResult:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    pod = _login_pod_name(runner, spec)
    if not pod:
        return SoperatorValidationCommandResult(tuple(args), 1, "", "login pod not found")
    return runner(
        _kubectl_args(spec, "-n", namespace, "exec", pod, "--", *[str(item) for item in args]),
        timeout_seconds=timeout_seconds,
        check=False,
    )


def _append_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    status: str,
    summary: str,
    command: Sequence[str] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    item: dict[str, Any] = {
        "name": name,
        "status": status,
        "passed": status == "passed",
        "summary": summary,
    }
    if command:
        item["command"] = _command_text(command)
    if stdout:
        item["stdout"] = _compact_output(stdout)
    if stderr:
        item["stderr"] = _compact_output(stderr)
    checks.append(item)


def _check_rollout(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(
        spec,
        "-n",
        namespace,
        "rollout",
        "status",
        "deployment/soperator-manager",
        "--timeout=10m",
    )
    result = runner(command, timeout_seconds=900, check=False)
    if result.returncode == 0:
        _append_check(
            checks,
            name="Soperator manager rollout",
            status="passed",
            summary="soperator-manager deployment rollout is healthy.",
            command=command,
            stdout=result.stdout,
        )
    else:
        _append_check(
            checks,
            name="Soperator manager rollout",
            status="failed",
            summary="soperator-manager deployment rollout did not complete.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def _check_slurmcluster(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    cluster_name = str(spec.get("cluster_name", "") or spec.get("target_ref") or "").strip()
    payload = _json_command(
        runner,
        _kubectl_args(spec, "-n", namespace, "get", "slurmclusters", "-o", "json"),
    )
    clusters: list[str] = []
    target_phase = ""
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        metadata = _as_mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not name:
            continue
        phase = str(_as_mapping(item.get("status")).get("phase", "") or "present").strip()
        clusters.append(f"{name}={phase}")
        if name == cluster_name:
            target_phase = phase
    if target_phase == "Available":
        _append_check(
            checks,
            name="SlurmCluster availability",
            status="passed",
            summary=f"target SlurmCluster {cluster_name} is Available.",
        )
    else:
        summary = (
            f"target SlurmCluster {cluster_name} phase is {target_phase or 'missing'}; "
            f"visible clusters: {', '.join(clusters) or 'none'}."
        )
        _append_check(
            checks,
            name="SlurmCluster availability",
            status="failed",
            summary=summary,
        )


def _check_slurm_status(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-h", "-o", "%t"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        _append_check(
            checks,
            name="Slurm node status",
            status="failed",
            summary="sinfo could not read Slurm worker state from a login pod.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )
        return
    states = [
        line.strip().lower().rstrip("*~#!%@^-+$")
        for line in sinfo.stdout.splitlines()
        if line.strip()
    ]
    unhealthy = {"down", "fail", "failing", "unknown", "invalid", "inval", "future"}
    if states and not any(state in unhealthy for state in states):
        summary = "worker states: " + ", ".join(states[:8])
        if len(states) > 8:
            summary += f", +{len(states) - 8} more"
        _append_check(
            checks,
            name="Slurm node status",
            status="passed",
            summary=summary,
            command=sinfo.args,
            stdout=sinfo.stdout,
        )
    else:
        _append_check(
            checks,
            name="Slurm node status",
            status="failed",
            summary="no healthy Slurm worker state was returned by sinfo.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )


def _check_slurm_queue(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    squeue = _exec_login(
        runner,
        spec,
        ("squeue", "-h", "-o", "%T"),
        timeout_seconds=120,
    )
    if squeue.returncode != 0:
        _append_check(
            checks,
            name="Slurm queue access",
            status="failed",
            summary="squeue could not read Slurm queue state from a login pod.",
            command=squeue.args,
            stdout=squeue.stdout,
            stderr=squeue.stderr,
        )
        return
    jobs = [line.strip() for line in squeue.stdout.splitlines() if line.strip()]
    summary = "queue is empty." if not jobs else f"queue contains {len(jobs)} job(s)."
    _append_check(
        checks,
        name="Slurm queue access",
        status="passed",
        summary=summary,
        command=squeue.args,
        stdout=squeue.stdout,
    )


def _sinfo_partition_candidates(stdout: str) -> tuple[str, ...]:
    preferred: list[str] = []
    fallback: list[str] = []
    for line in stdout.splitlines():
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 2:
            continue
        partition = parts[0].rstrip("*").strip()
        state = parts[1].lower().rstrip("*~#!%@^-+$")
        gres = parts[2].lower() if len(parts) > 2 else ""
        if not partition or partition == "hidden" or state != "idle":
            continue
        if "gpu" not in partition.lower() and "gpu" not in gres:
            preferred.append(partition)
        else:
            fallback.append(partition)
    return tuple(dict.fromkeys([*preferred, *fallback]))


def _select_srun_smoke_partition(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> str:
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-h", "-o", "%P|%t|%G"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        return ""
    candidates = _sinfo_partition_candidates(sinfo.stdout)
    return candidates[0] if candidates else ""


def _check_srun_smoke(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    partition = _select_srun_smoke_partition(runner, spec)
    result = _exec_login(
        runner,
        spec,
        ("bash", "-lc", _smoke_script(partition=partition)),
        timeout_seconds=360,
    )
    if result.returncode == 0 and _SMOKE_MARKER in result.stdout:
        partition_summary = f" on partition {partition}" if partition else ""
        _append_check(
            checks,
            name="Slurm srun smoke job",
            status="passed",
            summary=f"one-task synchronous srun job completed successfully{partition_summary}.",
            command=result.args,
            stdout=result.stdout,
        )
    else:
        _append_check(
            checks,
            name="Slurm srun smoke job",
            status="failed",
            summary=(
                "one-task synchronous srun job failed or did not print the expected "
                f"{_SMOKE_MARKER} marker."
            ),
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def run_soperator_cluster_validations(
    validations: list[dict[str, Any]],
    *,
    reports_dir: Path,
    extra_env: Mapping[str, str] | None = None,
    emit: Callable[[str], None] | None = None,
    command_runner: SoperatorValidationCommandRunner | None = None,
) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, spec in enumerate(validations, start=1):
        if str(spec.get("kind", "") or "").strip() != SOPERATOR_CLUSTER_VALIDATION_KIND:
            continue
        if emit:
            emit(f"Starting validation {index}/{len(validations)}: {spec.get('name')}.")
        runner = command_runner or (
            lambda args, *, input_text=None, timeout_seconds=300, check=True: _default_command_runner(
                args,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                check=check,
                extra_env=extra_env,
            )
        )
        checks: list[dict[str, Any]] = []
        try:
            _check_rollout(runner, spec, checks)
            _check_slurmcluster(runner, spec, checks)
            _check_slurm_status(runner, spec, checks)
            _check_slurm_queue(runner, spec, checks)
            _check_srun_smoke(runner, spec, checks)
        except Exception as exc:
            _append_check(
                checks,
                name="Soperator validation execution",
                status="failed",
                summary=str(exc),
            )
        failed = [item for item in checks if item.get("status") == "failed"]
        report = {
            "schema": SOPERATOR_CLUSTER_VALIDATION_SCHEMA,
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "validation": "Soperator cluster smoke test",
            "target_ref": str(spec.get("target_ref", "") or ""),
            "name": str(spec.get("name", "") or "Soperator cluster smoke test"),
            "status": "failed" if failed else "passed",
            "passed": not failed,
            "summary": (
                f"{len(checks) - len(failed)}/{len(checks)} Soperator/Slurm checks passed."
                if checks
                else "No Soperator/Slurm checks were run."
            ),
            "checks": checks,
        }
        report_path = reports_dir / str(
            spec.get("report_file") or _validation_report_file(str(spec.get("target_ref") or ""))
        )
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(report_path)
        if failed:
            names = ", ".join(str(item.get("name") or "check") for item in failed)
            raise RuntimeError(f"Soperator cluster validation failed: {names}.")
    return written


__all__ = [
    "SOPERATOR_CLUSTER_VALIDATION_KIND",
    "SOPERATOR_CLUSTER_VALIDATION_SCHEMA",
    "SoperatorValidationCommandResult",
    "run_soperator_cluster_validations",
    "soperator_cluster_validation_specs",
]
