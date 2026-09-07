#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00

set -euo pipefail
umask 077

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  printf 'Usage: sbatch [site options] %s EXECUTABLE [arguments]\n' "$0"
  exit 0
fi
if [[ -z "${SLURM_JOB_ID:-}" || $# -lt 1 ]]; then
  printf 'ERROR: submit with sbatch and provide a built executable.\n' >&2
  exit 2
fi
: "${CUDA_IMAGE_DIGEST:?set the exact qualified CUDA development image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${CUDA_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: the image must use @sha256 and the runner must be executable.\n' >&2
  exit 2
fi
executable="$1"
shift
if [[ ! -x "${executable}" ]]; then
  printf 'ERROR: executable not found or not executable: %s\n' "${executable}" >&2
  exit 2
fi
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" "${executable}" "$@"
