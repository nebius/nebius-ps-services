#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=16
#SBATCH --time=00:46:30

#SBATCH --no-requeue
#SBATCH --output=results/23_inference_expert_parallel/logs/%j.out
#SBATCH --error=results/23_inference_expert_parallel/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_nsys_cuda)
      # Actual scope: 2 node(s), 2 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.regular --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [--workload small|large] [lab options]\n' "$0"
  printf '\nRuns torchrun with one rank on each of two eight-H100 fabric nodes (bounded mechanics).\n'
  printf 'Set COURSE_TORCHRUN to select the torchrun executable.\n'
}

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ ! -f labs/23_inference_expert_parallel.py ]]; then show_usage >&2; exit 2; fi
if [[ -z "${SLURM_JOB_ID:-}" || -z "${SLURM_JOB_NODELIST:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
lab_path=labs/23_inference_expert_parallel.py
if [[ ! -f "${lab_path}" ]]; then printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2; exit 2; fi
command -v scontrol >/dev/null 2>&1 || { printf 'ERROR: required command not found: scontrol\n' >&2; exit 2; }
mapfile -t allocated_nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST}")
if [[ "${#allocated_nodes[@]}" -lt 2 ]]; then printf 'ERROR: expected two allocated nodes, received %s.\n' "${#allocated_nodes[@]}" >&2; exit 2; fi
export MASTER_ADDR="${allocated_nodes[0]}"
if [[ -n "${MASTER_PORT:-}" ]]; then
  if [[ ! ${MASTER_PORT} =~ ^[0-9]+$ ]] || ((MASTER_PORT < 1 || MASTER_PORT > 65535)); then printf 'ERROR: MASTER_PORT must be an integer from 1 through 65535.\n' >&2; exit 2; fi
else
  MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
fi
export MASTER_PORT
export COURSE_TORCHRUN="${COURSE_TORCHRUN}"
command -v "${COURSE_TORCHRUN}" >/dev/null 2>&1 || { printf 'ERROR: torchrun command not found: %s\n' "${COURSE_TORCHRUN}" >&2; exit 2; }
srun --nodes=2 --cpus-per-task=16 --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 "${COURSE_PYTHON}" tools/fabric_guard.py
# shellcheck disable=SC2016
srun --nodes=2 --cpus-per-task=16 --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 \
  bash -c '"$COURSE_TORCHRUN" --nnodes=2 --nproc-per-node=1 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt,nccl --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none --discard-environment=true --force-overwrite=false --duration=300 --kill=none --wait=all --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "${COURSE_PYTHON}" "$@"' \
  bash "${lab_path}" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 2)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    default_none_cuda)
      # Actual scope: 2 node(s), 2 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.regular --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [--workload small|large] [lab options]\n' "$0"
  printf '\nRuns torchrun with one rank on each of two eight-H100 fabric nodes (bounded mechanics).\n'
  printf 'Set COURSE_TORCHRUN to select the torchrun executable.\n'
}

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ ! -f labs/23_inference_expert_parallel.py ]]; then show_usage >&2; exit 2; fi
if [[ -z "${SLURM_JOB_ID:-}" || -z "${SLURM_JOB_NODELIST:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
lab_path=labs/23_inference_expert_parallel.py
if [[ ! -f "${lab_path}" ]]; then printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2; exit 2; fi
command -v scontrol >/dev/null 2>&1 || { printf 'ERROR: required command not found: scontrol\n' >&2; exit 2; }
mapfile -t allocated_nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST}")
if [[ "${#allocated_nodes[@]}" -lt 2 ]]; then printf 'ERROR: expected two allocated nodes, received %s.\n' "${#allocated_nodes[@]}" >&2; exit 2; fi
export MASTER_ADDR="${allocated_nodes[0]}"
if [[ -n "${MASTER_PORT:-}" ]]; then
  if [[ ! ${MASTER_PORT} =~ ^[0-9]+$ ]] || ((MASTER_PORT < 1 || MASTER_PORT > 65535)); then printf 'ERROR: MASTER_PORT must be an integer from 1 through 65535.\n' >&2; exit 2; fi
else
  MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
fi
export MASTER_PORT
export COURSE_TORCHRUN="${COURSE_TORCHRUN}"
command -v "${COURSE_TORCHRUN}" >/dev/null 2>&1 || { printf 'ERROR: torchrun command not found: %s\n' "${COURSE_TORCHRUN}" >&2; exit 2; }
srun --nodes=2 --cpus-per-task=16 --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 "${COURSE_PYTHON}" tools/fabric_guard.py
# shellcheck disable=SC2016
srun --nodes=2 --cpus-per-task=16 --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 \
  bash -c 'exec "$COURSE_TORCHRUN" --nnodes=2 --nproc-per-node=1 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python "${COURSE_PYTHON}" "$@"' \
  bash "${lab_path}" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 23_inference_expert_parallel --launcher "$0" -- "$@"
fi
