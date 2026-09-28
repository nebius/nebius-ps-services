#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=8
#SBATCH --time=00:20:00
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then printf 'Usage: python3 tools/submit_lab.py --lab LAB [--nodes=1|2] slurm/fabric_tools.sbatch labs/LAB.py [options]\n'; exit 0; fi
: "${SLURM_JOB_ID:?submit through tools/submit_lab.py}"
: "${SLURM_JOB_NUM_NODES:?missing allocation}"
[[ $SLURM_JOB_NUM_NODES == 1 || $SLURM_JOB_NUM_NODES == 2 ]] || exit 2
srun --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "${COURSE_PYTHON:-python3}" tools/fabric_guard.py
exec "${COURSE_PYTHON:-python3}" "$@"
