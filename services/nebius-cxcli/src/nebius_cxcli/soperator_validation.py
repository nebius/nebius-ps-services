"""Soperator and Slurm runtime validation helpers."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .component_instances import normalize_component_token
from .duration_utils import parse_go_duration_seconds
from .runtime_config import to_plain_data
from .soperator_gpu_driver_jail import (
    SOPERATOR_GPU_DRIVER_JAIL_HOST_PATH,
    SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME,
    SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH,
    soperator_nodeset_is_gpu_worker,
)

SOPERATOR_CLUSTER_VALIDATION_KIND = "soperator_cluster_smoke"
SOPERATOR_CLUSTER_VALIDATION_SCHEMA = "nebius-cxcli-soperator-cluster-validation/v2"
SOPERATOR_NAMESPACE = "soperator"
SOPERATOR_DEPLOY_SMOKE_MODE = "deploy"
SOPERATOR_ACCEPTANCE_SMOKE_MODE = "acceptance"
SOPERATOR_ACCEPTANCE_BENCHMARK_MODE = "benchmark"

_SMOKE_MARKER = "cxcli-soperator-srun-ok"
_PARTITION_HOSTNAME_MARKER = "cxcli-soperator-partition-hostnames-ok"
_GPU_ALLOCATION_MARKER = "cxcli-soperator-gpu-allocation-ok"
_GPU_DRIVER_JAIL_MARKER = "cxcli-soperator-gpu-driver-jail-ok"
_NCCL_MARKER = "cxcli-soperator-nccl-ok"
_NCCL_SKIP_MARKER = "cxcli-soperator-nccl-skipped:"
_NCCL_ALLOCATION_FAILED_MARKER = "cxcli-soperator-nccl-allocation-failed:"
_NCCL_RUN_RE = re.compile(
    r"cxcli-soperator-nccl-ok\s+mode=(?P<mode>\S+)\s+partition=(?P<partition>\S+)\s+"
    r"nodes=(?P<nodes>[0-9]+)\s+gpus_per_node=(?P<gpus_per_node>[0-9]+)\s+"
    r"ranks=(?P<ranks>[0-9]+)"
    r"(?:\s+reported_gpus_per_node=(?P<reported_gpus_per_node>[0-9]+))?"
    r"(?:\s+max_bytes=(?P<max_bytes>\S+))?"
)
_GPU_ALLOCATION_HOST_RE = re.compile(
    rf"^{re.escape(_GPU_ALLOCATION_MARKER)}\s+host=(?P<host>\S+)"
    r"(?:\s+evidence=(?P<evidence>\S+))?"
)
_GPU_DRIVER_JAIL_HOST_RE = re.compile(
    rf"^{re.escape(_GPU_DRIVER_JAIL_MARKER)}\s+host=(?P<host>\S+)"
)
_GPU_GRES_RE = re.compile(r"gpu(?::[A-Za-z0-9_.-]+)?:(?P<count>[0-9]+)")
_NCCL_LARGE_MESSAGE_BYTES = (2 * 1024**3, 4 * 1024**3, 8 * 1024**3)
_NCCL_ONE_GPU_LARGE_MESSAGE_BYTES = (2 * 1024**3,)
_NCCL_PREFERRED_GPUS_PER_NODE = 8
_GPU_ALLOCATION_VALID_EVIDENCE = {"nvidia-smi", "proc-driver-device"}
_REPORT_OUTPUT_MAX_LINES = 10000
_REPORT_OUTPUT_MAX_LINE_CHARS = 1200
_SOPERATOR_CLUSTER_READY_WAIT_SECONDS = 20 * 60
_SOPERATOR_CLUSTER_READY_POLL_SECONDS = 15.0
_SOPERATOR_DEPLOY_SNAPSHOT_COMMAND_TIMEOUT_SECONDS = 30
_SOPERATOR_RUNTIME_COMMAND_TIMEOUT_SECONDS = 120
_SRUN_SMOKE_IMMEDIATE_SECONDS = 60
_SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS = 600
_SLURM_SMOKE_SAMPLE_NODES_PER_PARTITION = 1
_SLURM_PARTITION_HOSTNAME_RETRY_ATTEMPTS = 3
_SLURM_PARTITION_HOSTNAME_RETRY_DELAY_SECONDS = 30.0
_SOPERATOR_STORAGE_VOLUME_NAME = "jail"
_SOPERATOR_STORAGE_PVC_NAME = "jail-pvc"
_SOPERATOR_STORAGE_PV_NAME = "jail-pv"
_SOPERATOR_STORAGE_MOUNT_DAEMONSET_NAME = "jail-mount"
_SOPERATOR_STORAGE_LOCAL_PATH = "/mnt/jail"
_SOPERATOR_STORAGE_PENDING_MARKERS = (
    "failedmount",
    "mountvolume",
    "volume",
)
_SOPERATOR_POPULATE_JAIL_JOB_SUFFIX = "populate-jail"
_SOPERATOR_POPULATE_JAIL_SENTINEL_FILE = ".populated"
_SOPERATOR_POPULATE_JAIL_RECOVERY_TIMEOUT_SECONDS = 180
_SOPERATOR_POD_UNHEALTHY_WAITING_REASONS = {
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "CreateContainerError",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "RunContainerError",
}
_SOPERATOR_POD_UNHEALTHY_TERMINATED_REASONS = {
    "ContainerCannotRun",
    "Error",
    "OOMKilled",
}


def _smoke_script(
    *,
    partition: str = "",
    immediate_seconds: int = _SRUN_SMOKE_IMMEDIATE_SECONDS,
) -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-smoke"
            f"{partition_arg} --nodes=1 --ntasks=1 "
            f"--cpus-per-task=1 --time=00:02:00 --immediate={immediate_seconds} "
            "bash -lc 'set -euo pipefail; echo cxcli-soperator-srun-ok; hostname'",
        ]
    )


def _partition_hostname_script(
    *,
    partition: str,
    nodes: int,
    nodelist: Sequence[str] = (),
    immediate_seconds: int = _SRUN_SMOKE_IMMEDIATE_SECONDS,
) -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    nodelist_arg = (
        f" --nodelist={shlex.quote(','.join(node for node in nodelist if node))}"
        if nodelist
        else ""
    )
    node_count = max(int(nodes), 1)
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-partition-hostnames"
            f"{partition_arg}{nodelist_arg} --nodes={node_count} --ntasks={node_count} "
            "--ntasks-per-node=1 --time=00:05:00 "
            f"--immediate={immediate_seconds} hostname | sort",
            f"echo {_PARTITION_HOSTNAME_MARKER} partition={shlex.quote(partition)} nodes={node_count}",
        ]
    )


def _gpu_allocation_script(
    *,
    partition: str,
    nodes: int,
    nodelist: Sequence[str] = (),
    immediate_seconds: int = _SRUN_SMOKE_IMMEDIATE_SECONDS,
) -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    nodelist_arg = (
        f" --nodelist={shlex.quote(','.join(node for node in nodelist if node))}"
        if nodelist
        else ""
    )
    node_count = max(int(nodes), 1)
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-gpu-allocation"
            f"{partition_arg}{nodelist_arg} --nodes={node_count} --ntasks={node_count} "
            "--ntasks-per-node=1 --gres=gpu:1 --time=00:05:00 "
            f"--immediate={immediate_seconds} "
            'bash -lc \'set -euo pipefail; host="$(hostname)"; '
            'evidence=""; nvidia_smi_path="$(command -v nvidia-smi || true)"; '
            'gpu_output=""; '
            'if [[ -n "$nvidia_smi_path" && -s "$nvidia_smi_path" ]]; then '
            'gpu_output="$(nvidia-smi -L || true)"; printf "%s\\n" "$gpu_output"; '
            'if grep -q "^GPU " <<< "$gpu_output"; then evidence="nvidia-smi"; fi; '
            'elif [[ -n "$nvidia_smi_path" ]]; then '
            'echo "cxcli-soperator-gpu-allocation-warning host=$host '
            'nvidia-smi-unusable path=$nvidia_smi_path"; '
            "fi; "
            'if [[ -z "$evidence" ]]; then '
            'proc_model="$(grep -h "^Model:" /proc/driver/nvidia/gpus/*/information '
            '2>/dev/null | head -n 1 || true)"; '
            'device_path="$(ls /dev/nvidia[0-9]* 2>/dev/null | head -n 1 || true)"; '
            '[[ -n "$proc_model" ]] && echo "cxcli-soperator-gpu-proc host=$host $proc_model"; '
            '[[ -n "$device_path" ]] && echo "cxcli-soperator-gpu-device host=$host '
            'path=$device_path"; '
            'if [[ -n "$proc_model" && -n "$device_path" ]]; then '
            'evidence="proc-driver-device"; fi; '
            "fi; "
            'if [[ -z "$evidence" ]]; then '
            "echo cxcli-soperator-gpu-allocation-missing host=$host >&2; exit 1; fi; "
            f"echo {_GPU_ALLOCATION_MARKER} host=$host evidence=$evidence'",
        ]
    )


def _gpu_driver_jail_script(
    *,
    partition: str,
    nodes: int,
    nodelist: Sequence[str] = (),
    immediate_seconds: int = _SRUN_SMOKE_IMMEDIATE_SECONDS,
) -> str:
    partition_arg = f" --partition={shlex.quote(partition)}" if partition else ""
    nodelist_arg = (
        f" --nodelist={shlex.quote(','.join(node for node in nodelist if node))}"
        if nodelist
        else ""
    )
    node_count = max(int(nodes), 1)
    return "\n".join(
        [
            "set -euo pipefail",
            "srun --job-name=cxcli-soperator-gpu-driver-jail"
            f"{partition_arg}{nodelist_arg} --nodes={node_count} --ntasks={node_count} "
            "--ntasks-per-node=1 --gres=gpu:1 --time=00:05:00 "
            f"--immediate={immediate_seconds} "
            'bash -lc \'set -euo pipefail; host="$(hostname)"; '
            'check_lib() { local lib="$1"; local path=""; '
            'path="$(ldconfig -p 2>/dev/null | awk -v lib="$lib" '
            "'\"'\"'$1 == lib { print $NF; exit }'\"'\"')\"; "
            'if [[ -z "$path" ]]; then path="/usr/lib/$(uname -m)-linux-gnu/$lib"; fi; '
            'if [[ ! -e "$path" ]]; then echo "cxcli-soperator-gpu-driver-jail-missing '
            'host=$host lib=$lib path=$path"; return 1; fi; '
            'if [[ ! -s "$path" ]]; then echo "cxcli-soperator-gpu-driver-jail-empty '
            'host=$host lib=$lib path=$path"; return 1; fi; '
            'printf " %s=%s" "$lib" "$path"; }; '
            'cuda_lib="$(check_lib libcuda.so.1)" || exit 42; '
            'nvml_lib="$(check_lib libnvidia-ml.so.1)" || exit 42; '
            'libs="${cuda_lib}${nvml_lib}"; '
            'gpu_output="$(nvidia-smi -L 2>/dev/null || true)"; '
            'if ! grep -q "^GPU " <<< "$gpu_output"; then '
            'echo "cxcli-soperator-gpu-driver-jail-missing-gpu host=$host"; exit 43; fi; '
            f"echo {_GPU_DRIVER_JAIL_MARKER} host=$host$libs'",
        ]
    )


def _slurm_time_arg(timeout_seconds: int | None) -> str:
    if timeout_seconds is None:
        return ""
    seconds = max(1, int(timeout_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"--time={hours:02d}:{minutes:02d}:{remaining_seconds:02d} "


def _nccl_script(
    *,
    partition: str,
    max_nodes: int | None = None,
    timeout_seconds: int | None = None,
) -> str:
    partition_value = shlex.quote(partition)
    max_nodes_value = max(0, int(max_nodes or 0))
    time_arg = _slurm_time_arg(timeout_seconds)
    return "\n".join(
        [
            "set -euo pipefail",
            f"partition={partition_value}",
            f"requested_max_nodes={max_nodes_value}",
            'sinfo_output="$(sinfo -N -h -p "$partition" -o "%N|%t|%G|%c" || true)"',
            "total_gpu_nodes=()",
            "eligible_gpu_nodes=()",
            "idle_eligible_gpu_nodes=()",
            "preferred_gpu_nodes=()",
            "idle_preferred_gpu_nodes=()",
            'while IFS="|" read -r node state gres cpus; do',
            '  [[ -n "${node:-}" ]] || continue',
            '  if [[ "${gres:-}" =~ gpu(:[[:alnum:]_.-]+)?:([0-9]+) ]]; then',
            '    gpu_count="${BASH_REMATCH[2]}"',
            '    cpu_count="${cpus:-0}"',
            '    [[ "$cpu_count" =~ ^[0-9]+$ ]] || cpu_count=0',
            '    total_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            '    state_lower="$(printf "%s" "${state:-}" | tr "[:upper:]" "[:lower:]")"',
            "    if (( gpu_count >= 1 )); then",
            '      eligible_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            '      if [[ "$state_lower" == idle* ]]; then',
            '        idle_eligible_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            "      fi",
            f"      if (( gpu_count >= {_NCCL_PREFERRED_GPUS_PER_NODE} )); then",
            '        preferred_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            '        if [[ "$state_lower" == idle* ]]; then',
            '          idle_preferred_gpu_nodes+=("$node|$gpu_count|$cpu_count")',
            "        fi",
            "      fi",
            "    fi",
            "  fi",
            'done <<< "$sinfo_output"',
            "target_nodes=0",
            "benchmark_mode=",
            "if (( ${#total_gpu_nodes[@]} == 0 )); then",
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            'at least one GPU node on partition $partition; found 0 GPU node(s)."',
            "  exit 0",
            "fi",
            "if (( ${#eligible_gpu_nodes[@]} == 0 )); then",
            '  echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark could not '
            "resolve allocatable Slurm GPU counts on partition $partition; found "
            '${#total_gpu_nodes[@]} GPU-labeled node(s)."',
            "  exit 0",
            "fi",
            'candidate_gpu_nodes=("${eligible_gpu_nodes[@]}")',
            'idle_candidate_gpu_nodes=("${idle_eligible_gpu_nodes[@]}")',
            "if (( ${#idle_preferred_gpu_nodes[@]} >= 2 )); then",
            '  candidate_gpu_nodes=("${preferred_gpu_nodes[@]}")',
            '  idle_candidate_gpu_nodes=("${idle_preferred_gpu_nodes[@]}")',
            "elif (( requested_max_nodes == 1 && ${#idle_preferred_gpu_nodes[@]} >= 1 )); then",
            '  candidate_gpu_nodes=("${preferred_gpu_nodes[@]}")',
            '  idle_candidate_gpu_nodes=("${idle_preferred_gpu_nodes[@]}")',
            "elif (( ${#idle_preferred_gpu_nodes[@]} == 1 && ${#idle_eligible_gpu_nodes[@]} >= 2 )); then",
            '  candidate_gpu_nodes=("${preferred_gpu_nodes[@]}")',
            '  idle_candidate_gpu_nodes=("${idle_preferred_gpu_nodes[@]}")',
            "fi",
            "if (( ${#candidate_gpu_nodes[@]} >= 2 )); then",
            "  target_nodes=${#idle_candidate_gpu_nodes[@]}",
            "  if (( requested_max_nodes > 0 && requested_max_nodes < target_nodes )); then",
            "    target_nodes=$requested_max_nodes",
            "  fi",
            "  if (( target_nodes >= 2 )); then",
            "    :",
            "  elif (( requested_max_nodes == 1 && ${#idle_candidate_gpu_nodes[@]} >= 1 )); then",
            "    target_nodes=1",
            "  else",
            '    echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            "2 idle GPU Slurm nodes on partition $partition, or --max-nodes 1 for "
            "a single-node run; found ${#idle_candidate_gpu_nodes[@]} idle candidate GPU node(s), "
            "${#candidate_gpu_nodes[@]} total candidate GPU node(s), "
            '${#total_gpu_nodes[@]} total GPU node(s)."',
            "    exit 0",
            "  fi",
            "else",
            "  target_nodes=1",
            "  if (( ${#idle_candidate_gpu_nodes[@]} < target_nodes )); then",
            '    echo "cxcli-soperator-nccl-skipped: full Slurm NCCL benchmark requires '
            "the only GPU Slurm node on partition $partition to be idle; found "
            "${#idle_candidate_gpu_nodes[@]} idle candidate GPU node(s), "
            "${#candidate_gpu_nodes[@]} total candidate GPU node(s), "
            '${#total_gpu_nodes[@]} total GPU node(s)."',
            "    exit 0",
            "  fi",
            "fi",
            "selected=()",
            "gpus_per_node=999999",
            "cpus_per_node=999999",
            'for entry in "${idle_candidate_gpu_nodes[@]:0:$target_nodes}"; do',
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
            'node_list="$(IFS=,; echo "${selected[*]}")"',
            "allocatable_gpus_per_node=0",
            "allocatable_cpus_per_task=0",
            "probe_output=",
            'probe_file="$(mktemp)"',
            "trap 'rm -f \"$probe_file\"' EXIT",
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
            "count on $target_nodes selected node(s) from reported $reported_gpus_per_node "
            'GPU(s) per node. Last probe: ${probe_output:-empty}" >&2',
            "  exit 1",
            "fi",
            'gpus_per_node="$allocatable_gpus_per_node"',
            'cpus_per_task="$allocatable_cpus_per_task"',
            "if (( target_nodes >= 2 )); then",
            '  benchmark_mode="multi-node"',
            "elif (( gpus_per_node >= 2 )); then",
            '  benchmark_mode="single-node-multi-gpu"',
            "else",
            '  benchmark_mode="single-node-single-gpu"',
            "fi",
            "ranks=$((target_nodes * gpus_per_node))",
            "if (( gpus_per_node == 1 )); then",
            "  nccl_max_bytes=2G",
            "else",
            "  nccl_max_bytes=8G",
            "fi",
            'export CXCLI_NCCL_MODE="$benchmark_mode"',
            'export CXCLI_NCCL_PARTITION="$partition"',
            'export CXCLI_NCCL_NODES="$target_nodes"',
            'export CXCLI_NCCL_GPUS_PER_NODE="$gpus_per_node"',
            'export CXCLI_NCCL_REPORTED_GPUS_PER_NODE="$reported_gpus_per_node"',
            'export CXCLI_NCCL_RANKS="$ranks"',
            'export CXCLI_NCCL_MAX_BYTES="$nccl_max_bytes"',
            "runner_script=$(cat <<'CXCLI_NCCL_RUNNER'",
            "set -euo pipefail",
            'echo "cxcli-soperator-nccl-ok mode=${CXCLI_NCCL_MODE} '
            "partition=${CXCLI_NCCL_PARTITION} nodes=${CXCLI_NCCL_NODES} "
            "gpus_per_node=${CXCLI_NCCL_GPUS_PER_NODE} ranks=${CXCLI_NCCL_RANKS} "
            "reported_gpus_per_node=${CXCLI_NCCL_REPORTED_GPUS_PER_NODE} "
            'max_bytes=${CXCLI_NCCL_MAX_BYTES}"',
            "export PATH=/usr/mpi/gcc/openmpi-4.1.7a1/bin:$PATH",
            "export OMPI_ALLOW_RUN_AS_ROOT=1",
            "export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
            "command -v mpirun",
            "command -v /usr/bin/all_reduce_perf_mpi",
            'hostfile="$(mktemp)"',
            "trap 'rm -f \"$hostfile\"' EXIT",
            'scontrol show hostnames "$SLURM_JOB_NODELIST" '
            '| awk -v slots="$CXCLI_NCCL_GPUS_PER_NODE" '
            '\'{print $1 " slots=" slots}\' > "$hostfile"',
            'echo "cxcli-soperator-nccl-hostfile"',
            'cat "$hostfile"',
            'mpirun --allow-run-as-root --hostfile "$hostfile" '
            '-np "$CXCLI_NCCL_RANKS" -N "$CXCLI_NCCL_GPUS_PER_NODE" --bind-to none '
            "--oversubscribe -mca coll ^hcoll "
            '/usr/bin/all_reduce_perf_mpi -b 512M -e "$CXCLI_NCCL_MAX_BYTES" -f 2 -g 1',
            "CXCLI_NCCL_RUNNER",
            ")",
            "launcher_script=$(printf "
            "'srun --job-name=cxcli-soperator-nccl-launcher "
            "--nodes=1 --ntasks=1 --cpus-per-task=%q --gres=gpu:%q "
            "--kill-on-bad-exit=1 bash -lc %q' "
            '"$cpus_per_task" "$gpus_per_node" "$runner_script")',
            "salloc --job-name=cxcli-soperator-nccl "
            '--partition="$partition" --nodelist="$node_list" '
            '--nodes="$target_nodes" --ntasks="$target_nodes" --ntasks-per-node=1 '
            '--gres="gpu:$gpus_per_node" '
            f'--cpus-per-task="$cpus_per_task" {time_arg}--immediate=60 '
            'bash -lc "$launcher_script"',
        ]
    )


@dataclass(frozen=True)
class SoperatorValidationCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class _PendingSoperatorPod:
    name: str
    message: str
    storage_related: bool = False


@dataclass(frozen=True)
class _UnhealthySoperatorPod:
    name: str
    message: str


@dataclass(frozen=True)
class _SoperatorJailStorage:
    pvc_name: str = _SOPERATOR_STORAGE_PVC_NAME
    pv_name: str = _SOPERATOR_STORAGE_PV_NAME
    mount_daemonset_name: str = _SOPERATOR_STORAGE_MOUNT_DAEMONSET_NAME
    local_path: str = _SOPERATOR_STORAGE_LOCAL_PATH


@dataclass(frozen=True)
class _SrunBatchResult:
    partition: str
    nodes: tuple[str, ...]
    result: SoperatorValidationCommandResult


class SoperatorValidationCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int | None = 300,
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


def _compact_output_line(value: str, *, limit: int = _REPORT_OUTPUT_MAX_LINE_CHARS) -> str:
    text = str(value or "").rstrip()
    if len(text) <= limit:
        return text
    return text[: limit - 24].rstrip() + " ... line truncated ..."


def _compact_output(value: str, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... output truncated ..."


def _compact_output_lines(
    value: str,
    *,
    max_lines: int = _REPORT_OUTPUT_MAX_LINES,
) -> list[str]:
    lines = [
        _compact_output_line(line) for line in str(value or "").strip().splitlines() if line.strip()
    ]
    if len(lines) <= max_lines:
        return lines
    omitted = len(lines) - max_lines
    return lines[:max_lines] + [f"... output truncated: {omitted} additional line(s) ..."]


def _partition_hostname_lines(stdout: str) -> list[str]:
    hostnames: list[str] = []
    for line in str(stdout or "").splitlines():
        text = line.strip()
        if not text or text.startswith(_PARTITION_HOSTNAME_MARKER):
            continue
        if text.startswith(("srun:", "slurmstepd:")):
            continue
        hostnames.append(text)
    return hostnames


def _gpu_allocation_hosts(stdout: str) -> list[str]:
    return sorted(_gpu_allocation_evidence(stdout))


def _gpu_allocation_evidence(stdout: str) -> dict[str, str]:
    evidence_by_host: dict[str, str] = {}
    for line in str(stdout or "").splitlines():
        match = _GPU_ALLOCATION_HOST_RE.match(line.strip())
        if match is None:
            continue
        host = match.group("host").strip()
        evidence = (match.group("evidence") or "").strip()
        if host and evidence in _GPU_ALLOCATION_VALID_EVIDENCE:
            evidence_by_host[host] = evidence
    return dict(sorted(evidence_by_host.items()))


def _slurm_partition_hostname_transient_failure(
    result: SoperatorValidationCommandResult,
) -> bool:
    if _PARTITION_HOSTNAME_MARKER in result.stdout:
        return False
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).lower()
    markers = (
        "copying stdout failed",
        "copying stderr failed",
        "error reading from error stream",
        "connection reset by peer",
        "required node not available",
        "resources are not available",
        "resource temporarily unavailable",
        "unable to allocate",
        "queued and waiting for resources",
        "has been allocated resources",
        "nodes required for job are down",
    )
    return any(marker in output for marker in markers)


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _slurm_smoke_sample_nodes(nodes: int) -> int:
    return min(max(int(nodes), 1), _SLURM_SMOKE_SAMPLE_NODES_PER_PARTITION)


def _validation_mode(spec: Mapping[str, Any]) -> str:
    mode = str(spec.get("mode", "") or "").strip().lower()
    if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        return SOPERATOR_ACCEPTANCE_SMOKE_MODE
    if mode == SOPERATOR_ACCEPTANCE_BENCHMARK_MODE:
        return SOPERATOR_ACCEPTANCE_BENCHMARK_MODE
    return SOPERATOR_DEPLOY_SMOKE_MODE


def _snapshot_command_timeout_seconds(spec: Mapping[str, Any]) -> int:
    if _validation_mode(spec) == SOPERATOR_DEPLOY_SMOKE_MODE:
        return _SOPERATOR_DEPLOY_SNAPSHOT_COMMAND_TIMEOUT_SECONDS
    return _SOPERATOR_RUNTIME_COMMAND_TIMEOUT_SECONDS


def _acceptance_batch_size(spec: Mapping[str, Any]) -> int:
    try:
        value = int(spec.get("batch_size", 128) or 128)
    except (TypeError, ValueError):
        return 128
    return max(1, value)


def _acceptance_concurrency(spec: Mapping[str, Any]) -> int:
    try:
        value = int(spec.get("concurrency", 8) or 8)
    except (TypeError, ValueError):
        return 8
    return max(1, value)


def _acceptance_continue_on_failure(spec: Mapping[str, Any]) -> bool:
    return bool(spec.get("continue_on_failure", True))


def _partition_name(value: str) -> str:
    return str(value or "").strip().rstrip("*").strip()


def _gpu_count_from_gres(value: str) -> int:
    counts = [
        _parse_positive_int(match.group("count"))
        for match in _GPU_GRES_RE.finditer(str(value or ""))
    ]
    return max(counts, default=0)


def _partition_node_counts(stdout: str) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for line in (stdout or "").splitlines():
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 2:
            continue
        partition = _partition_name(parts[0])
        node_count = _parse_positive_int(parts[1])
        if not partition or partition == "hidden" or node_count <= 0:
            continue
        counts[partition] = counts.get(partition, 0) + node_count
    return tuple((partition, count) for partition, count in counts.items() if count > 0)


def _partition_nodes(stdout: str) -> dict[str, list[str]]:
    nodes_by_partition: dict[str, list[str]] = {}
    for line in (stdout or "").splitlines():
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 2:
            continue
        partition = _partition_name(parts[0])
        node_name = parts[1]
        if not partition or partition == "hidden" or not node_name:
            continue
        bucket = nodes_by_partition.setdefault(partition, [])
        if node_name not in bucket:
            bucket.append(node_name)
    return {partition: sorted(nodes) for partition, nodes in nodes_by_partition.items()}


def _gpu_partition_nodes(stdout: str) -> dict[str, list[str]]:
    nodes_by_partition: dict[str, list[str]] = {}
    for line in (stdout or "").splitlines():
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 3:
            continue
        partition = _partition_name(parts[0])
        node_name = parts[1]
        gpu_count = _gpu_count_from_gres(parts[2])
        if not partition or partition == "hidden" or not node_name or gpu_count <= 0:
            continue
        bucket = nodes_by_partition.setdefault(partition, [])
        if node_name not in bucket:
            bucket.append(node_name)
    return {partition: sorted(nodes) for partition, nodes in nodes_by_partition.items()}


def _batches(items: Sequence[str], *, size: int) -> list[list[str]]:
    batch_size = max(1, int(size))
    return [list(items[index : index + batch_size]) for index in range(0, len(items), batch_size)]


def _format_gib_label(size_bytes: int) -> str:
    return f"{size_bytes // 1024**3}G"


def _nccl_message_size_bytes(value: str) -> int | None:
    match = re.fullmatch(r"(?P<number>[0-9]+)(?P<unit>[KMGTP]?)", str(value or "").strip(), re.I)
    if match is None:
        return None
    number = int(match.group("number"))
    unit = match.group("unit").upper()
    multipliers = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
    }
    return number * multipliers[unit]


def _nccl_expected_large_message_bytes(metadata: Mapping[str, Any]) -> tuple[int, ...]:
    max_message_bytes = metadata.get("max_message_bytes")
    if isinstance(max_message_bytes, int) and max_message_bytes > 0:
        expected = tuple(size for size in _NCCL_LARGE_MESSAGE_BYTES if size <= max_message_bytes)
        return expected or (max_message_bytes,)
    if metadata.get("gpus_per_node") == 1:
        return _NCCL_ONE_GPU_LARGE_MESSAGE_BYTES
    return _NCCL_LARGE_MESSAGE_BYTES


def _nccl_large_message_bus_bandwidth_gbps(
    output: str,
    *,
    expected_bytes: Sequence[int] = _NCCL_LARGE_MESSAGE_BYTES,
) -> tuple[float | None, dict[str, float]]:
    values: dict[str, float] = {}
    wanted = set(expected_bytes)
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
    for key in ("nodes", "gpus_per_node", "ranks", "reported_gpus_per_node"):
        value = match.group(key)
        if value is not None:
            metadata[key] = int(value)
    max_bytes = match.group("max_bytes")
    if max_bytes:
        metadata["max_message_size"] = max_bytes
        parsed_max_bytes = _nccl_message_size_bytes(max_bytes)
        if parsed_max_bytes is not None:
            metadata["max_message_bytes"] = parsed_max_bytes
    return metadata


def _nccl_skip_summary(output: str) -> str:
    for line in (output or "").splitlines():
        text = line.strip()
        if text.startswith(_NCCL_SKIP_MARKER):
            return text.removeprefix(_NCCL_SKIP_MARKER).strip()
    return "full Slurm NCCL benchmark was skipped."


def _nccl_failure_summary(output: str, *, partition: str) -> tuple[str, str]:
    for line in (output or "").splitlines():
        text = line.strip()
        if text.startswith(_NCCL_ALLOCATION_FAILED_MARKER):
            detail = text.removeprefix(_NCCL_ALLOCATION_FAILED_MARKER).strip()
            detail = _compact_output_line(detail, limit=260).rstrip(".")
            return (
                f"Slurm NCCL benchmark could not allocate GPUs on partition {partition}: {detail}.",
                "slurm_gpu_allocation_failed",
            )
    lowered = str(output or "").lower()
    if "cuda driver version is insufficient for cuda runtime version" in lowered:
        return (
            "NCCL all_reduce_perf failed on partition "
            f"{partition}: CUDA driver version is insufficient for the CUDA runtime used "
            "by /usr/bin/all_reduce_perf_mpi.",
            "cuda_driver_runtime_mismatch",
        )
    if "/usr/bin/all_reduce_perf_mpi" in lowered and (
        "no such file or directory" in lowered or "not found" in lowered
    ):
        return (
            f"NCCL all_reduce_perf binary /usr/bin/all_reduce_perf_mpi was not available "
            f"on partition {partition}.",
            "nccl_binary_missing",
        )
    if "mpirun" in lowered and ("exited" in lowered or "error" in lowered):
        return (
            f"NCCL all_reduce_perf failed under mpirun on partition {partition}.",
            "mpirun_failed",
        )
    return (
        "NCCL all_reduce_perf benchmark failed or did not report "
        f"large-message bandwidth output on partition {partition}.",
        "nccl_benchmark_failed",
    )


def _default_command_runner(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int | None = 300,
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


def _validation_report_file(
    target_ref: str,
    *,
    mode: str = SOPERATOR_DEPLOY_SMOKE_MODE,
) -> str:
    normalized = normalize_component_token(target_ref) or "soperator"
    if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        return f"acceptance-smoke-report-{normalized}.json"
    if mode == SOPERATOR_ACCEPTANCE_BENCHMARK_MODE:
        return f"acceptance-benchmark-report-{normalized}.json"
    return f"deploy-smoke-report-{normalized}.json"


def _resolve_validation_report_file(spec: Mapping[str, Any], *, mode: str) -> str:
    target_ref = str(spec.get("target_ref") or "")
    expected = _validation_report_file(target_ref, mode=mode)
    configured = str(spec.get("report_file") or "").strip()
    if configured and configured != expected:
        raise ValueError(
            f"Soperator {mode} report_file must be {expected}; got {configured}. "
            "Rerender or regenerate the acceptance-test spec with canonical report names."
        )
    return expected


def _cluster_inventory_report_file(target_ref: str) -> str:
    normalized = normalize_component_token(target_ref) or "cluster"
    return f"cluster-inventory-report-{normalized}.json"


def _validation_test_purpose(mode: str) -> str:
    if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        return "acceptance-smoke"
    if mode == SOPERATOR_ACCEPTANCE_BENCHMARK_MODE:
        return "acceptance-benchmark"
    return "deployment-testing"


def _validation_scope(mode: str) -> str:
    if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        return "all-node-slurm-smoke"
    if mode == SOPERATOR_ACCEPTANCE_BENCHMARK_MODE:
        return "slurm-nccl"
    return "soperator-deployment-snapshot"


def _soperator_cluster_name(row: Mapping[str, Any], *, target_ref: str) -> str:
    values = _as_mapping(row.get("values"))
    cluster_name = str(
        values.get("clusterName", "") or values.get("cluster_name", "") or ""
    ).strip()
    return cluster_name or target_ref


def _soperator_chart_version(row: Mapping[str, Any]) -> str:
    return str(row.get("version", "") or row.get("chart_version", "") or "").strip()


def _soperator_storage_mashed_kebab(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z])([A-Z])", r"\1-\2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1-\2", text)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return re.sub(r"-{2,}", "-", text)


def _soperator_jail_storage_from_chart_row(row: Mapping[str, Any]) -> dict[str, str]:
    values = _as_mapping(row.get("values"))
    volume = _as_mapping(values.get("volume"))
    jail = _as_mapping(volume.get("jail"))
    volume_name = (
        _soperator_storage_mashed_kebab(jail.get("name")) or _SOPERATOR_STORAGE_VOLUME_NAME
    )
    local_path = str(jail.get("localPath", "") or _SOPERATOR_STORAGE_LOCAL_PATH).strip()
    return {
        "pvc_name": _soperator_storage_mashed_kebab(f"{volume_name} pvc"),
        "pv_name": _soperator_storage_mashed_kebab(f"{volume_name} pv"),
        "mount_daemonset_name": _soperator_storage_mashed_kebab(f"{volume_name} mount"),
        "local_path": local_path or _SOPERATOR_STORAGE_LOCAL_PATH,
    }


def _soperator_deployment_smoke_enabled_by_target(
    deploy_targets: Sequence[Any],
) -> dict[str, bool]:
    enabled_by_target: dict[str, bool] = {}
    for row in deploy_targets:
        if not isinstance(row, Mapping):
            continue
        target_ref = normalize_component_token(
            row.get("instance_id") or row.get("target_ref") or row.get("id")
        )
        if not target_ref:
            continue
        deployment_testing = _as_mapping(row.get("deployment_testing"))
        soperator = _as_mapping(deployment_testing.get("soperator"))
        smoke = _as_mapping(soperator.get("smoke"))
        enabled_by_target[target_ref] = smoke.get("enabled") is not False
    return enabled_by_target


def normalize_soperator_project_deployment_testing_settings(payload: dict[str, Any]) -> bool:
    plain_payload = to_plain_data(payload)
    if not isinstance(plain_payload, Mapping):
        return False
    charts = _as_sequence(_as_mapping(plain_payload.get("apps")).get("charts"))
    soperator_target_refs = {
        normalize_component_token(row.get("instance_id") or row.get("target_ref"))
        for row in charts
        if isinstance(row, Mapping)
        and str(row.get("id", "") or "").strip() == "soperator"
        and row.get("enabled") is not False
    }
    soperator_target_refs.discard("")

    deploy = payload.get("deploy")
    if not isinstance(deploy, dict):
        return False
    targets = deploy.get("targets")
    if not isinstance(targets, list):
        return False

    changed = False
    for row in targets:
        if not isinstance(row, dict):
            continue
        target_ref = normalize_component_token(
            row.get("instance_id") or row.get("target_ref") or row.get("id")
        )
        deployment_testing = row.get("deployment_testing")
        if target_ref not in soperator_target_refs:
            if isinstance(deployment_testing, dict):
                soperator = deployment_testing.get("soperator")
                if soperator is not None:
                    deployment_testing.pop("soperator", None)
                    changed = True
                    if not deployment_testing:
                        row.pop("deployment_testing", None)
            continue
        if not isinstance(deployment_testing, dict):
            deployment_testing = {}
            row["deployment_testing"] = deployment_testing
            changed = True
        soperator = deployment_testing.get("soperator")
        if not isinstance(soperator, dict):
            soperator = {}
            deployment_testing["soperator"] = soperator
            changed = True
        smoke = soperator.get("smoke")
        if not isinstance(smoke, dict):
            smoke = {}
            soperator["smoke"] = smoke
            changed = True
        if "enabled" not in smoke:
            smoke["enabled"] = True
            changed = True
    return changed


def soperator_cluster_validation_specs(
    payload_or_config: Any,
    *,
    respect_deployment_testing: bool = True,
) -> list[dict[str, Any]]:
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
    smoke_enabled_by_target = _soperator_deployment_smoke_enabled_by_target(deploy_targets)
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
        if respect_deployment_testing and smoke_enabled_by_target.get(target_ref, True) is False:
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
                "jail_storage": _soperator_jail_storage_from_chart_row(row),
                "kube_context": target_context_by_ref.get(target_ref, ""),
                "report_file": _validation_report_file(target_ref),
                "inventory_report_file": _cluster_inventory_report_file(target_ref),
                "readiness_timeout_seconds": _SOPERATOR_CLUSTER_READY_WAIT_SECONDS,
                "readiness_poll_seconds": _SOPERATOR_CLUSTER_READY_POLL_SECONDS,
                "required": True,
                "mode": SOPERATOR_DEPLOY_SMOKE_MODE,
            }
        )
    return specs


def soperator_acceptance_smoke_specs(
    payload_or_config: Any,
    *,
    batch_size: int = 128,
    concurrency: int = 8,
    continue_on_failure: bool = True,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for spec in soperator_cluster_validation_specs(
        payload_or_config,
        respect_deployment_testing=False,
    ):
        target_ref = str(spec.get("target_ref", "") or "")
        item = dict(spec)
        item.update(
            {
                "mode": SOPERATOR_ACCEPTANCE_SMOKE_MODE,
                "name": f"Soperator acceptance smoke test ({target_ref})",
                "report_file": _validation_report_file(
                    target_ref,
                    mode=SOPERATOR_ACCEPTANCE_SMOKE_MODE,
                ),
                "batch_size": max(1, int(batch_size)),
                "concurrency": max(1, int(concurrency)),
                "continue_on_failure": bool(continue_on_failure),
                "required": True,
            }
        )
        specs.append(item)
    return specs


def soperator_acceptance_benchmark_specs(
    payload_or_config: Any,
    *,
    max_nodes: int | None = None,
    timeout: str | None = None,
    average_bus_bandwidth_threshold_gbps: float | None = None,
) -> list[dict[str, Any]]:
    resolved_max_nodes = _positive_int_setting(
        max_nodes,
        field_label="acceptance-test benchmark --max-nodes",
    )
    resolved_timeout = str(timeout or "").strip() or None
    if resolved_timeout:
        _optional_duration_seconds(
            resolved_timeout,
            field_label="acceptance-test benchmark --timeout",
        )
    resolved_threshold = _non_negative_float_setting(
        average_bus_bandwidth_threshold_gbps,
        field_label="acceptance-test benchmark --average-bus-bandwidth-threshold-gbps",
    )
    specs: list[dict[str, Any]] = []
    for spec in soperator_cluster_validation_specs(
        payload_or_config,
        respect_deployment_testing=False,
    ):
        target_ref = str(spec.get("target_ref", "") or "")
        item = dict(spec)
        item.update(
            {
                "mode": SOPERATOR_ACCEPTANCE_BENCHMARK_MODE,
                "name": f"Soperator NCCL benchmark ({target_ref})",
                "report_file": _validation_report_file(
                    target_ref,
                    mode=SOPERATOR_ACCEPTANCE_BENCHMARK_MODE,
                ),
                "include_nccl_benchmark": True,
                "max_nodes": resolved_max_nodes,
                "timeout": resolved_timeout,
                "average_bus_bandwidth_threshold_gbps": resolved_threshold,
                "required": True,
            }
        )
        specs.append(item)
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


def _positive_int_setting(value: Any, *, field_label: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_label} must be a positive integer")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_label} must be a positive integer")
    return parsed


def _non_negative_float_setting(value: Any, *, field_label: str) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_label} must be >= 0")
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_label} must be >= 0") from exc
    if parsed < 0:
        raise ValueError(f"{field_label} must be >= 0")
    return parsed


def _optional_duration_seconds(value: Any, *, field_label: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parse_go_duration_seconds(text)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a Go-style duration like '20m'") from exc


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
    *,
    timeout_seconds: int = _SOPERATOR_RUNTIME_COMMAND_TIMEOUT_SECONDS,
) -> str:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    payload = _json_command(
        runner,
        _kubectl_args(spec, "-n", namespace, "get", "pods", "-o", "json"),
        timeout_seconds=timeout_seconds,
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
    timeout_seconds: int | None = 300,
) -> SoperatorValidationCommandResult:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    effective_timeout = (
        _SOPERATOR_RUNTIME_COMMAND_TIMEOUT_SECONDS
        if timeout_seconds is None
        else max(int(timeout_seconds), 1)
    )
    login_timeout_seconds = min(
        effective_timeout,
        _SOPERATOR_RUNTIME_COMMAND_TIMEOUT_SECONDS,
    )
    pod = _login_pod_name(runner, spec, timeout_seconds=login_timeout_seconds)
    if not pod:
        return SoperatorValidationCommandResult(tuple(args), 1, "", "login pod not found")
    return runner(
        _kubectl_args(
            spec,
            "-n",
            namespace,
            "exec",
            pod,
            "--",
            "chroot",
            "/mnt/jail",
            *[str(item) for item in args],
        ),
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
        item["stdout"] = _compact_output_lines(stdout)
    if stderr:
        item["stderr"] = _compact_output_lines(stderr)
    checks.append(item)


def _check_sconfigcontroller_deployment(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(
        spec,
        "-n",
        namespace,
        "get",
        "deployment",
        "sconfigcontroller",
        "-o",
        "json",
    )
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
    if result.returncode != 0:
        _append_check(
            checks,
            name="Soperator SConfigController deployment",
            status="failed",
            summary="sconfigcontroller deployment is not readable.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        _append_check(
            checks,
            name="Soperator SConfigController deployment",
            status="failed",
            summary=f"sconfigcontroller deployment returned invalid JSON: {exc}.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    spec_payload = _as_mapping(_as_mapping(payload).get("spec"))
    status_payload = _as_mapping(_as_mapping(payload).get("status"))
    desired = int(spec_payload.get("replicas") or status_payload.get("replicas") or 0)
    ready = int(status_payload.get("readyReplicas") or 0)
    available = int(status_payload.get("availableReplicas") or 0)
    updated = int(status_payload.get("updatedReplicas") or 0)
    if desired <= 0:
        _append_check(
            checks,
            name="Soperator SConfigController deployment",
            status="failed",
            summary="sconfigcontroller deployment has no desired replicas.",
            command=command,
            stdout=result.stdout,
        )
        return
    if available <= 0 or ready <= 0:
        _append_check(
            checks,
            name="Soperator SConfigController deployment",
            status="failed",
            summary=(
                "sconfigcontroller deployment has no available ready replicas "
                f"(ready={ready}/{desired}, available={available}/{desired}, "
                f"updated={updated}/{desired})."
            ),
            command=command,
            stdout=result.stdout,
        )
        return
    _append_check(
        checks,
        name="Soperator SConfigController deployment",
        status="passed",
        summary=(
            "sconfigcontroller deployment is visible with "
            f"ready={ready}/{desired}, available={available}/{desired}, "
            f"updated={updated}/{desired}."
        ),
        command=command,
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
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
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
        condition_status = str(condition.get("status", "") or "").strip().lower()
        if condition_status == "true":
            continue
        message = str(condition.get("message", "") or "").strip()
        if message:
            return message
    return str(status.get("message", "") or "").strip()


def _soperator_pod_event_messages(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    *,
    pod_name: str,
) -> list[str]:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(
        spec,
        "-n",
        namespace,
        "get",
        "events",
        "--field-selector",
        f"involvedObject.name={pod_name}",
        "-o",
        "json",
    )
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return []
    messages: list[str] = []
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        event_type = str(item.get("type", "") or "").strip().lower()
        reason = str(item.get("reason", "") or "").strip()
        message = str(item.get("message", "") or "").strip()
        if event_type and event_type != "warning" and reason not in {"FailedMount"}:
            continue
        if not reason and not message:
            continue
        if reason and message:
            messages.append(f"{reason}: {message}")
        else:
            messages.append(reason or message)
    return [_compact_output(message, limit=300) for message in messages[-3:]]


def _soperator_jail_storage(spec: Mapping[str, Any]) -> _SoperatorJailStorage:
    raw = _as_mapping(spec.get("jail_storage"))
    return _SoperatorJailStorage(
        pvc_name=str(raw.get("pvc_name") or _SOPERATOR_STORAGE_PVC_NAME).strip()
        or _SOPERATOR_STORAGE_PVC_NAME,
        pv_name=str(raw.get("pv_name") or _SOPERATOR_STORAGE_PV_NAME).strip()
        or _SOPERATOR_STORAGE_PV_NAME,
        mount_daemonset_name=str(
            raw.get("mount_daemonset_name") or _SOPERATOR_STORAGE_MOUNT_DAEMONSET_NAME
        ).strip()
        or _SOPERATOR_STORAGE_MOUNT_DAEMONSET_NAME,
        local_path=str(raw.get("local_path") or _SOPERATOR_STORAGE_LOCAL_PATH).strip()
        or _SOPERATOR_STORAGE_LOCAL_PATH,
    )


def _pod_uses_soperator_jail_pvc(item: Mapping[str, Any], *, pvc_name: str) -> bool:
    spec = _as_mapping(item.get("spec"))
    for volume in _as_sequence(spec.get("volumes")):
        if not isinstance(volume, Mapping):
            continue
        claim = _as_mapping(volume.get("persistentVolumeClaim"))
        claim_name = str(claim.get("claimName", "") or "").strip()
        if claim_name == pvc_name:
            return True
    return False


def _pending_message_is_storage_related(message: str, *, storage: _SoperatorJailStorage) -> bool:
    text = str(message or "").lower()
    markers = (
        *_SOPERATOR_STORAGE_PENDING_MARKERS,
        storage.pvc_name.lower(),
        storage.pv_name.lower(),
        storage.mount_daemonset_name.lower(),
        storage.local_path.lower(),
    )
    return any(marker and marker in text for marker in markers)


def _pending_soperator_pods(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    *,
    include_events: bool = False,
) -> tuple[list[_PendingSoperatorPod], Sequence[str]]:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    storage = _soperator_jail_storage(spec)
    command = _kubectl_args(spec, "-n", namespace, "get", "pods", "-o", "json")
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
    if result.returncode != 0:
        return [], command
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return [], command
    pending: list[_PendingSoperatorPod] = []
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
        event_messages = (
            _soperator_pod_event_messages(runner, spec, pod_name=name) if include_events else []
        )
        if event_messages:
            event_text = "; ".join(event_messages)
            message = f"{message}; {event_text}" if message else event_text
        storage_related = _pod_uses_soperator_jail_pvc(
            item,
            pvc_name=storage.pvc_name,
        ) or _pending_message_is_storage_related(
            message,
            storage=storage,
        )
        pending.append(
            _PendingSoperatorPod(
                name=name,
                message=message or "Pending",
                storage_related=storage_related,
            )
        )
    return pending, command


def _soperator_pods_payload(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Sequence[str]]:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "pods", "-o", "json")
    payload = _json_command(
        runner,
        command,
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
    )
    return payload, command


def _int_from_mapping(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _populate_jail_job_name(spec: Mapping[str, Any]) -> str:
    cluster_name = str(spec.get("cluster_name", "") or spec.get("target_ref") or "").strip()
    if not cluster_name:
        return ""
    return f"{cluster_name}-{_SOPERATOR_POPULATE_JAIL_JOB_SUFFIX}"


def _job_succeeded(payload: Mapping[str, Any]) -> bool:
    spec = _as_mapping(payload.get("spec"))
    status = _as_mapping(payload.get("status"))
    completions = max(_int_from_mapping(spec.get("completions") or 1), 1)
    return _int_from_mapping(status.get("succeeded")) >= completions


def _job_failed(payload: Mapping[str, Any]) -> bool:
    spec = _as_mapping(payload.get("spec"))
    status = _as_mapping(payload.get("status"))
    failed = _int_from_mapping(status.get("failed"))
    backoff_limit = _int_from_mapping(spec.get("backoffLimit", 6))
    return failed > 0 and failed > backoff_limit


def _pod_name(item: Mapping[str, Any]) -> str:
    return str(_as_mapping(item.get("metadata")).get("name", "") or "").strip()


def _pod_node_name(item: Mapping[str, Any]) -> str:
    return str(_as_mapping(item.get("spec")).get("nodeName", "") or "").strip()


def _pod_phase(item: Mapping[str, Any]) -> str:
    return str(_as_mapping(item.get("status")).get("phase", "") or "").strip()


def _pod_owned_by_job(item: Mapping[str, Any], *, job_name: str) -> bool:
    metadata = _as_mapping(item.get("metadata"))
    labels = _as_mapping(metadata.get("labels"))
    if str(labels.get("job-name", "") or "").strip() == job_name:
        return True
    if str(labels.get("batch.kubernetes.io/job-name", "") or "").strip() == job_name:
        return True
    for owner in _as_sequence(metadata.get("ownerReferences")):
        if not isinstance(owner, Mapping):
            continue
        if str(owner.get("kind", "") or "").strip() != "Job":
            continue
        if str(owner.get("name", "") or "").strip() == job_name:
            return True
    return False


def _active_populate_jail_pods(
    payload: Mapping[str, Any],
    *,
    job_name: str,
) -> list[Mapping[str, Any]]:
    pods: list[Mapping[str, Any]] = []
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        if not _pod_owned_by_job(item, job_name=job_name):
            continue
        phase = _pod_phase(item)
        if phase in {"Succeeded", "Failed"}:
            continue
        if not _pod_name(item) or not _pod_node_name(item):
            continue
        pods.append(item)
    return pods


def _jail_mount_pod_name_for_node(
    payload: Mapping[str, Any],
    *,
    storage: _SoperatorJailStorage,
    node_name: str,
) -> str:
    pod_prefix = f"{storage.mount_daemonset_name}-"
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        name = _pod_name(item)
        if not name.startswith(pod_prefix):
            continue
        if _pod_node_name(item) != node_name:
            continue
        phase = _pod_phase(item)
        if phase and phase != "Running":
            continue
        return name
    return ""


def _host_jail_sentinel_path(storage: _SoperatorJailStorage) -> str:
    local_path = "/" + storage.local_path.strip("/")
    return f"/host{local_path.rstrip('/')}/{_SOPERATOR_POPULATE_JAIL_SENTINEL_FILE}"


def _wait_for_populate_jail_job_complete(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    *,
    job_name: str,
    timeout_seconds: float,
) -> tuple[bool, str]:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "job", job_name, "-o", "json")
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_detail = "job did not report completion"
    while True:
        result = runner(
            command,
            timeout_seconds=_snapshot_command_timeout_seconds(spec),
            check=False,
        )
        if result.returncode != 0:
            last_detail = _command_detail(result)
        else:
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                last_detail = f"invalid job JSON: {exc}"
            else:
                if isinstance(payload, Mapping) and _job_succeeded(payload):
                    return True, "job completed"
                if isinstance(payload, Mapping) and _job_failed(payload):
                    return False, "job exceeded its backoff limit"
                status = _as_mapping(_as_mapping(payload).get("status"))
                last_detail = (
                    "job status "
                    f"active={_int_from_mapping(status.get('active'))}, "
                    f"succeeded={_int_from_mapping(status.get('succeeded'))}, "
                    f"failed={_int_from_mapping(status.get('failed'))}"
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, last_detail
        time.sleep(min(_SOPERATOR_CLUSTER_READY_POLL_SECONDS, remaining))


def _recover_stuck_populate_jail_job(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    job_name = _populate_jail_job_name(spec)
    if not job_name:
        return
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    job_command = _kubectl_args(spec, "-n", namespace, "get", "job", job_name, "-o", "json")
    try:
        job_payload = _json_command(
            runner,
            job_command,
            timeout_seconds=_snapshot_command_timeout_seconds(spec),
        )
    except RuntimeError:
        return
    if _job_succeeded(job_payload):
        return
    status = _as_mapping(job_payload.get("status"))
    if _int_from_mapping(status.get("active")) <= 0:
        return
    try:
        pods_payload, _pods_command = _soperator_pods_payload(runner, spec)
    except RuntimeError:
        return
    active_pods = _active_populate_jail_pods(pods_payload, job_name=job_name)
    if not active_pods:
        return
    storage = _soperator_jail_storage(spec)
    sentinel_path = _host_jail_sentinel_path(storage)
    for pod in active_pods:
        pod_name = _pod_name(pod)
        node_name = _pod_node_name(pod)
        mount_pod_name = _jail_mount_pod_name_for_node(
            pods_payload,
            storage=storage,
            node_name=node_name,
        )
        if not mount_pod_name:
            continue
        sentinel_command = _kubectl_args(
            spec,
            "-n",
            namespace,
            "exec",
            mount_pod_name,
            "--",
            "sh",
            "-lc",
            f"test -s {shlex.quote(sentinel_path)} && cat {shlex.quote(sentinel_path)}",
        )
        sentinel = runner(
            sentinel_command,
            timeout_seconds=_snapshot_command_timeout_seconds(spec),
            check=False,
        )
        if sentinel.returncode != 0:
            continue
        delete_command = _kubectl_args(
            spec,
            "-n",
            namespace,
            "delete",
            "pod",
            pod_name,
            "--wait=false",
        )
        deleted = runner(
            delete_command,
            timeout_seconds=_snapshot_command_timeout_seconds(spec),
            check=False,
        )
        if deleted.returncode != 0:
            _append_check(
                checks,
                name="Populate jail recovery",
                status="failed",
                summary=(
                    f"populate-jail job {job_name} appears complete on node {node_name}, "
                    f"but deleting stuck pod {pod_name} failed: "
                    f"{_compact_output(_command_detail(deleted), limit=300)}"
                ),
                command=delete_command,
                stdout=deleted.stdout,
                stderr=deleted.stderr,
                extra={
                    "job_name": job_name,
                    "pod_name": pod_name,
                    "node_name": node_name,
                    "sentinel_path": sentinel_path,
                },
            )
            return
        timeout_seconds = _float_setting(
            spec.get("populate_jail_recovery_timeout_seconds"),
            default=float(_SOPERATOR_POPULATE_JAIL_RECOVERY_TIMEOUT_SECONDS),
        )
        recovered, detail = _wait_for_populate_jail_job_complete(
            runner,
            spec,
            job_name=job_name,
            timeout_seconds=timeout_seconds,
        )
        _append_check(
            checks,
            name="Populate jail recovery",
            status="passed" if recovered else "failed",
            summary=(
                f"populate-jail job {job_name} had written {sentinel_path} on node "
                f"{node_name} but pod {pod_name} stayed active; deleted the pod and "
                + ("the job completed." if recovered else f"the job did not complete: {detail}.")
            ),
            command=delete_command,
            extra={
                "job_name": job_name,
                "pod_name": pod_name,
                "node_name": node_name,
                "jail_mount_pod": mount_pod_name,
                "sentinel_path": sentinel_path,
                "sentinel": _compact_output(sentinel.stdout, limit=200),
            },
        )
        return


def _container_problem(status: Mapping[str, Any], *, init_container: bool) -> str:
    name = str(status.get("name", "") or "container").strip()
    label = "init container" if init_container else "container"
    state = _as_mapping(status.get("state"))
    waiting = _as_mapping(state.get("waiting"))
    waiting_reason = str(waiting.get("reason", "") or "").strip()
    if waiting_reason in _SOPERATOR_POD_UNHEALTHY_WAITING_REASONS:
        message = str(waiting.get("message", "") or "").strip()
        suffix = f": {_compact_output(message, limit=220)}" if message else ""
        return f"{label} {name} waiting {waiting_reason}{suffix}"
    terminated = _as_mapping(state.get("terminated"))
    exit_code = _int_from_mapping(terminated.get("exitCode"))
    terminated_reason = str(terminated.get("reason", "") or "").strip()
    if exit_code != 0 and (
        not terminated_reason or terminated_reason in _SOPERATOR_POD_UNHEALTHY_TERMINATED_REASONS
    ):
        message = str(terminated.get("message", "") or "").strip()
        reason_text = f" {terminated_reason}" if terminated_reason else ""
        suffix = f": {_compact_output(message, limit=220)}" if message else ""
        return f"{label} {name} terminated{reason_text} exit_code={exit_code}{suffix}"
    return ""


def _unhealthy_soperator_pods(payload: Mapping[str, Any]) -> list[_UnhealthySoperatorPod]:
    unhealthy: list[_UnhealthySoperatorPod] = []
    for item in _as_sequence(payload.get("items")):
        if not isinstance(item, Mapping):
            continue
        name = _pod_name(item)
        if not name:
            continue
        status = _as_mapping(item.get("status"))
        phase = str(status.get("phase", "") or "").strip()
        problems: list[str] = []
        if phase == "Failed":
            reason = str(status.get("reason", "") or "").strip()
            message = str(status.get("message", "") or "").strip()
            detail = ": ".join(part for part in (reason, message) if part)
            problems.append(detail or "pod phase Failed")
        for raw_status in _as_sequence(status.get("initContainerStatuses")):
            if isinstance(raw_status, Mapping):
                problem = _container_problem(raw_status, init_container=True)
                if problem:
                    problems.append(problem)
        for raw_status in _as_sequence(status.get("containerStatuses")):
            if isinstance(raw_status, Mapping):
                problem = _container_problem(raw_status, init_container=False)
                if problem:
                    problems.append(problem)
        if problems:
            unhealthy.append(
                _UnhealthySoperatorPod(
                    name=name,
                    message="; ".join(problems[:3]),
                )
            )
    return unhealthy


def _check_soperator_pod_health_snapshot(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    try:
        payload, command = _soperator_pods_payload(runner, spec)
    except RuntimeError as exc:
        _append_check(
            checks,
            name="Soperator pod health snapshot",
            status="failed",
            summary=f"Soperator pods are not readable for health snapshot: {exc}.",
        )
        return
    unhealthy = _unhealthy_soperator_pods(payload)
    if not unhealthy:
        _append_check(
            checks,
            name="Soperator pod health snapshot",
            status="passed",
            summary="No failed or crash-looping Soperator pods were visible in the fast snapshot.",
            command=command,
        )
        return
    shown = "; ".join(f"{item.name}: {item.message}" for item in unhealthy[:5])
    if len(unhealthy) > 5:
        shown += f"; +{len(unhealthy) - 5} more"
    _append_check(
        checks,
        name="Soperator pod health snapshot",
        status="failed",
        summary="Failed or crash-looping Soperator pod(s) visible in the fast snapshot: " + shown,
        command=command,
        extra={
            "unhealthy_pods": [{"name": item.name, "message": item.message} for item in unhealthy],
        },
    )


def _soperator_storage_unready_summaries(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
) -> list[str]:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    storage = _soperator_jail_storage(spec)
    summaries: list[str] = []

    pvc_command = _kubectl_args(
        spec,
        "-n",
        namespace,
        "get",
        "pvc",
        storage.pvc_name,
        "-o",
        "json",
    )
    pvc_result = runner(
        pvc_command,
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
        check=False,
    )
    if pvc_result.returncode != 0:
        summaries.append(
            f"pvc/{storage.pvc_name} lookup failed: "
            f"{_compact_output(_command_detail(pvc_result), limit=300)}"
        )
    else:
        try:
            pvc_payload = json.loads(pvc_result.stdout or "{}")
        except json.JSONDecodeError:
            pvc_payload = {}
        phase = str(_as_mapping(pvc_payload.get("status")).get("phase", "") or "").strip()
        if phase and phase != "Bound":
            summaries.append(f"pvc/{storage.pvc_name}={phase}")

    pv_command = _kubectl_args(spec, "get", "pv", storage.pv_name, "-o", "json")
    pv_result = runner(
        pv_command,
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
        check=False,
    )
    if pv_result.returncode != 0:
        summaries.append(
            f"pv/{storage.pv_name} lookup failed: "
            f"{_compact_output(_command_detail(pv_result), limit=300)}"
        )
    else:
        try:
            pv_payload = json.loads(pv_result.stdout or "{}")
        except json.JSONDecodeError:
            pv_payload = {}
        phase = str(_as_mapping(pv_payload.get("status")).get("phase", "") or "").strip()
        if phase and phase != "Bound":
            summaries.append(f"pv/{storage.pv_name}={phase}")

    daemonset_command = _kubectl_args(
        spec,
        "-n",
        namespace,
        "get",
        "daemonset",
        storage.mount_daemonset_name,
        "-o",
        "json",
    )
    daemonset_result = runner(
        daemonset_command,
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
        check=False,
    )
    if daemonset_result.returncode != 0:
        summaries.append(
            f"daemonset/{storage.mount_daemonset_name} lookup failed: "
            f"{_compact_output(_command_detail(daemonset_result), limit=300)}"
        )
    else:
        try:
            daemonset_payload = json.loads(daemonset_result.stdout or "{}")
        except json.JSONDecodeError:
            daemonset_payload = {}
        status = _as_mapping(daemonset_payload.get("status"))
        desired = int(status.get("desiredNumberScheduled") or 0)
        ready = int(status.get("numberReady") or 0)
        available = int(status.get("numberAvailable") or ready)
        if desired > 0 and (ready < desired or available < desired):
            summaries.append(
                f"daemonset/{storage.mount_daemonset_name} "
                f"ready={ready}/{desired} available={available}/{desired}"
            )
    return summaries


def _wait_for_soperator_storage_and_pods(
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
        storage_unready = _soperator_storage_unready_summaries(runner, spec)
        pending, _command = _pending_soperator_pods(runner, spec, include_events=True)
        if not storage_unready and not pending:
            return
        if pending and not storage_unready and not any(item.storage_related for item in pending):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(max(poll_seconds, 0.1), remaining))


def _check_soperator_storage_readiness(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
    *,
    fail_on_unready: bool = True,
) -> None:
    unready = _soperator_storage_unready_summaries(runner, spec)
    if not unready:
        _append_check(
            checks,
            name="Soperator storage snapshot",
            status="passed",
            summary="Soperator jail storage objects are visible and ready.",
        )
        return
    shown = "; ".join(unready[:5])
    if len(unready) > 5:
        shown += f"; +{len(unready) - 5} more"
    _append_check(
        checks,
        name="Soperator storage snapshot",
        status="failed" if fail_on_unready else "skipped",
        summary="Soperator jail storage is not ready in the fast snapshot: " + shown,
        extra={"progressing": not fail_on_unready} if not fail_on_unready else None,
    )


def _check_soperator_pod_scheduling(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
    *,
    fail_on_pending: bool = True,
) -> None:
    pending_items, command = _pending_soperator_pods(runner, spec, include_events=True)
    pending = [f"{item.name}: {item.message}" for item in pending_items]
    if not pending:
        _append_check(
            checks,
            name="Soperator pod scheduling snapshot",
            status="passed",
            summary="No Pending Soperator pods were visible in the fast snapshot.",
            command=command,
        )
        return
    shown = "; ".join(pending[:5])
    if len(pending) > 5:
        shown += f"; +{len(pending) - 5} more"
    has_storage_failure = any(item.storage_related for item in pending_items)
    status = "failed" if fail_on_pending or has_storage_failure else "skipped"
    _append_check(
        checks,
        name="Soperator pod scheduling snapshot",
        status=status,
        summary="Pending Soperator pod(s) visible in the fast snapshot: " + shown,
        command=command,
        extra={
            "progressing": status == "skipped",
            "storage_related": has_storage_failure,
        },
    )


def _check_slurmcluster(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
    *,
    require_available: bool = True,
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    cluster_name = str(spec.get("cluster_name", "") or spec.get("target_ref") or "").strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "slurmclusters", "-o", "json")
    payload = _json_command(
        runner,
        command,
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
    )
    check_name = "SlurmCluster availability" if require_available else "SlurmCluster visibility"
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
    if target_phase and not require_available:
        _append_check(
            checks,
            name=check_name,
            status="passed",
            summary=f"target SlurmCluster {cluster_name} is visible with phase {target_phase}.",
            command=command,
            extra={"phase": target_phase},
        )
        return
    if target_phase == "Available":
        _append_check(
            checks,
            name=check_name,
            status="passed",
            summary=f"target SlurmCluster {cluster_name} is Available.",
            command=command,
            extra={"phase": target_phase},
        )
    else:
        summary = (
            f"target SlurmCluster {cluster_name} phase is {target_phase or 'missing'}; "
            f"visible clusters: {', '.join(clusters) or 'none'}."
        )
        _append_check(
            checks,
            name=check_name,
            status="failed",
            summary=summary,
            command=command,
            extra={"phase": target_phase or "missing"},
        )


def _resource_item_name(item: Mapping[str, Any]) -> str:
    return str(_as_mapping(item.get("metadata")).get("name", "") or "").strip()


def _nodeset_gpu_driver_jail_mount_ok(spec: Mapping[str, Any]) -> bool:
    volumes = _as_mapping(_as_mapping(spec.get("slurmd")).get("volumes"))
    mounts = volumes.get("customVolumeMounts")
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes, bytearray)):
        return False
    for raw_mount in mounts:
        if not isinstance(raw_mount, Mapping):
            continue
        name = str(raw_mount.get("name", "") or "").strip()
        mount_path = str(raw_mount.get("mountPath", "") or "").strip()
        if name != SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME:
            continue
        if mount_path != SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH:
            return False
        if raw_mount.get("readOnly") is not True:
            return False
        volume_source = _as_mapping(raw_mount.get("volumeSource"))
        host_path = _as_mapping(volume_source.get("hostPath"))
        return str(host_path.get("path", "") or "").strip() == SOPERATOR_GPU_DRIVER_JAIL_HOST_PATH
    return False


def _check_gpu_driver_jail_nodeset_contract(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "nodesets", "-o", "json")
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
    if result.returncode != 0:
        _append_check(
            checks,
            name="GPU driver jail NodeSet contract",
            status="failed",
            summary="Soperator NodeSet resources are not readable for GPU driver jail checks.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        _append_check(
            checks,
            name="GPU driver jail NodeSet contract",
            status="failed",
            summary=f"Soperator NodeSet query returned invalid JSON: {exc}.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    gpu_nodesets: list[dict[str, Any]] = []
    failed: list[str] = []
    for item in _as_sequence(_as_mapping(payload).get("items")):
        if not isinstance(item, Mapping):
            continue
        nodeset_spec = _as_mapping(item.get("spec"))
        if not soperator_nodeset_is_gpu_worker(nodeset_spec):
            continue
        name = _resource_item_name(item) or "worker"
        mount_ok = _nodeset_gpu_driver_jail_mount_ok(nodeset_spec)
        gpu_nodesets.append(
            {
                "name": name,
                "driver_root_mount": mount_ok,
            }
        )
        if not mount_ok:
            failed.append(name)
    if not gpu_nodesets:
        _append_check(
            checks,
            name="GPU driver jail NodeSet contract",
            status="skipped",
            summary="No GPU worker NodeSets were visible.",
            command=command,
            extra={"gpu_driver_jail_nodesets": []},
        )
        return
    if failed:
        _append_check(
            checks,
            name="GPU driver jail NodeSet contract",
            status="failed",
            summary=(
                "GPU worker NodeSet(s) are missing the adapter-owned read-only "
                f"{SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME} host driver root mount: "
                + ", ".join(failed)
                + "."
            ),
            command=command,
            extra={"gpu_driver_jail_nodesets": gpu_nodesets},
        )
        return
    _append_check(
        checks,
        name="GPU driver jail NodeSet contract",
        status="passed",
        summary=("GPU worker NodeSets include the adapter-owned read-only host driver root mount."),
        command=command,
        extra={"gpu_driver_jail_nodesets": gpu_nodesets},
    )


def _check_nodeset_visibility(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    namespace = str(spec.get("namespace", "") or SOPERATOR_NAMESPACE).strip()
    command = _kubectl_args(spec, "-n", namespace, "get", "nodesets", "-o", "json")
    result = runner(command, timeout_seconds=_snapshot_command_timeout_seconds(spec), check=False)
    if result.returncode != 0:
        _append_check(
            checks,
            name="NodeSet visibility",
            status="failed",
            summary="Soperator NodeSet resources are not readable.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        _append_check(
            checks,
            name="NodeSet visibility",
            status="failed",
            summary=f"Soperator NodeSet query returned invalid JSON: {exc}.",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return
    nodesets: list[dict[str, str]] = []
    for item in _as_sequence(_as_mapping(payload).get("items")):
        if not isinstance(item, Mapping):
            continue
        name = _resource_item_name(item)
        if not name:
            continue
        status = _as_mapping(item.get("status"))
        phase = str(status.get("phase", "") or status.get("state", "") or "present").strip()
        nodesets.append({"name": name, "phase": phase})
    if not nodesets:
        _append_check(
            checks,
            name="NodeSet visibility",
            status="failed",
            summary="No Soperator NodeSet resources are visible.",
            command=command,
        )
        return
    shown = ", ".join(f"{item['name']}={item['phase']}" for item in nodesets[:8])
    if len(nodesets) > 8:
        shown += f", +{len(nodesets) - 8} more"
    _append_check(
        checks,
        name="NodeSet visibility",
        status="passed",
        summary=f"Soperator NodeSets are visible: {shown}.",
        command=command,
        extra={"nodeset_count": len(nodesets), "nodesets": nodesets},
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
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
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


def _write_slurm_inventory_report(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    *,
    reports_dir: Path,
) -> None:
    report_file = str(spec.get("inventory_report_file", "") or "").strip()
    if not report_file:
        return
    report_path = reports_dir / report_file
    try:
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-N", "-h", "-o", "%P|%N|%t|%G"),
        timeout_seconds=_snapshot_command_timeout_seconds(spec),
    )
    partitions: dict[str, dict[str, Any]] = {}
    inventory_error = ""
    if sinfo.returncode != 0:
        inventory_error = _command_detail(sinfo)
    else:
        for line in sinfo.stdout.splitlines():
            parts = [item.strip() for item in line.split("|")]
            if len(parts) < 4:
                continue
            partition = _partition_name(parts[0])
            node_name = parts[1]
            if not partition or partition == "hidden" or not node_name:
                continue
            bucket = partitions.setdefault(
                partition,
                {
                    "partition": partition,
                    "node_count": 0,
                    "gpu_node_count": 0,
                    "nodes": [],
                },
            )
            gpu_count = _gpu_count_from_gres(parts[3])
            bucket["node_count"] += 1
            if gpu_count > 0:
                bucket["gpu_node_count"] += 1
            bucket["nodes"].append(
                {
                    "node_name": node_name,
                    "state": parts[2],
                    "gres": parts[3],
                    "gpu_count": gpu_count,
                }
            )

    slurm_inventory: dict[str, Any] = {
        "kind": "slurm_inventory",
        "target_ref": str(spec.get("target_ref", "") or ""),
        "cluster_name": str(spec.get("cluster_name", "") or ""),
        "test_purpose": "inventory",
        "mode": "deploy",
        "scope": "read-only-slurm-inventory",
        "read_only": True,
        "partitions": [
            {
                **partition,
                "nodes": sorted(
                    partition.get("nodes", []),
                    key=lambda item: str(item.get("node_name", "")),
                ),
            }
            for partition in sorted(partitions.values(), key=lambda item: item["partition"])
        ],
    }
    if inventory_error:
        slurm_inventory["error"] = inventory_error

    existing.setdefault("target_ref", str(spec.get("target_ref", "") or ""))
    existing.setdefault("schema", "nebius-cxcli-cluster-inventory/v1")
    existing.setdefault("kind", "cluster_inventory")
    existing.setdefault("validation", "Cluster inventory")
    existing.setdefault("test_purpose", "inventory")
    existing.setdefault("mode", "deploy")
    existing.setdefault("scope", "read-only-inventory")
    existing.setdefault("read_only", True)
    existing["slurm"] = slurm_inventory
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _partition_has_powered_down_nodes(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    *,
    partition: str,
) -> bool:
    args = ["sinfo", "-h", "-o", "%t"]
    if partition:
        args[2:2] = ["-p", partition]
    sinfo = _exec_login(
        runner,
        spec,
        tuple(args),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        return False
    states = [line.strip().lower() for line in sinfo.stdout.splitlines() if line.strip()]
    return any("~" in state or "%" in state or "power" in state for state in states)


def _run_srun_batches(
    *,
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    partition: str,
    batches: Sequence[Sequence[str]],
    script_factory: Callable[[Sequence[str]], str],
    timeout_seconds: int,
    stop_on_failure: bool = False,
    batch_succeeded: Callable[[_SrunBatchResult], bool] | None = None,
) -> list[_SrunBatchResult]:
    concurrency = _acceptance_concurrency(spec)

    def _run_batch(nodes: Sequence[str]) -> _SrunBatchResult:
        node_tuple = tuple(str(node).strip() for node in nodes if str(node).strip())
        result = _exec_login(
            runner,
            spec,
            ("bash", "-lc", script_factory(node_tuple)),
            timeout_seconds=timeout_seconds,
        )
        return _SrunBatchResult(partition=partition, nodes=node_tuple, result=result)

    if stop_on_failure:
        results: list[_SrunBatchResult] = []
        for nodes in batches:
            batch_result = _run_batch(nodes)
            results.append(batch_result)
            if batch_succeeded is not None and not batch_succeeded(batch_result):
                break
        return results

    if concurrency <= 1 or len(batches) <= 1:
        return [_run_batch(nodes) for nodes in batches]

    ordered: dict[int, _SrunBatchResult] = {}
    with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
        futures = {executor.submit(_run_batch, nodes): index for index, nodes in enumerate(batches)}
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()
    return [ordered[index] for index in sorted(ordered)]


def _gpu_driver_jail_hosts(stdout: str) -> tuple[str, ...]:
    hosts: list[str] = []
    for line in stdout.splitlines():
        match = _GPU_DRIVER_JAIL_HOST_RE.search(line.strip())
        if match:
            hosts.append(match.group("host"))
    return tuple(hosts)


def _check_slurm_gpu_driver_jail(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
    *,
    partition: str = "",
    max_nodes: int | None = None,
    check_name: str = "Slurm GPU driver jail",
) -> bool:
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-N", "-h", "-o", "%P|%N|%G"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        _append_check(
            checks,
            name=check_name,
            status="failed",
            summary="sinfo could not list Slurm GPU nodes for GPU driver jail checks.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )
        return False
    partitions = _gpu_partition_nodes(sinfo.stdout)
    if partition:
        partitions = {partition: partitions.get(partition, [])}
    partitions = {name: nodes for name, nodes in partitions.items() if nodes}
    if not partitions:
        _append_check(
            checks,
            name=check_name,
            status="skipped",
            summary="No Slurm GPU nodes were available for GPU driver jail checks.",
        )
        return True

    batch_size = _acceptance_batch_size(spec)
    continue_on_failure = _acceptance_continue_on_failure(spec)
    outputs: list[str] = []
    errors: list[str] = []
    failure_details: list[str] = []
    partition_reports: list[dict[str, Any]] = []
    failed_partitions: list[str] = []
    remaining_nodes = max_nodes if max_nodes is not None else None
    stopped = False
    for partition_name, raw_nodes in sorted(partitions.items()):
        nodes = list(raw_nodes)
        if remaining_nodes is not None:
            if remaining_nodes <= 0:
                break
            nodes = nodes[:remaining_nodes]
            remaining_nodes -= len(nodes)
        if not nodes:
            continue
        immediate_seconds = _SRUN_SMOKE_IMMEDIATE_SECONDS
        timeout_seconds = 420
        if _partition_has_powered_down_nodes(runner, spec, partition=partition_name):
            immediate_seconds = _SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS
            timeout_seconds = immediate_seconds + 300
        partition_batches = _batches(nodes, size=batch_size)

        def _driver_batch_succeeded(batch_result: _SrunBatchResult) -> bool:
            result = batch_result.result
            reported_hosts = set(_gpu_driver_jail_hosts(result.stdout))
            return (
                result.returncode == 0
                and _GPU_DRIVER_JAIL_MARKER in result.stdout
                and set(batch_result.nodes).issubset(reported_hosts)
            )

        batch_results = _run_srun_batches(
            runner=runner,
            spec=spec,
            partition=partition_name,
            batches=partition_batches,
            script_factory=lambda batch_nodes, _partition=partition_name, _immediate_seconds=immediate_seconds: (
                _gpu_driver_jail_script(
                    partition=_partition,
                    nodes=len(batch_nodes),
                    nodelist=batch_nodes,
                    immediate_seconds=_immediate_seconds,
                )
            ),
            timeout_seconds=timeout_seconds,
            stop_on_failure=not continue_on_failure,
            batch_succeeded=_driver_batch_succeeded,
        )
        node_reports: list[dict[str, Any]] = []
        partition_failed = False
        for batch_result in batch_results:
            result = batch_result.result
            if result.stdout:
                outputs.append(result.stdout)
            if result.stderr:
                errors.append(result.stderr)
            reported_hosts = set(_gpu_driver_jail_hosts(result.stdout))
            batch_passed = result.returncode == 0 and _GPU_DRIVER_JAIL_MARKER in result.stdout
            if not batch_passed:
                for line in "\n".join((result.stdout, result.stderr)).splitlines():
                    text = line.strip()
                    if text.startswith("cxcli-soperator-gpu-driver-jail-") and not text.startswith(
                        _GPU_DRIVER_JAIL_MARKER
                    ):
                        failure_details.append(_compact_output_line(text, limit=260))
            for node in batch_result.nodes:
                passed = batch_passed and node in reported_hosts
                if not passed:
                    partition_failed = True
                node_reports.append(
                    {
                        "node_name": node,
                        "status": "passed" if passed else "failed",
                        "reported": node in reported_hosts,
                    }
                )
            if partition_failed and not continue_on_failure:
                stopped = True
                break
        if stopped and len(node_reports) < len(nodes):
            tested = {str(item.get("node_name", "")) for item in node_reports}
            for node in nodes:
                if node not in tested:
                    node_reports.append(
                        {
                            "node_name": node,
                            "status": "not_run",
                            "reported": False,
                        }
                    )
        passed_count = sum(1 for item in node_reports if item["status"] == "passed")
        failed_count = sum(1 for item in node_reports if item["status"] == "failed")
        not_run_count = sum(1 for item in node_reports if item["status"] == "not_run")
        if partition_failed or not_run_count:
            failed_partitions.append(partition_name)
        partition_reports.append(
            {
                "partition": partition_name,
                "expected_node_count": len(nodes),
                "tested_node_count": len(node_reports) - not_run_count,
                "passed_node_count": passed_count,
                "failed_node_count": failed_count,
                "not_run_node_count": not_run_count,
                "batch_size": batch_size,
                "batch_count": len(partition_batches),
                "status": "failed" if partition_failed or not_run_count else "passed",
                "nodes": sorted(node_reports, key=lambda item: str(item.get("node_name", ""))),
            }
        )
        if stopped:
            break

    total_nodes = sum(int(item["expected_node_count"]) for item in partition_reports)
    passed_nodes = sum(int(item["passed_node_count"]) for item in partition_reports)
    failed_nodes = sum(int(item["failed_node_count"]) for item in partition_reports)
    not_run_nodes = sum(int(item["not_run_node_count"]) for item in partition_reports)
    extra: dict[str, Any] = {"gpu_driver_jail": partition_reports}
    if failure_details:
        extra["failure_details"] = failure_details[:8]
    if not failed_partitions:
        _append_check(
            checks,
            name=check_name,
            status="passed",
            summary=(
                f"GPU driver jail checks passed on {passed_nodes}/{total_nodes} "
                f"Slurm GPU node(s); libcuda.so.1, libnvidia-ml.so.1, and nvidia-smi "
                "were visible from the Slurm job root."
            ),
            stdout="\n".join(outputs),
            extra=extra,
        )
        return True
    detail = ""
    if failure_details:
        detail = f" First failure: {failure_details[0]}."
    _append_check(
        checks,
        name=check_name,
        status="failed",
        summary=(
            f"GPU driver jail checks passed on {passed_nodes}/{total_nodes} Slurm GPU "
            f"node(s); failed={failed_nodes}, not_run={not_run_nodes}; affected "
            "partition(s): " + ", ".join(failed_partitions) + "." + detail
        ),
        stdout="\n".join(outputs),
        stderr="\n".join(errors),
        extra=extra,
    )
    return False


def _sinfo_gpu_partition_candidates(stdout: str) -> tuple[str, ...]:
    preferred: list[tuple[int, int, int, str]] = []
    order = 0
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
            gpu_count = _gpu_count_from_gres(gres)
            floor_rank = 0 if gpu_count >= _NCCL_PREFERRED_GPUS_PER_NODE else 1
            preferred.append((floor_rank, -gpu_count, order, partition))
            order += 1
    return tuple(dict.fromkeys(item[3] for item in sorted(preferred)))


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
    immediate_seconds = _SRUN_SMOKE_IMMEDIATE_SECONDS
    timeout_seconds = 360
    if _partition_has_powered_down_nodes(runner, spec, partition=partition):
        immediate_seconds = _SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS
        timeout_seconds = immediate_seconds + 300
    result = _exec_login(
        runner,
        spec,
        ("bash", "-lc", _smoke_script(partition=partition, immediate_seconds=immediate_seconds)),
        timeout_seconds=timeout_seconds,
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


def _check_slurm_partition_hostnames_acceptance(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-N", "-h", "-o", "%P|%N"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        _append_check(
            checks,
            name="Slurm all-node hostname acceptance",
            status="failed",
            summary="sinfo could not list Slurm partition nodes for acceptance hostname checks.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )
        return
    partitions = _partition_nodes(sinfo.stdout)
    if not partitions:
        return
    batch_size = _acceptance_batch_size(spec)
    continue_on_failure = _acceptance_continue_on_failure(spec)
    outputs: list[str] = []
    errors: list[str] = []
    partition_reports: list[dict[str, Any]] = []
    failed_partitions: list[str] = []
    stopped = False
    for partition, nodes in sorted(partitions.items()):
        immediate_seconds = _SRUN_SMOKE_IMMEDIATE_SECONDS
        timeout_seconds = 420
        if _partition_has_powered_down_nodes(runner, spec, partition=partition):
            immediate_seconds = _SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS
            timeout_seconds = immediate_seconds + 300
        partition_batches = _batches(nodes, size=batch_size)

        def _hostname_batch_succeeded(batch_result: _SrunBatchResult) -> bool:
            result = batch_result.result
            reported = set(_partition_hostname_lines(result.stdout))
            return (
                result.returncode == 0
                and _PARTITION_HOSTNAME_MARKER in result.stdout
                and set(batch_result.nodes).issubset(reported)
            )

        batch_results = _run_srun_batches(
            runner=runner,
            spec=spec,
            partition=partition,
            batches=partition_batches,
            script_factory=lambda batch_nodes, _partition=partition, _immediate_seconds=immediate_seconds: (
                _partition_hostname_script(
                    partition=_partition,
                    nodes=len(batch_nodes),
                    nodelist=batch_nodes,
                    immediate_seconds=_immediate_seconds,
                )
            ),
            timeout_seconds=timeout_seconds,
            stop_on_failure=not continue_on_failure,
            batch_succeeded=_hostname_batch_succeeded,
        )
        node_reports: list[dict[str, Any]] = []
        partition_failed = False
        for batch_result in batch_results:
            result = batch_result.result
            if result.stdout:
                outputs.append(result.stdout)
            if result.stderr:
                errors.append(result.stderr)
            batch_passed = result.returncode == 0 and _PARTITION_HOSTNAME_MARKER in result.stdout
            reported = set(_partition_hostname_lines(result.stdout))
            for node in batch_result.nodes:
                passed = batch_passed and node in reported
                if not passed:
                    partition_failed = True
                node_reports.append(
                    {
                        "node_name": node,
                        "status": "passed" if passed else "failed",
                        "reported": node in reported,
                    }
                )
            if partition_failed and not continue_on_failure:
                stopped = True
                break
        if stopped and len(node_reports) < len(nodes):
            tested = {str(item.get("node_name", "")) for item in node_reports}
            for node in nodes:
                if node not in tested:
                    node_reports.append(
                        {
                            "node_name": node,
                            "status": "not_run",
                            "reported": False,
                        }
                    )
        passed_count = sum(1 for item in node_reports if item["status"] == "passed")
        failed_count = sum(1 for item in node_reports if item["status"] == "failed")
        not_run_count = sum(1 for item in node_reports if item["status"] == "not_run")
        if partition_failed or not_run_count:
            failed_partitions.append(partition)
        partition_reports.append(
            {
                "partition": partition,
                "expected_node_count": len(nodes),
                "tested_node_count": len(node_reports) - not_run_count,
                "passed_node_count": passed_count,
                "failed_node_count": failed_count,
                "not_run_node_count": not_run_count,
                "batch_size": batch_size,
                "batch_count": len(partition_batches),
                "status": "failed" if partition_failed or not_run_count else "passed",
                "nodes": sorted(node_reports, key=lambda item: str(item.get("node_name", ""))),
            }
        )
        if stopped:
            break
    total_nodes = sum(int(item["expected_node_count"]) for item in partition_reports)
    passed_nodes = sum(int(item["passed_node_count"]) for item in partition_reports)
    failed_nodes = sum(int(item["failed_node_count"]) for item in partition_reports)
    not_run_nodes = sum(int(item["not_run_node_count"]) for item in partition_reports)
    extra = {"partition_hostnames": partition_reports}
    if not failed_partitions:
        _append_check(
            checks,
            name="Slurm all-node hostname acceptance",
            status="passed",
            summary=(
                f"hostname acceptance jobs passed on {passed_nodes}/{total_nodes} Slurm "
                f"node(s) across {len(partition_reports)} partition(s)."
            ),
            stdout="\n".join(outputs),
            extra=extra,
        )
    else:
        _append_check(
            checks,
            name="Slurm all-node hostname acceptance",
            status="failed",
            summary=(
                f"hostname acceptance jobs passed on {passed_nodes}/{total_nodes} Slurm "
                f"node(s); failed={failed_nodes}, not_run={not_run_nodes}; affected "
                "partition(s): " + ", ".join(failed_partitions) + "."
            ),
            stdout="\n".join(outputs),
            stderr="\n".join(errors),
            extra=extra,
        )


def _check_slurm_partition_hostnames(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    if _validation_mode(spec) == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        _check_slurm_partition_hostnames_acceptance(runner, spec, checks)
        return
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-h", "-o", "%P|%D"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        _append_check(
            checks,
            name="Slurm all-partition hostname check",
            status="failed",
            summary="sinfo could not list Slurm partitions for all-node hostname checks.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )
        return
    partitions = _partition_node_counts(sinfo.stdout)
    if not partitions:
        return
    outputs: list[str] = []
    errors: list[str] = []
    failed: list[str] = []
    summary_parts: list[str] = []
    partition_hostnames: list[dict[str, Any]] = []
    for partition, node_count in partitions:
        sampled_node_count = _slurm_smoke_sample_nodes(node_count)
        passed = False
        result: SoperatorValidationCommandResult | None = None
        for attempt in range(1, _SLURM_PARTITION_HOSTNAME_RETRY_ATTEMPTS + 1):
            immediate_seconds = _SRUN_SMOKE_IMMEDIATE_SECONDS
            timeout_seconds = 420
            if _partition_has_powered_down_nodes(runner, spec, partition=partition):
                immediate_seconds = _SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS
                timeout_seconds = immediate_seconds + 300
            result = _exec_login(
                runner,
                spec,
                (
                    "bash",
                    "-lc",
                    _partition_hostname_script(
                        partition=partition,
                        nodes=sampled_node_count,
                        immediate_seconds=immediate_seconds,
                    ),
                ),
                timeout_seconds=timeout_seconds,
            )
            if result.stdout:
                outputs.append(result.stdout)
            if result.stderr:
                errors.append(result.stderr)
            if result.returncode == 0 and _PARTITION_HOSTNAME_MARKER in result.stdout:
                passed = True
                break
            if (
                attempt < _SLURM_PARTITION_HOSTNAME_RETRY_ATTEMPTS
                and _slurm_partition_hostname_transient_failure(result)
            ):
                time.sleep(_SLURM_PARTITION_HOSTNAME_RETRY_DELAY_SECONDS)
                continue
            break
        summary_parts.append(f"{partition}={sampled_node_count}/{node_count}")
        hostnames = _partition_hostname_lines(result.stdout if result is not None else "")
        partition_hostnames.append(
            {
                "partition": partition,
                "expected_node_count": node_count,
                "sampled_node_count": sampled_node_count,
                "reported_hostname_count": len(hostnames),
                "status": "passed" if passed else "failed",
                "hostnames": hostnames,
            }
        )
        if not passed:
            failed.append(partition)
    extra = {"partition_hostnames": partition_hostnames}
    if not failed:
        _append_check(
            checks,
            name="Slurm all-partition hostname check",
            status="passed",
            summary=(
                "hostname smoke jobs completed on sampled Slurm partition nodes: "
                + ", ".join(summary_parts)
                + "."
            ),
            stdout="\n".join(outputs),
            extra=extra,
        )
    else:
        _append_check(
            checks,
            name="Slurm all-partition hostname check",
            status="failed",
            summary=(
                "hostname smoke job failed or did not report the expected marker on partition(s): "
                + ", ".join(failed)
                + "."
            ),
            stdout="\n".join(outputs),
            stderr="\n".join(errors),
            extra=extra,
        )


def _check_slurm_gpu_allocation_acceptance(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    sinfo = _exec_login(
        runner,
        spec,
        ("sinfo", "-N", "-h", "-o", "%P|%N|%G"),
        timeout_seconds=120,
    )
    if sinfo.returncode != 0:
        _append_check(
            checks,
            name="Slurm all-node GPU allocation acceptance",
            status="failed",
            summary="sinfo could not list Slurm GPU partition nodes for acceptance allocation checks.",
            command=sinfo.args,
            stdout=sinfo.stdout,
            stderr=sinfo.stderr,
        )
        return
    partitions = _gpu_partition_nodes(sinfo.stdout)
    if not partitions:
        return
    batch_size = _acceptance_batch_size(spec)
    continue_on_failure = _acceptance_continue_on_failure(spec)
    outputs: list[str] = []
    errors: list[str] = []
    allocation_reports: list[dict[str, Any]] = []
    failed_partitions: list[str] = []
    stopped = False
    for partition, nodes in sorted(partitions.items()):
        immediate_seconds = _SRUN_SMOKE_IMMEDIATE_SECONDS
        timeout_seconds = 420
        if _partition_has_powered_down_nodes(runner, spec, partition=partition):
            immediate_seconds = _SRUN_SMOKE_CLOUD_IMMEDIATE_SECONDS
            timeout_seconds = immediate_seconds + 300
        partition_batches = _batches(nodes, size=batch_size)

        def _gpu_batch_succeeded(batch_result: _SrunBatchResult) -> bool:
            result = batch_result.result
            reported_hosts = set(_gpu_allocation_hosts(result.stdout))
            return (
                result.returncode == 0
                and _GPU_ALLOCATION_MARKER in result.stdout
                and set(batch_result.nodes).issubset(reported_hosts)
            )

        batch_results = _run_srun_batches(
            runner=runner,
            spec=spec,
            partition=partition,
            batches=partition_batches,
            script_factory=lambda batch_nodes, _partition=partition, _immediate_seconds=immediate_seconds: (
                _gpu_allocation_script(
                    partition=_partition,
                    nodes=len(batch_nodes),
                    nodelist=batch_nodes,
                    immediate_seconds=_immediate_seconds,
                )
            ),
            timeout_seconds=timeout_seconds,
            stop_on_failure=not continue_on_failure,
            batch_succeeded=_gpu_batch_succeeded,
        )
        node_reports: list[dict[str, Any]] = []
        partition_failed = False
        for batch_result in batch_results:
            result = batch_result.result
            if result.stdout:
                outputs.append(result.stdout)
            if result.stderr:
                errors.append(result.stderr)
            batch_passed = result.returncode == 0 and _GPU_ALLOCATION_MARKER in result.stdout
            evidence_by_host = _gpu_allocation_evidence(result.stdout)
            reported_hosts = set(evidence_by_host)
            for node in batch_result.nodes:
                passed = batch_passed and node in reported_hosts
                if not passed:
                    partition_failed = True
                node_report = {
                    "node_name": node,
                    "status": "passed" if passed else "failed",
                    "reported": node in reported_hosts,
                }
                if node in evidence_by_host:
                    node_report["evidence"] = evidence_by_host[node]
                node_reports.append(node_report)
            if partition_failed and not continue_on_failure:
                stopped = True
                break
        if stopped and len(node_reports) < len(nodes):
            tested = {str(item.get("node_name", "")) for item in node_reports}
            for node in nodes:
                if node not in tested:
                    node_reports.append(
                        {
                            "node_name": node,
                            "status": "not_run",
                            "reported": False,
                        }
                    )
        passed_count = sum(1 for item in node_reports if item["status"] == "passed")
        failed_count = sum(1 for item in node_reports if item["status"] == "failed")
        not_run_count = sum(1 for item in node_reports if item["status"] == "not_run")
        if partition_failed or not_run_count:
            failed_partitions.append(partition)
        allocation_reports.append(
            {
                "partition": partition,
                "expected_node_count": len(nodes),
                "tested_node_count": len(node_reports) - not_run_count,
                "passed_node_count": passed_count,
                "failed_node_count": failed_count,
                "not_run_node_count": not_run_count,
                "batch_size": batch_size,
                "batch_count": len(partition_batches),
                "status": "failed" if partition_failed or not_run_count else "passed",
                "nodes": sorted(node_reports, key=lambda item: str(item.get("node_name", ""))),
            }
        )
        if stopped:
            break
    total_nodes = sum(int(item["expected_node_count"]) for item in allocation_reports)
    passed_nodes = sum(int(item["passed_node_count"]) for item in allocation_reports)
    failed_nodes = sum(int(item["failed_node_count"]) for item in allocation_reports)
    not_run_nodes = sum(int(item["not_run_node_count"]) for item in allocation_reports)
    extra = {"gpu_allocations": allocation_reports}
    if not failed_partitions:
        _append_check(
            checks,
            name="Slurm all-node GPU allocation acceptance",
            status="passed",
            summary=(
                f"one-GPU Slurm acceptance allocations passed on {passed_nodes}/{total_nodes} "
                f"GPU node(s) across {len(allocation_reports)} partition(s)."
            ),
            stdout="\n".join(outputs),
            extra=extra,
        )
    else:
        _append_check(
            checks,
            name="Slurm all-node GPU allocation acceptance",
            status="failed",
            summary=(
                f"one-GPU Slurm acceptance allocations passed on {passed_nodes}/{total_nodes} "
                f"GPU node(s); failed={failed_nodes}, not_run={not_run_nodes}; affected "
                "partition(s): " + ", ".join(failed_partitions) + "."
            ),
            stdout="\n".join(outputs),
            stderr="\n".join(errors),
            extra=extra,
        )


def _check_slurm_gpu_allocation(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    if _validation_mode(spec) == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
        _check_slurm_gpu_allocation_acceptance(runner, spec, checks)


def _check_slurm_nccl_benchmark(
    runner: SoperatorValidationCommandRunner,
    spec: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    max_nodes = _positive_int_setting(
        spec.get("max_nodes"),
        field_label="acceptance-test benchmark --max-nodes",
    )
    timeout_seconds = _optional_duration_seconds(
        spec.get("timeout"),
        field_label="acceptance-test benchmark --timeout",
    )
    threshold_gbps = _non_negative_float_setting(
        spec.get("average_bus_bandwidth_threshold_gbps"),
        field_label="acceptance-test benchmark --average-bus-bandwidth-threshold-gbps",
    )
    partition = _select_gpu_smoke_partition(runner, spec)
    if not partition:
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status="failed",
            summary=(
                "No eligible Slurm GPU partition was available for the explicit NCCL benchmark."
            ),
            extra={
                "selected_partition": "",
                "failure_reason": "no_eligible_gpu_partition",
            },
        )
        return
    if not _check_slurm_gpu_driver_jail(
        runner,
        spec,
        checks,
        partition=partition,
        max_nodes=max_nodes,
        check_name="Slurm GPU driver jail benchmark preflight",
    ):
        return
    result = _exec_login(
        runner,
        spec,
        (
            "bash",
            "-lc",
            _nccl_script(
                partition=partition,
                max_nodes=max_nodes,
                timeout_seconds=timeout_seconds,
            ),
        ),
        timeout_seconds=timeout_seconds,
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
    metadata = _nccl_run_metadata(output)
    expected_message_bytes = _nccl_expected_large_message_bytes(metadata)
    avg_bus_bandwidth, large_message_bus_bandwidth = _nccl_large_message_bus_bandwidth_gbps(
        output,
        expected_bytes=expected_message_bytes,
    )
    if result.returncode == 0 and _NCCL_MARKER in output:
        nodes = metadata.get("nodes", 2)
        ranks = metadata.get("ranks", 0)
        gpus_per_node = metadata.get("gpus_per_node", 0)
        benchmark_mode = str(metadata.get("mode") or "")
        if ranks == 1:
            _append_check(
                checks,
                name="Slurm NCCL benchmark",
                status="passed",
                summary=(
                    f"single-rank Slurm NCCL all_reduce_perf smoke completed on partition "
                    f"{partition} with 1 GPU on 1 node; no collective bandwidth metric is "
                    "reported for a one-rank run."
                ),
                command=result.args,
                stdout=result.stdout,
                stderr=result.stderr,
                extra={
                    "avg_large_message_bus_bandwidth_gbps": None,
                    "large_message_bus_bandwidth_gbps": {},
                    "average_bus_bandwidth_threshold_gbps": threshold_gbps,
                    "bandwidth_threshold_passed": None,
                    "max_nodes": max_nodes,
                    "all_nodes": max_nodes is None,
                    "timeout": spec.get("timeout") or None,
                    "multi_node_benchmark": False,
                    "single_node_multi_gpu_benchmark": False,
                    "single_rank_smoke": True,
                    "benchmark_mode": benchmark_mode,
                    **metadata,
                },
            )
            return
        if avg_bus_bandwidth is None:
            _append_check(
                checks,
                name="Slurm NCCL benchmark",
                status="failed",
                summary=(
                    "NCCL all_reduce_perf benchmark completed but did not report "
                    f"large-message bandwidth output on partition {partition}."
                ),
                command=result.args,
                stdout=result.stdout,
                stderr=result.stderr,
                extra={
                    "max_nodes": max_nodes,
                    "all_nodes": max_nodes is None,
                    "timeout": spec.get("timeout") or None,
                    "benchmark_mode": benchmark_mode,
                    **metadata,
                },
            )
            return
        bandwidth_sizes = ", ".join(large_message_bus_bandwidth) or "large"
        bandwidth_size_label = (
            "message size" if len(large_message_bus_bandwidth) == 1 else "message sizes"
        )
        threshold_passed = threshold_gbps is None or avg_bus_bandwidth + 1e-9 >= threshold_gbps
        reported_gpus_per_node = metadata.get("reported_gpus_per_node")
        if isinstance(reported_gpus_per_node, int) and reported_gpus_per_node > 0:
            platform_gpus_per_node = reported_gpus_per_node
        else:
            platform_gpus_per_node = gpus_per_node
        one_gpu_platform = platform_gpus_per_node == 1
        threshold_comment = ""
        if threshold_gbps is not None and one_gpu_platform and not threshold_passed:
            threshold_comment = (
                "Observed average bus bandwidth is below the configured threshold; "
                "for a 1-GPU Slurm NCCL run this is recorded as informational because "
                "the NCCL workload completed and reported average bandwidth."
            )
        if nodes > 1:
            benchmark_summary = (
                f"multi-node NCCL all_reduce_perf benchmark completed on partition {partition} "
                f"with {ranks} rank(s) across {nodes} node(s)"
                f"{f' ({gpus_per_node} GPU(s) per node)' if gpus_per_node else ''}; "
            )
        elif gpus_per_node > 1:
            benchmark_summary = (
                f"single-node multi-GPU NCCL all_reduce_perf benchmark completed on "
                f"partition {partition} with {ranks} rank(s) on 1 node"
                f"{f' ({gpus_per_node} GPU(s) on the node)' if gpus_per_node else ''}; "
            )
        else:
            benchmark_summary = (
                f"single-rank Slurm NCCL all_reduce_perf smoke completed on partition "
                f"{partition} with 1 GPU on 1 node; "
            )
        status = "passed" if threshold_passed or threshold_comment else "failed"
        threshold_summary = ""
        if threshold_gbps is not None:
            threshold_summary = f" Required threshold: {threshold_gbps:.1f} Gbps."
            if not threshold_passed:
                threshold_summary += (
                    f" {threshold_comment}"
                    if threshold_comment
                    else " Observed bandwidth is below the required threshold."
                )
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status=status,
            summary=(
                benchmark_summary + f"average bus bandwidth {avg_bus_bandwidth:.1f} Gbps across "
                f"{bandwidth_sizes} {bandwidth_size_label}.{threshold_summary}"
            ),
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
            extra={
                "avg_large_message_bus_bandwidth_gbps": avg_bus_bandwidth,
                "large_message_bus_bandwidth_gbps": large_message_bus_bandwidth,
                "average_bus_bandwidth_threshold_gbps": threshold_gbps,
                "bandwidth_threshold_passed": threshold_passed,
                "bandwidth_threshold_comment": threshold_comment,
                "one_gpu_platform": one_gpu_platform,
                "max_nodes": max_nodes,
                "all_nodes": max_nodes is None,
                "timeout": spec.get("timeout") or None,
                "multi_node_benchmark": nodes > 1,
                "single_node_multi_gpu_benchmark": nodes == 1 and gpus_per_node > 1,
                "single_rank_smoke": ranks == 1,
                "benchmark_mode": benchmark_mode,
                **metadata,
            },
        )
    else:
        summary, failure_reason = _nccl_failure_summary(output, partition=partition)
        _append_check(
            checks,
            name="Slurm NCCL benchmark",
            status="failed",
            summary=summary,
            command=result.args,
            stdout=result.stdout,
            stderr=result.stderr,
            extra={
                "failure_reason": failure_reason,
                "max_nodes": max_nodes,
                "all_nodes": max_nodes is None,
                "timeout": spec.get("timeout") or None,
                "benchmark_mode": metadata.get("mode") or "",
                **metadata,
            },
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
        mode = _validation_mode(spec)
        try:
            report_file = _resolve_validation_report_file(spec, mode=mode)
        except ValueError as exc:
            target_ref = str(spec.get("target_ref", "") or "")
            report_file = _validation_report_file(target_ref, mode=mode)
            report = {
                "schema": SOPERATOR_CLUSTER_VALIDATION_SCHEMA,
                "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
                "mode": mode,
                "test_purpose": _validation_test_purpose(mode),
                "scope": _validation_scope(mode),
                "validation": "Soperator validation report-file contract",
                "target_ref": target_ref,
                "name": str(spec.get("name", "") or "Soperator cluster smoke test"),
                "status": "failed",
                "passed": False,
                "summary": str(exc),
                "checks": [
                    {
                        "name": "Soperator report file contract",
                        "status": "failed",
                        "passed": False,
                        "summary": str(exc),
                    }
                ],
            }
            report_path = reports_dir / report_file
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            written.append(report_path)
            continue
        if emit:
            emit(f"Starting validation {index}/{len(validations)}: {spec.get('name')}.")
        runner = command_runner or (
            lambda args, *, input_text=None, timeout_seconds=300, check=True: (
                _default_command_runner(
                    args,
                    input_text=input_text,
                    timeout_seconds=timeout_seconds,
                    check=check,
                    extra_env=extra_env,
                )
            )
        )
        checks: list[dict[str, Any]] = []
        try:
            deploy_mode = mode == SOPERATOR_DEPLOY_SMOKE_MODE
            _check_sconfigcontroller_deployment(runner, spec, checks)
            if bool(spec.get("check_old_source_flux", False)):
                _check_old_source_flux_desired_state(runner, spec, checks)
            if not deploy_mode:
                _wait_for_slurmcluster_available(runner, spec)
            _wait_for_soperator_storage_and_pods(runner, spec)
            if deploy_mode:
                _recover_stuck_populate_jail_job(runner, spec, checks)
            _check_soperator_storage_readiness(
                runner,
                spec,
                checks,
                fail_on_unready=not deploy_mode,
            )
            _check_soperator_pod_scheduling(
                runner,
                spec,
                checks,
                fail_on_pending=not deploy_mode,
            )
            if deploy_mode:
                _check_soperator_pod_health_snapshot(runner, spec, checks)
            _check_slurmcluster(runner, spec, checks, require_available=not deploy_mode)
            _check_nodeset_visibility(runner, spec, checks)
            _check_gpu_driver_jail_nodeset_contract(runner, spec, checks)
            if not deploy_mode:
                _check_slurm_status(runner, spec, checks)
                _check_slurm_queue(runner, spec, checks)
            if mode != SOPERATOR_ACCEPTANCE_BENCHMARK_MODE:
                _write_slurm_inventory_report(runner, spec, reports_dir=reports_dir)
            if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE:
                _check_srun_smoke(runner, spec, checks)
                _check_slurm_partition_hostnames(runner, spec, checks)
                _check_slurm_gpu_driver_jail(
                    runner,
                    spec,
                    checks,
                    check_name="Slurm GPU driver jail acceptance",
                )
                _check_slurm_gpu_allocation(runner, spec, checks)
            if bool(spec.get("include_nccl_benchmark", False)):
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
            "mode": mode,
            "test_purpose": _validation_test_purpose(mode),
            "scope": _validation_scope(mode),
            "validation": (
                "Soperator acceptance smoke test"
                if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE
                else "Soperator NCCL benchmark"
                if mode == SOPERATOR_ACCEPTANCE_BENCHMARK_MODE
                else "Soperator deployment test"
            ),
            "target_ref": str(spec.get("target_ref", "") or ""),
            "name": str(spec.get("name", "") or "Soperator cluster smoke test"),
            "batch_size": spec.get("batch_size")
            if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE
            else None,
            "concurrency": spec.get("concurrency")
            if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE
            else None,
            "continue_on_failure": spec.get("continue_on_failure")
            if mode == SOPERATOR_ACCEPTANCE_SMOKE_MODE
            else None,
            "status": "failed" if failed else "passed",
            "passed": not failed,
            "summary": summary,
            "checks": checks,
        }
        if mode != SOPERATOR_ACCEPTANCE_SMOKE_MODE:
            report.pop("batch_size", None)
            report.pop("concurrency", None)
            report.pop("continue_on_failure", None)
        report_path = reports_dir / report_file
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(report_path)
        if failed:
            names = ", ".join(str(item.get("name") or "check") for item in failed)
            raise RuntimeError(f"Soperator cluster validation failed: {names}.")
    return written


__all__ = [
    "SOPERATOR_CLUSTER_VALIDATION_KIND",
    "SOPERATOR_CLUSTER_VALIDATION_SCHEMA",
    "SoperatorValidationCommandResult",
    "normalize_soperator_project_deployment_testing_settings",
    "run_soperator_cluster_validations",
    "soperator_acceptance_benchmark_specs",
    "soperator_acceptance_smoke_specs",
    "soperator_cluster_validation_specs",
]
