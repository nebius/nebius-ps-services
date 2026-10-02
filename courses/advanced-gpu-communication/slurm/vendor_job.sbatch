#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --exclusive
#SBATCH --time=01:00:00
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then
  printf 'Usage: sbatch slurm/vendor_job.sbatch labs/LAB.py [options]\n'
  exit 0
fi
: "${SLURM_JOB_ID:?submit through the course launcher}"
: "${COURSE_PYTHON:?complete Lab 00}"
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 "$COURSE_PYTHON" tools/fabric_guard.py
# The coordinator runs on the batch host; child steps stay inside this allocation.
exec "$COURSE_PYTHON" "$@"
