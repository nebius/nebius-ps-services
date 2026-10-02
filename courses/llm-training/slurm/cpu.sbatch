#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:10:00

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  printf 'Usage: sbatch [Slurm options] %s LAB.py [lab options]\n' "$0"
  printf 'Run a CPU-only course model with COURSE_PYTHON; no GPU is requested.\n'
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || $# -lt 1 || ! -f $1 ]]; then
  printf 'ERROR: submit with sbatch and provide an existing lab source.\n' >&2
  exit 2
fi
if [[ ${COURSE_PROFILE_TOOL:-none} != none || ${COURSE_CAPTURE:-0} != 0 ]]; then
  printf 'ERROR: CPU models do not support GPU capture.\n' >&2
  exit 2
fi
course_python=${COURSE_PYTHON:-python3}
command -v "$course_python" >/dev/null 2>&1 || {
  printf 'ERROR: COURSE_PYTHON is unavailable.\n' >&2
  exit 2
}
exec srun --ntasks=1 "$course_python" "$@"
