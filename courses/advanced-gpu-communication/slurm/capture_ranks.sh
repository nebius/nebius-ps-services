#!/usr/bin/env bash
# Run once per node with srun; the caller supplies the visible native profiler argv.
set -euo pipefail
umask 077
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: srun [two-node resources] bash slurm/capture_ranks.sh 1|8 env ... nsys profile ... PYTHON LAB.py [options]\n'
  exit 0
fi
ranks=${1:?provide one or eight ranks per node}; shift
[[ $ranks == 1 || $ranks == 8 ]] || exit 2
[[ ${SLURM_JOB_ID:-} =~ ^[0-9]+$ && ${SLURM_NODEID:-} =~ ^[01]$ && ${SLURM_NTASKS:-} == 2 ]] || {
  printf 'ERROR: use one Slurm task on each of two allocated nodes.\n' >&2; exit 2;
}
: "${COURSE_PYTHON:?source the qualified course runtime}"
: "${COURSE_TORCHRUN:?source the qualified course runtime}"
mapfile -t nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST:?missing allocation}")
[[ ${#nodes[@]} == 2 ]] || exit 2
export MASTER_ADDR=${nodes[0]} MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
export COURSE_NODES=2 COURSE_WORLD_SIZE=$((2 * ranks))
# All nodes derive the same identity; different scheduler steps remain distinct.
printf -v COURSE_RUN_ID '%08x%04x' "$SLURM_JOB_ID" "${SLURM_STEP_ID:-0}"
[[ $COURSE_RUN_ID =~ ^[0-9a-f]{12}$ ]] || exit 2
export COURSE_RUN_ID PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1
"$COURSE_PYTHON" tools/fabric_guard.py
exec "$COURSE_TORCHRUN" --nnodes=2 --nproc-per-node="$ranks" \
  --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" \
  --no-python "$@"
