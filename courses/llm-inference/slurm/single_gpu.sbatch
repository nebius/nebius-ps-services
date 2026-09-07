#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s LAB.py [lab options]\n' "$0"
  printf '\nRuns one course lab on one full H100. Set COURSE_PYTHON to select Python.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ $# -lt 1 ]]; then show_usage >&2; exit 2; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
lab_path="$1"
shift
if [[ ! -f "${lab_path}" ]]; then printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2; exit 2; fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2; exit 2; }
srun --ntasks=1 --gpus-per-task=1 "${course_python}" "${lab_path}" "$@"
