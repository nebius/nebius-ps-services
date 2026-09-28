#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --time=00:30:00
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then
  printf 'Usage: python3 tools/submit_lab.py --lab LAB [--nodes=1|2] slurm/fabric.sbatch labs/LAB.py [options]\n'
  exit 0
fi
: "${SLURM_JOB_ID:?submit through tools/submit_lab.py}"
: "${SLURM_JOB_NODELIST:?missing allocation}"
lab=${1:?provide lab source}; shift
[[ -f $lab ]] || { printf 'ERROR: lab source missing.\n' >&2; exit 2; }
mapfile -t nodes < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
[[ ${#nodes[@]} == 1 || ${#nodes[@]} == 2 ]] || { printf 'ERROR: allocate one or two eight-H100 workers.\n' >&2; exit 2; }
export COURSE_NODES=${#nodes[@]}
export COURSE_WORLD_SIZE=$((COURSE_NODES * 8))
export MASTER_ADDR=${nodes[0]}
export MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
export COURSE_TORCHRUN=${COURSE_TORCHRUN:-torchrun}
export COURSE_PYTHON=${COURSE_PYTHON:-python3}
if [[ -z ${COURSE_RUN_ID:-} ]]; then
  COURSE_RUN_ID=$("$COURSE_PYTHON" -c 'import secrets; print(secrets.token_hex(6))')
fi
[[ $COURSE_RUN_ID =~ ^[0-9a-f]{12}$ ]] || { printf 'ERROR: invalid shared run identity.\n' >&2; exit 2; }
export COURSE_RUN_ID
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}
srun --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "$COURSE_PYTHON" tools/fabric_guard.py
# One Slurm task launches eight torchrun workers on each node.
# shellcheck disable=SC2016
srun --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 --kill-on-bad-exit=1 \
  bash -c 'exec "$COURSE_TORCHRUN" --nnodes="$COURSE_NODES" --nproc-per-node=8 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python "$COURSE_PYTHON" tools/profile_lab.py --lab "$1" -- "$COURSE_PYTHON" "$@"' bash "$lab" "$@"
