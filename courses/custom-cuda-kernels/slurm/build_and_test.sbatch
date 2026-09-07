#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00

set -euo pipefail
umask 077

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: CUDA_IMAGE_DIGEST=... CUTLASS_ROOT=... COURSE_CONTAINER_RUNNER=... sbatch %s\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} ]]; then printf 'ERROR: submit with sbatch.\n' >&2; exit 2; fi
: "${CUDA_IMAGE_DIGEST:?set the exact qualified CUDA development image digest}"
: "${CUTLASS_ROOT:?set the shared path to the reviewed CUTLASS 4.6.1 source tree}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${CUDA_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then printf 'ERROR: immutable image and executable runner required.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
course_dir=${SLURM_SUBMIT_DIR:?Submit from the custom-cuda-kernels course root.}
if [[ ! -f ${course_dir}/CMakeLists.txt ]]; then printf 'ERROR: CMakeLists.txt is unavailable.\n' >&2; exit 2; fi
if [[ ! -f ${CUTLASS_ROOT}/include/cutlass/version.h ]]; then printf 'ERROR: CUTLASS_ROOT does not contain include/cutlass/version.h.\n' >&2; exit 2; fi
cd "${course_dir}"
build_dir="build/run-${SLURM_JOB_ID}"
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  cmake -S . -B "${build_dir}" -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DCOURSE_ENABLE_CUTLASS=ON -DCUTLASS_ROOT="${CUTLASS_ROOT}"
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  cmake --build "${build_dir}" --parallel
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  ctest --test-dir "${build_dir}" --output-on-failure
