#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=00:30:00

set -euo pipefail
umask 077

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  printf 'Usage: DYNAMO_IMAGE_DIGEST=... sbatch [site container and transport options] %s\n' "$0"
  exit 0
fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit with sbatch.\n' >&2; exit 2; fi
: "${DYNAMO_IMAGE_DIGEST:?set the exact qualified Dynamo image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ "${DYNAMO_IMAGE_DIGEST}" != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then printf 'ERROR: image must use @sha256 and the runner must be executable.\n' >&2; exit 2; fi
printf 'Advanced preflight only. Starting prefill/decode workers requires separately reviewed site transport, routing, and KV-transfer configuration.\n'
srun --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${DYNAMO_IMAGE_DIGEST}" nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
