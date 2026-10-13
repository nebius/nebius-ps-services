#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=0
#SBATCH --cpus-per-task=1
#SBATCH --time=00:11:30

#SBATCH --no-requeue
#SBATCH --output=results/36_kv_tiering/logs/%j.out
#SBATCH --error=results/36_kv_tiering/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_none_cpu)
      # Actual scope: 1 node(s), 1 task(s), 0 GPU(s) per task.
      source tools/course_env.sh llm-inference.08_kv_cache.ncu --runtime
      source tools/course_job_cache.sh
srun --nodes=1 --cpus-per-task=1 --gres=none --ntasks=1 "${COURSE_PYTHON}" labs/36_kv_tiering.py --workload "$workload" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 36_kv_tiering --launcher "$0" -- "$@"
fi
