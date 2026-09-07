#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00

set -euo pipefail
umask 077
if [[ -z "${SLURM_JOB_ID:-}" || $# -lt 2 ]]; then
  printf 'Usage: sbatch [site options] %s TOOL EXECUTABLE [arguments]\n' "$0" >&2
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
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  compute-sanitizer --tool "${tool}" --error-exitcode=3 "${executable}" "$@"
