#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00

set -euo pipefail
umask 077
if [[ -z "${SLURM_JOB_ID:-}" || $# -lt 2 ]]; then
  printf 'Usage: sbatch [Slurm --name=value options] %s TOOL EXECUTABLE [arguments]\n' "$0" >&2
  printf 'TOOL must be memcheck, racecheck, initcheck, or synccheck.\n' >&2
  exit 2
fi
: "${CUDA_IMAGE_DIGEST:?set the exact qualified CUDA development image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${CUDA_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then printf 'ERROR: immutable image and executable runner required.\n' >&2; exit 2; fi
tool="$1"
executable="$2"
shift 2
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
  compute-sanitizer "${sanitizer_options[@]}" "${executable}" "$@"
