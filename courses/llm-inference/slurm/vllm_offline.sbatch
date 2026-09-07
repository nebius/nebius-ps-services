#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=00:30:00

set -euo pipefail
umask 077

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: VLLM_IMAGE_DIGEST=... COURSE_CONTAINER_RUNNER=... sbatch %s [lab options]\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} ]]; then printf 'ERROR: submit with sbatch.\n' >&2; exit 2; fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: immutable image and executable runner required.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit from the llm-inference course root.}
if [[ ! -f ${course_dir}/labs/10_vllm_offline.py ]]; then
  printf 'ERROR: submit from the course root containing labs/10_vllm_offline.py.\n' >&2
  exit 2
fi
cd "${course_dir}"
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}" \
  python3 labs/10_vllm_offline.py "$@"
