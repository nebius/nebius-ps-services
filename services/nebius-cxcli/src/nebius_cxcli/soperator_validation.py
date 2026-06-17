"""Soperator and Slurm runtime validation helpers."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
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
_GPU_VISIBILITY_MARKER = "cxcli-soperator-gpu-visibility-ok"
_NCCL_MARKER = "cxcli-soperator-nccl-ok"
_NCCL_SKIP_MARKER = "cxcli-soperator-nccl-skipped:"
_NCCL_RUN_RE = re.compile(
    r"cxcli-soperator-nccl-ok\s+mode=(?P<mode>\S+)\s+partition=(?P<partition>\S+)\s+"
    r"nodes=(?P<nodes>[0-9]+)\s+gpus_per_node=(?P<gpus_per_node>[0-9]+)\s+"
    r"ranks=(?P<ranks>[0-9]+)"
)
_NCCL_LARGE_MESSAGE_BYTES = (2 * 1024**3, 4 * 1024**3, 8 * 1024**3)
_SOPERATOR_CLUSTER_READY_WAIT_SECONDS = 20 * 60
_SOPERATOR_CLUSTER_READY_POLL_SECONDS = 15.0


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


def _gpu_visibility_script(*, partition: str) -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-gpu-visibility"
            f"{partition_arg} --nodes=1 --ntasks=1 --gres=gpu:1 "
            "--time=00:03:00 --immediate=60 "
            "bash -lc 'set -euo pipefail; echo cxcli-soperator-gpu-visibility-ok; "
            "hostname; nvidia-smi -L'",
        ]
    )


def _nccl_script(*, partition: str) -> str:
    partition_value = shlex.quote(partition)
    return "\n".join(
        [
            "set -euo pipefail",
            f"partition={partition_value}",
            'sinfo_output="$(sinfo -N -h -p "$partition" -o "%N|%t|%G|%c" || true)"',
            "total_gpu_nodes=()",
            "idle_gpu_nodes=()",
            'while IFS="|" read -r node state gres cpus; do',
            '  [[ -n "${node:-}" ]] || continue',
            '  if [[ "${gres:-}" =~ gpu:([0-9]+) ]]; then',
            '    gpu_count="${BASH_REMATCH[1]}"',
            '    cpu_count="${cpus:-0}"',
            '    [[ "$cpu_count" =~ ^[0-9]+$ ]] || cpu_count=0',
            '    total_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            '    state_lower="$(printf "%s" "${state:-}" | tr "[:upper:]" "[:lower:]")"',
            '    if [[ "$state_lower" == idle* ]]; then',
            '      idle_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            "    fi",
            "  fi",
            'done <<< "$sinfo_output"',
            "target_nodes=0",
            "benchmark_mode=",
            'if (( ${#total_gpu_nodes[@]} == 0 )); then',
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            'at least one GPU node on partition $partition; found 0 GPU node(s)."',
            "  exit 0",
            "fi",
            'if (( ${#total_gpu_nodes[@]} >= 2 )); then',
            "  target_nodes=2",
            '  benchmark_mode="multi-node"',
            '  if (( ${#idle_gpu_nodes[@]} < target_nodes )); then',
            '    echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            '2 idle GPU nodes on partition $partition; found ${#idle_gpu_nodes[@]} idle '
            'GPU node(s), ${#total_gpu_nodes[@]} total GPU node(s)."',
            "    exit 0",
            "  fi",
            "else",
            "  target_nodes=1",
            '  benchmark_mode="single-node-multi-gpu"',
            '  if (( ${#idle_gpu_nodes[@]} < target_nodes )); then',
            '    echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            'the only GPU node on partition $partition to be idle; found '
            '${#idle_gpu_nodes[@]} idle GPU node(s), ${#total_gpu_nodes[@]} total GPU node(s)."',
            "    exit 0",
            "  fi",
            "fi",
            "selected=()",
            "gpus_per_node=999999",
            "cpus_per_node=999999",
            "for entry in \"${idle_gpu_nodes[@]:0:$target_nodes}\"; do",
            '  IFS="|" read -r node gpu_count cpu_count <<< "$entry"',
            '  selected+=("$node")',
            "  if (( gpu_count < gpus_per_node )); then",
            '    gpus_per_node="$gpu_count"',
            "  fi",
            "  if (( cpu_count > 0 && cpu_count < cpus_per_node )); then",
            '    cpus_per_node="$cpu_count"',
            "  fi",
            "done",
            "if (( gpus_per_node < 1 || gpus_per_node == 999999 )); then",
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark could not '
            'resolve GPU count for selected nodes on partition $partition."',
            "  exit 0",
            "fi",
            "if (( cpus_per_node == 999999 )); then",
            "  cpus_per_node=$((gpus_per_node * 4))",
            "fi",
            'reported_gpus_per_node="$gpus_per_node"',
            'if (( target_nodes == 1 && gpus_per_node < 2 )); then',
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            'at least 2 GPUs on the only GPU node in partition $partition; found '
            '$gpus_per_node GPU(s)."',
            "  exit 0",
            "fi",
            'node_list="$(IFS=,; echo "${selected[*]}")"',
            "allocatable_gpus_per_node=0",
            "allocatable_cpus_per_task=0",
            "probe_output=",
            'probe_file="$(mktemp)"',
            'trap \'rm -f "$probe_file"\' EXIT',
            'candidate_gpus="$gpus_per_node"',
            "while (( candidate_gpus >= 1 )); do",
            "  candidate_cpus=$((candidate_gpus * 4))",
            "  if (( cpus_per_node > 0 && candidate_cpus > cpus_per_node )); then",
            "    candidate_gpus=$((candidate_gpus - 1))",
            "    continue",
            "  fi",
            "  if salloc --job-name=cxcli-soperator-nccl-probe "
            '--partition="$partition" --nodelist="$node_list" '
            '--nodes="$target_nodes" --ntasks="$target_nodes" --ntasks-per-node=1 '
            '--gres="gpu:$candidate_gpus" --cpus-per-task="$candidate_cpus" '
            '--time=00:01:00 --immediate=10 bash -lc true >"$probe_file" 2>&1; then',
            '    allocatable_gpus_per_node="$candidate_gpus"',
            '    allocatable_cpus_per_task="$candidate_cpus"',
            "    break",
            "  fi",
            '  probe_output="$(cat "$probe_file" 2>/dev/null || true)"',
            "  candidate_gpus=$((candidate_gpus - 1))",
            "done",
            "if (( allocatable_gpus_per_node < 1 )); then",
            '  echo "cxcli-soperator-nccl-allocation-failed: could not allocate any GPU '
            'count on $target_nodes selected node(s) from reported $reported_gpus_per_node '
            'GPU(s) per node. Last probe: ${probe_output:-empty}" >&2',
            "  exit 1",
            "fi",
            'gpus_per_node="$allocatable_gpus_per_node"',
            'cpus_per_task="$allocatable_cpus_per_task"',
            'if (( target_nodes == 1 && gpus_per_node < 2 )); then',
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            'at least 2 allocatable GPUs on the only GPU node in partition $partition; '
            'reported $reported_gpus_per_node GPU(s), allocatable $gpus_per_node."',
            "  exit 0",
            "fi",
            "ranks=$((target_nodes * gpus_per_node))",
            'export CXCLI_NCCL_MODE="$benchmark_mode"',
            'export CXCLI_NCCL_PARTITION="$partition"',
            'export CXCLI_NCCL_NODES="$target_nodes"',
            'export CXCLI_NCCL_GPUS_PER_NODE="$gpus_per_node"',
            'export CXCLI_NCCL_REPORTED_GPUS_PER_NODE="$reported_gpus_per_node"',
            'export CXCLI_NCCL_RANKS="$ranks"',
            "runner_script=$(cat <<'CXCLI_NCCL_RUNNER'",
            "set -euo pipefail",
            'echo "cxcli-soperator-nccl-ok mode=${CXCLI_NCCL_MODE} '
            'partition=${CXCLI_NCCL_PARTITION} nodes=${CXCLI_NCCL_NODES} '
            'gpus_per_node=${CXCLI_NCCL_GPUS_PER_NODE} ranks=${CXCLI_NCCL_RANKS} '
            'reported_gpus_per_node=${CXCLI_NCCL_REPORTED_GPUS_PER_NODE}"',
            "export PATH=/usr/mpi/gcc/openmpi-4.1.7a1/bin:$PATH",
            "export OMPI_ALLOW_RUN_AS_ROOT=1",
            "export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
            "command -v mpirun",
            "command -v /usr/bin/all_reduce_perf_mpi",
            'hostfile="$(mktemp)"',
            'trap \'rm -f "$hostfile"\' EXIT',
            'scontrol show hostnames "$SLURM_JOB_NODELIST" '
            '| awk -v slots="$CXCLI_NCCL_GPUS_PER_NODE" '
            '\'{print $1 " slots=" slots}\' > "$hostfile"',
            'echo "cxcli-soperator-nccl-hostfile"',
            'cat "$hostfile"',
            "mpirun --allow-run-as-root --hostfile \"$hostfile\" "
            "-np \"$CXCLI_NCCL_RANKS\" -N \"$CXCLI_NCCL_GPUS_PER_NODE\" --bind-to none "
            "--oversubscribe -mca coll ^hcoll "
            "/usr/bin/all_reduce_perf_mpi -b 512M -e 8G -f 2 -g 1",
            "CXCLI_NCCL_RUNNER",
            ")",
            "launcher_script=$(printf "
            "'srun --job-name=cxcli-soperator-nccl-launcher "
            "--nodes=1 --ntasks=1 --cpus-per-task=%q --gres=gpu:%q "
            "--kill-on-bad-exit=1 bash -lc %q' "
            '"$cpus_per_task" "$gpus_per_node" "$runner_script")',
            "salloc --job-name=cxcli-soperator-nccl "
            "--partition=\"$partition\" --nodelist=\"$node_list\" "
            "--nodes=\"$target_nodes\" --ntasks=\"$target_nodes\" --ntasks-per-node=1 "
            '--gres="gpu:$gpus_per_node" '
            '--cpus-per-task="$cpus_per_task" --time=00:30:00 --immediate=60 '
            'bash -lc "$launcher_script"',
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


def _format_gib_label(size_bytes: int) -> str:
    return f"{size_bytes // 1024**3}G"


def _nccl_large_message_bus_bandwidth_gbps(output: str) -> tuple[float | None, dict[str, float]]:
    values: dict[str, float] = {}
    wanted = set(_NCCL_LARGE_MESSAGE_BYTES)
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) < 12 or not parts[0].isdigit():
            continue
        try:
            size_bytes = int(parts[0])
        except ValueError:
            continue
        if size_bytes not in wanted:
            continue
        bus_values: list[float] = []
        for index in (7, 11):
            try:
                bus_values.append(float(parts[index]))
            except (IndexError, ValueError):
                continue
        if not bus_values:
            continue
        values[_format_gib_label(size_bytes)] = max(bus_values)
    if {key for key in values} != {_format_gib_label(size) for size in wanted}:
        return None, {}
    average = sum(values.values()) / len(values)
    return average, values


def _nccl_run_metadata(output: str) -> dict[str, Any]:
    match = _NCCL_RUN_RE.search(output or "")
    if match is None:
        return {}
    metadata: dict[str, Any] = {
        "mode": match.group("mode"),
        "partition": match.group("partition"),
    }
    for key in ("nodes", "gpus_per_node", "ranks"):
        metadata[key] = int(match.group(key))
    return metadata


def _nccl_skip_summary(output: str) -> str:
    for line in (output or "").splitlines():
        text = line.strip()
        if text.startswith(_NCCL_SKIP_MARKER):
            return text.removeprefix(_NCCL_SKIP_MARKER).strip()
    return "full Slurm NCCL benchmark was skipped."


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


def _soperator_chart_version(row: Mapping[str, Any]) -> str:
    return str(row.get("version", "") or row.get("chart_version", "") or "").strip()


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
                "target_version": _soperator_chart_version(row),
                "kube_context": target_context_by_ref.get(target_ref, ""),
                "report_file": _validation_report_file(target_ref),
                "readiness_timeout_seconds": _SOPERATOR_CLUSTER_READY_WAIT_SECONDS,
                "readiness_poll_seconds": _SOPERATOR_CLUSTER_READY_POLL_SECONDS,
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


def _float_setting(value: Any, *, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def _target_slurmcluster_phase(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> str:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    cluster_name = str(spec.get("cluster_name", "") or spec.get("target_ref") or "").strip()
    payload = _json_command(
        runner,
        _kubectl_args(spec, "-n", namespace, "get", "slurmclusters", "-o", "json"),
    )
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        metadata = _as_mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if name != cluster_name:
            continue
        return str(_as_mapping(item.get("status")).get("phase", "") or "present").strip()
    return ""


def _wait_for_slurmcluster_available(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> None:
    timeout_seconds = _float_setting(spec.get("readiness_timeout_seconds"), default=0.0)
    if timeout_seconds <= 0:
        return
    poll_seconds = _float_setting(
        spec.get("readiness_poll_seconds"),
        default=_SOPERATOR_CLUSTER_READY_POLL_SECONDS,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if _target_slurmcluster_phase(runner, spec) == "Available":
                return
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(max(poll_seconds, 0.1), remaining))


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
    extra: Mapping[str, Any] | None = None,
) -> None:
    item: dict[str, Any] = {
        "name": name,
        "status": status,
        "passed": status == "passed",
        "summary": summary,
    }
    if extra:
        item.update(dict(extra))
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


def _flux_helmrelease_chart_text(item: Mapping[str, Any]) -> str:
    spec = _as_mapping(item.get("spec"))
    chart_spec = _as_mapping(_as_mapping(spec.get("chart")).get("spec"))
    status = _as_mapping(item.get("status"))
    history = _as_sequence(status.get("history"))
    history_item = _as_mapping(history[0]) if history else {}
    parts = (
        spec.get("releaseName"),
        spec.get("targetNamespace"),
        chart_spec.get("chart"),
        chart_spec.get("version"),
        history_item.get("chartName"),
        history_item.get("chartVersion"),
        history_item.get("appVersion"),
    )
    return " ".join(str(part) for part in parts if str(part or "").strip()).lower()


def _old_source_flux_helmrelease_name(name: str) -> bool:
    text = str(name or "").strip()
    return text in {"soperator-fluxcd", "flux-system-soperator-fluxcd"} or text.startswith(
        ("flux-system-soperator-fluxcd-", "soperator-fluxcd-")
    )


def _active_old_source_flux_helmrelease(item: Mapping[str, Any], *, target_version: str) -> bool:
    metadata = _as_mapping(item.get("metadata"))
    spec = _as_mapping(item.get("spec"))
    if spec.get("suspend") is True:
        return False
    name = str(metadata.get("name", "") or "").strip()
    if _old_source_flux_helmrelease_name(name):
        return True
    chart_text = _flux_helmrelease_chart_text(item)
    if target_version and target_version.lower() in chart_text:
        return False
    return any(token in chart_text for token in ("soperator", "slurm-cluster", "nodeset"))


def _check_old_source_flux_desired_state(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    target_version = str(spec.get("target_version", "") or "").strip()
    command = _kubectl_args(spec, "get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json")
    result = runner(command, timeout_seconds=120, check=False)
    if result.returncode != 0:
        detail = _command_detail(result).lower()
        if "the server doesn't have a resource type" in detail or "not found" in detail:
            return
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return
    active: list[str] = []
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        if not _active_old_source_flux_helmrelease(item, target_version=target_version):
            continue
        metadata = _as_mapping(item.get("metadata"))
        namespace = str(metadata.get("namespace", "") or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if namespace and name:
            active.append(f"{namespace}/{name}")
    if not active:
        return
    shown = ", ".join(active[:8])
    if len(active) > 8:
        shown += f", +{len(active) - 8} more"
    _append_check(
        checks,
        name="Old source Flux desired state",
        status="failed",
        summary=(
            "active old source-family Flux HelmRelease records are still present: "
            f"{shown}. Suspend or remove the old source desired state before validating "
            "the adopted target Soperator release."
        ),
        command=command,
    )


def _pending_pod_scheduling_message(item: Mapping[str, Any]) -> str:
    status = _as_mapping(item.get("status"))
    for condition in _as_sequence(status.get("conditions")):
        if not isinstance(condition, Mapping):
            continue
        if str(condition.get("type", "") or "").strip() != "PodScheduled":
            continue
        message = str(condition.get("message", "") or "").strip()
        if message:
            return message
    return str(status.get("message", "") or "").strip()


def _check_soperator_pod_scheduling(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "pods", "-o", "json")
    result = runner(command, timeout_seconds=120, check=False)
    if result.returncode != 0:
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return
    pending: list[str] = []
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        phase = str(_as_mapping(item.get("status")).get("phase", "") or "").strip()
        if phase != "Pending":
            continue
        metadata = _as_mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not name:
            continue
        message = _pending_pod_scheduling_message(item)
        pending.append(f"{name}: {message or 'Pending'}")
    if not pending:
        return
    shown = "; ".join(pending[:5])
    if len(pending) > 5:
        shown += f"; +{len(pending) - 5} more"
    _append_check(
        checks,
        name="Soperator pod scheduling",
        status="failed",
        summary="pending Soperator pod(s) block Slurm readiness: " + shown,
        command=command,
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


def _sinfo_gpu_partition_candidates(stdout: str) -> tuple[str, ...]:
    preferred: list[str] = []
    for line in stdout.splitlines():
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 3:
            continue
        partition = parts[0].rstrip("*").strip()
        state = parts[1].lower().rstrip("*~#!%@^-+$")
        gres = parts[2].lower()
        if not partition or partition == "hidden":
            continue
        if "gpu" not in partition.lower() and "gpu" not in gres:
            continue
        if state in {"idle", "mix", "mixed"}:
            preferred.append(partition)
    return tuple(dict.fromkeys(preferred))


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


def _select_gpu_smoke_partition(
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
    candidates = _sinfo_gpu_partition_candidates(sinfo.stdout)
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


def _check_slurm_gpu_visibility(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    partition = _select_gpu_smoke_partition(runner, spec)
    if not partition:
        return
    result = _exec_login(
        runner,
        spec,
        ("bash", "-lc", _gpu_visibility_script(partition=partition)),
        timeout_seconds=420,
    )
    if result.returncode == 0 and _GPU_VISIBILITY_MARKER in result.stdout and "GPU " in result.stdout:
        _append_check(
            checks,
            name="Slurm GPU visibility test",
            status="passed",
            summary=f"one-GPU Slurm allocation reported NVIDIA GPUs on partition {partition}.",
            command=result.args,
            stdout=result.stdout,
        )
    else:
        _append_check(
            checks,
            name="Slurm GPU visibility test",
            status="failed",
            summary=(
                "one-GPU Slurm allocation failed or did not report an NVIDIA GPU "
                f"on partition {partition}."
            ),
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def _check_slurm_nccl_benchmark(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    partition = _select_gpu_smoke_partition(runner, spec)
    if not partition:
        return
    result = _exec_login(
        runner,
        spec,
        ("bash", "-lc", _nccl_script(partition=partition)),
        timeout_seconds=2400,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode == 0 and _NCCL_SKIP_MARKER in output:
        summary = _nccl_skip_summary(output)
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status="skipped",
            summary=f"Skipped: {summary}",
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
            extra={"skipped": True, "skip_reason": summary},
        )
        return
    avg_bus_bandwidth, large_message_bus_bandwidth = _nccl_large_message_bus_bandwidth_gbps(
        output
    )
    metadata = _nccl_run_metadata(output)
    if result.returncode == 0 and _NCCL_MARKER in output and avg_bus_bandwidth is not None:
        nodes = metadata.get("nodes", 2)
        ranks = metadata.get("ranks", 0)
        gpus_per_node = metadata.get("gpus_per_node", 0)
        benchmark_mode = str(metadata.get("mode") or "")
        bandwidth_sizes = ", ".join(large_message_bus_bandwidth) or "large"
        if nodes > 1:
            benchmark_summary = (
                f"two-node NCCL all_reduce_perf benchmark completed on partition {partition} "
                f"with {ranks} rank(s) across {nodes} node(s)"
                f"{f' ({gpus_per_node} GPU(s) per node)' if gpus_per_node else ''}; "
            )
        else:
            benchmark_summary = (
                f"single-node multi-GPU NCCL all_reduce_perf benchmark completed on "
                f"partition {partition} with {ranks} rank(s) on 1 node"
                f"{f' ({gpus_per_node} GPU(s) on the node)' if gpus_per_node else ''}; "
            )
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status="passed",
            summary=(
                benchmark_summary
                + f"average bus bandwidth {avg_bus_bandwidth:.1f} Gbps across "
                f"{bandwidth_sizes} message sizes."
            ),
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
            extra={
                "avg_large_message_bus_bandwidth_gbps": avg_bus_bandwidth,
                "large_message_bus_bandwidth_gbps": large_message_bus_bandwidth,
                "multi_node_benchmark": nodes > 1,
                "single_node_multi_gpu_benchmark": nodes == 1,
                "benchmark_mode": benchmark_mode,
                **metadata,
            },
        )
    else:
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status="failed",
            summary=(
                "NCCL all_reduce_perf benchmark failed or did not report "
                f"large-message bandwidth output on partition {partition}."
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
            _check_old_source_flux_desired_state(runner, spec, checks)
            _wait_for_slurmcluster_available(runner, spec)
            _check_soperator_pod_scheduling(runner, spec, checks)
            _check_slurmcluster(runner, spec, checks)
            _check_slurm_status(runner, spec, checks)
            _check_slurm_queue(runner, spec, checks)
            _check_srun_smoke(runner, spec, checks)
            _check_slurm_gpu_visibility(runner, spec, checks)
            _check_slurm_nccl_benchmark(runner, spec, checks)
        except Exception as exc:
            _append_check(
                checks,
                name="Soperator validation execution",
                status="failed",
                summary=str(exc),
            )
        failed = [item for item in checks if item.get("status") == "failed"]
        passed = [item for item in checks if item.get("status") == "passed"]
        skipped = [item for item in checks if item.get("status") == "skipped"]
        if checks:
            summary = f"{len(passed)}/{len(checks)} Soperator/Slurm checks passed."
            if skipped:
                summary += f" {len(skipped)} skipped."
        else:
            summary = "No Soperator/Slurm checks were run."
        report = {
            "schema": SOPERATOR_CLUSTER_VALIDATION_SCHEMA,
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "validation": "Soperator cluster smoke test",
            "target_ref": str(spec.get("target_ref", "") or ""),
            "name": str(spec.get("name", "") or "Soperator cluster smoke test"),
            "status": "failed" if failed else "passed",
            "passed": not failed,
            "summary": summary,
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
