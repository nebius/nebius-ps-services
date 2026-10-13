#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:01:30

#SBATCH --no-requeue
#SBATCH --output=results/13_h100_preflight/logs/%j.out
#SBATCH --error=results/13_h100_preflight/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    sanitizer_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh custom-cuda-kernels.13_h100_preflight --runtime
      source tools/course_job_cache.sh
set -euo pipefail
umask 077
tool="${COURSE_SANITIZER:?select --sanitizer}"
executable="${COURSE_BUILD_DIR:?select completed build}/13_h100_preflight"
case ${tool} in
  memcheck | racecheck | initcheck | synccheck) ;;
  *) printf 'ERROR: unsupported sanitizer tool: %s\n' "${tool}" >&2; exit 2 ;;
esac
sanitizer_options=(--tool "${tool}" --error-exitcode=3)
if [[ ${tool} == racecheck ]]; then
  : "${SLURM_CPUS_PER_TASK:?Racecheck requires the Slurm CPU allocation}"
  if [[ ! ${SLURM_CPUS_PER_TASK} =~ ^[1-9][0-9]*$ ]]; then
    printf 'ERROR: SLURM_CPUS_PER_TASK must be a positive integer.\n' >&2
    exit 2
  fi
  sanitizer_options+=(--racecheck-num-workers "${SLURM_CPUS_PER_TASK}" --force-synchronization-limit 2)
fi
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 \
  compute-sanitizer "${sanitizer_options[@]}" "${executable}" --workload "$workload" "$@"
      ;;
    default_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh custom-cuda-kernels.13_h100_preflight --runtime
      source tools/course_job_cache.sh
: "${COURSE_BUILD_DIR:?select the completed build directory}"
executable="$COURSE_BUILD_DIR/13_h100_preflight"
[[ -x $executable && ! -L $executable ]] || exit 2
raw="$COURSE_JOB_DIR/logs/13_h100_preflight.txt"
started=$(date +%s)
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 "$executable" --workload "$workload" "$@" >"$raw"
ended=$(date +%s)
"${COURSE_PYTHON}" tools/cuda_result.py --input "$raw" --started "$started" --ended "$ended" "$executable" --workload "$workload" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 13_h100_preflight --launcher "$0" -- "$@"
fi
