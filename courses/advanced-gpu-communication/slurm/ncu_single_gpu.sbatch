#!/usr/bin/env bash
#SBATCH --job-name=gradient-backward-ncu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:45:00
#SBATCH --output=%x-%j.out

set -euo pipefail
umask 077
export COURSE_CAPTURE=1
export COURSE_PROFILE_TOOL=ncu
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [companion options]\n' "$0"
  printf '\nDiagnose one local Lab 19 backward kernel on one H100.\n'
  printf 'Options: --profile small|large, --seed N, --bucket-mib N N [...], --warmup N.\n'
  printf 'COURSE_PYTHON selects Python; COURSE_PROFILE_KERNEL selects a kernel regex.\n'
  printf 'Produces diagnostic reports only; native capture qualification is separate.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  printf 'ERROR: submit this script with sbatch.\n' >&2
  exit 2
fi
if [[ -n "${NCU_SET+x}" || -n "${NCU_SECTIONS+x}" ]]; then
  printf 'ERROR: NCU_SET and NCU_SECTIONS are unsupported; the companion uses the shared basic sections.\n' >&2
  exit 2
fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
srun --nodes=1 --ntasks=1 --gpus-per-task=1 \
  "${course_python}" tools/profile_lab.py --lab 19_gradient_overlap --tool ncu -- \
  "${course_python}" labs/gradient_overlap_compute.py "$@"
