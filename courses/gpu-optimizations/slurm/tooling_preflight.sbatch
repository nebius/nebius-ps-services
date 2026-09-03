#!/usr/bin/env bash
#SBATCH --job-name=gpu-tooling-preflight
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s\n' "$0"
  printf '\nReports tool availability from an allocated H100 compute node.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  show_usage >&2
  exit 2
fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  printf 'ERROR: submit this script with sbatch.\n' >&2
  exit 2
fi

course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}

report_command() {
  local command_name="$1"
  if command -v "${command_name}" >/dev/null 2>&1; then
    printf 'FOUND   %s\n' "${command_name}"
  else
    printf 'MISSING %s; ask the cluster owner to install or expose it\n' "${command_name}"
  fi
}

run_capability_check() {
  local label="$1"
  shift
  printf '\nCHECK   %s\n' "${label}"
  if "$@"; then
    printf 'PASS    %s\n' "${label}"
  else
    local status=$?
    printf 'BLOCKED %s (exit %s); preserve this output and ask the tool owner.\n' \
      "${label}" "${status}" >&2
  fi
}

printf 'Slurm allocation detected; environment-specific identifiers are suppressed.\n'
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
  printf 'MISSING nvidia-smi: the H100 allocation cannot be verified.\n' >&2
fi
"${course_python}" -c 'import torch; print(f"PyTorch {torch.__version__}; CUDA {torch.version.cuda}; profiler={hasattr(torch, \"profiler\")}; nvtx={hasattr(torch.cuda, \"nvtx\")}")'
for command_name in \
  nvidia-smi nsys ncu dcgmi dcgm-exporter nvbandwidth \
  all_reduce_perf all_gather_perf reduce_scatter_perf alltoall_perf \
  genai-perf aiperf; do
  report_command "${command_name}"
done

if command -v nsys >/dev/null 2>&1; then
  run_capability_check "Nsight Systems version" nsys --version
  run_capability_check "Nsight Systems environment" nsys status -e
fi
if command -v ncu >/dev/null 2>&1; then
  run_capability_check "Nsight Compute version" ncu --version
  run_capability_check "Nsight Compute section sets" ncu --list-sets
  run_capability_check "Nsight Compute sections" ncu --list-sections
fi
if command -v dcgmi >/dev/null 2>&1; then
  run_capability_check "DCGM version" dcgmi --version
  printf '\nCHECK   DCGM device discovery\n'
  if dcgmi discovery -l >/dev/null 2>&1; then
    printf 'PASS    DCGM device discovery (device identifiers suppressed)\n'
  else
    status=$?
    printf 'BLOCKED DCGM device discovery (exit %s); ask the tool owner.\n' \
      "${status}" >&2
  fi
  run_capability_check \
    "DCGM profiling fields for GPU 0" \
    dcgmi profile --list --entity-id gpu:0
fi
if command -v genai-perf >/dev/null 2>&1; then
  run_capability_check "GenAI-Perf version" genai-perf --version
fi
if command -v aiperf >/dev/null 2>&1; then
  run_capability_check "AIPerf version" aiperf --version
fi
