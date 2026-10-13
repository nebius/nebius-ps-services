#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=8
#SBATCH --time=00:21:30

#SBATCH --no-requeue
#SBATCH --output=results/07_rdma_bandwidth/logs/%j.out
#SBATCH --error=results/07_rdma_bandwidth/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_none_cuda)
      # Actual scope: 2 node(s), 2 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.07_rdma_bandwidth --runtime
      source tools/course_job_cache.sh
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then printf 'Usage: sbatch [--nodes=1|2] slurm/fabric_tools.sbatch labs/LAB.py [options]\n'; exit 0; fi
: "${SLURM_JOB_ID:?submit with sbatch}"
: "${SLURM_JOB_NUM_NODES:?missing allocation}"
[[ $SLURM_JOB_NUM_NODES == 1 || $SLURM_JOB_NUM_NODES == 2 ]] || exit 2
srun --cpus-per-task=8 --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "${COURSE_PYTHON}" tools/fabric_guard.py
"${COURSE_PYTHON}" labs/07_rdma_bandwidth.py "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 07_rdma_bandwidth --launcher "$0" -- "$@"
fi
