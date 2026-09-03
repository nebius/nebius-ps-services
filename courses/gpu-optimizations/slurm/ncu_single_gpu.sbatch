#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-ncu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:45:00
#SBATCH --output=%x-%j.out

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s LAB.py [lab options]\n' "$0"
  printf '\nCaptures one NVTX-selected kernel with an Nsight Compute section set.\n'
  printf 'Set NCU_SET to override the default roofline set after checking ncu --list-sets.\n'
  printf 'Set comma-separated NCU_SECTIONS after checking ncu --list-sections.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi
if [[ $# -lt 1 ]]; then
  show_usage >&2
  exit 2
fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  printf 'ERROR: submit this script with sbatch.\n' >&2
  exit 2
fi
lab_path="$1"
shift
if [[ ! -f "${lab_path}" ]]; then printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2; exit 2; fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
command -v ncu >/dev/null 2>&1 || {
  printf 'ERROR: ncu is not available in this job environment.\n' >&2
  exit 2
}
ncu_set="${NCU_SET:-roofline}"
if [[ ! "${ncu_set}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  printf 'ERROR: NCU_SET contains unsupported characters: %s\n' "${ncu_set}" >&2
  exit 2
fi
printf 'Collecting Nsight Compute set: %s\n' "${ncu_set}"
ncu_arguments=(
  --set="${ncu_set}"
  --target-processes=all
  --nvtx
  --nvtx-include 'profile_region/'
  --launch-count=1
  --force-overwrite
)
if [[ -n "${NCU_SECTIONS:-}" ]]; then
  IFS=',' read -r -a ncu_sections <<<"${NCU_SECTIONS}"
  for section_name in "${ncu_sections[@]}"; do
    if [[ ! "${section_name}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
      printf 'ERROR: invalid Nsight Compute section: %s\n' "${section_name}" >&2
      exit 2
    fi
    ncu_arguments+=(--section "${section_name}")
  done
  printf 'Additional Nsight Compute sections: %s\n' "${NCU_SECTIONS}"
fi
mkdir -p results
run_directory="$(mktemp -d results/ncu-run-XXXXXXXX)"
report="${run_directory}/profile"
srun --ntasks=1 --gpus-per-task=1 \
  ncu "${ncu_arguments[@]}" --export="${report}" \
  "${course_python}" "${lab_path}" "$@"
