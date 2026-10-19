#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --time=01:01:30
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --output=results/27_rdma_latency/logs/%j.out
#SBATCH --error=results/27_rdma_latency/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_none_cuda)
      # Actual scope: 2 node(s), 2 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.27_rdma_latency --runtime
      source tools/course_job_cache.sh
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then
  printf 'Usage: sbatch slurm/vendor_job.sbatch labs/LAB.py [options]\n'
  exit 0
fi
: "${SLURM_JOB_ID:?submit through the course launcher}"
: "${COURSE_PYTHON:?complete Lab 00}"
srun --cpus-per-task=32 --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 "$COURSE_PYTHON" tools/fabric_guard.py
# The coordinator runs on the batch host; child steps stay inside this allocation.
"$COURSE_PYTHON" labs/27_rdma_latency.py "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 27_rdma_latency --launcher "$0" -- "$@"
fi
