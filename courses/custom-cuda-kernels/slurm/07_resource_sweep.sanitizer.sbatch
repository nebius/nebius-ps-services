#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --output=results/07_resource_sweep/logs/%j.out
#SBATCH --error=results/07_resource_sweep/logs/%j.err

set -euo pipefail

# Workload size is independent of the profiler selected by this job file.
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  printf 'Usage: sbatch %s [--workload small|large] [lab options]\n' "$0"
  exit 0
fi
workload=${COURSE_WORKLOAD:-large}
lab_args=()
while (($#)); do
  case "$1" in
    --workload) [[ $# -ge 2 ]] || { printf 'ERROR: --workload needs small or large.\n' >&2; exit 2; }; workload=$2; shift 2 ;;
    --workload=*) workload=${1#*=}; shift ;;
    --profile|--profile=*) printf 'ERROR: use --workload small|large.\n' >&2; exit 2 ;;
    *) lab_args+=("$1"); shift ;;
  esac
done
[[ $workload == small || $workload == large ]] || { printf 'ERROR: --workload must be small or large.\n' >&2; exit 2; }
set -- "${lab_args[@]}"
export COURSE_WORKLOAD=$workload
[[ ${SLURM_JOB_ID:-} =~ ^[1-9][0-9]*$ && ${SLURM_RESTART_COUNT:-0} == 0 ]] || {
  printf 'ERROR: submit a fresh job with sbatch; requeued jobs are unsupported.\n' >&2; exit 2;
}
umask 077
for directory in results results/07_resource_sweep results/07_resource_sweep/logs results/07_resource_sweep/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run course_setup.py prepare; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/07_resource_sweep/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=none COURSE_CAPTURE=0 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"

set -euo pipefail
umask 077
if [[ -z "${SLURM_JOB_ID:-}" || $# -lt 1 ]]; then
  printf 'Usage: sbatch [Slurm --name=value options] %s TOOL EXECUTABLE [arguments]\n' "$0" >&2
  printf 'TOOL must be memcheck, racecheck, initcheck, or synccheck.\n' >&2
  exit 2
fi
: "${CUDA_IMAGE_DIGEST:?set the exact qualified CUDA development image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${CUDA_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then printf 'ERROR: immutable image and executable runner required.\n' >&2; exit 2; fi
tool="$1"
executable="${COURSE_BUILD_DIR:?select completed build}/07_resource_sweep"
shift
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
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  compute-sanitizer "${sanitizer_options[@]}" "${executable}" --workload "$workload" "$@"
