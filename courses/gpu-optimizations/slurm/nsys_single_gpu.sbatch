#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-nsys
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s LAB.py [lab options]\n' "$0"
  printf '\nCaptures a focused Nsight Systems report and CLI statistics for one H100 lab.\n'
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
command -v nsys >/dev/null 2>&1 || {
  printf 'ERROR: nsys is not available in this job environment.\n' >&2
  exit 2
}
mkdir -p results
run_directory="$(mktemp -d results/nsys-run-XXXXXXXX)"
report="${run_directory}/profile"
srun --ntasks=1 --gpus-per-task=1 \
  nsys profile --force-overwrite=true --trace=cuda,nvtx,osrt \
  --sample=none --cpuctxsw=none --output="${report}" \
  "${course_python}" "${lab_path}" "$@"
nsys stats "${report}.nsys-rep" >"${report}-stats.txt"
