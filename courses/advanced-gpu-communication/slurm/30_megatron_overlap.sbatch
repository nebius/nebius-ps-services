#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --time=00:30:00
#SBATCH --no-requeue
#SBATCH --output=results/30_megatron_overlap/logs/%j.out
#SBATCH --error=results/30_megatron_overlap/logs/%j.err

set -euo pipefail

# Workload size is independent of the profiler selected by this job file.
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  printf 'Usage: sbatch %s [--workload small|large] [lab options]\n' "$0"
  exit 0
fi
workload=${COURSE_WORKLOAD:-small}
lab_args=()
while (($#)); do
  case "$1" in
    --workload) [[ $# -ge 2 ]] || { printf 'ERROR: --workload needs small or large.\n' >&2; exit 2; }; workload=$2; shift 2 ;;
    --workload=*) workload=${1#*=}; shift ;;
    --profile|--profile=*) printf 'ERROR: use --workload small|large.\n' >&2; exit 2 ;;
    *) lab_args+=("$1"); shift ;;
  esac
done
[[ $workload == small || $workload == large ]] || { printf 'ERROR: --workload must be small or large.\n' >&2; exit 2; }
set -- "${lab_args[@]}"
export COURSE_WORKLOAD=$workload
[[ ${SLURM_JOB_ID:-} =~ ^[1-9][0-9]*$ && ${SLURM_RESTART_COUNT:-0} == 0 ]] || {
  printf 'ERROR: submit a fresh job with sbatch; requeued jobs are unsupported.\n' >&2; exit 2;
}
# Load this launcher's persistent installation inside the allocated job.
source tools/course_env.sh 30_megatron_overlap.sbatch
umask 077
for directory in results results/30_megatron_overlap results/30_megatron_overlap/logs results/30_megatron_overlap/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/30_megatron_overlap/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=none COURSE_CAPTURE=0 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"
source tools/course_job_cache.sh

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --help ]]; then
  printf 'Usage: sbatch [--nodes=1|2] slurm/fabric.sbatch labs/LAB.py [options]\n'
  exit 0
fi
: "${SLURM_JOB_ID:?submit with sbatch}"
: "${SLURM_JOB_NODELIST:?missing allocation}"
lab=labs/30_megatron_overlap.py
[[ -f $lab ]] || { printf 'ERROR: lab source missing.\n' >&2; exit 2; }
mapfile -t nodes < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
[[ ${#nodes[@]} == 1 || ${#nodes[@]} == 2 ]] || { printf 'ERROR: allocate one or two eight-H100 workers.\n' >&2; exit 2; }
export COURSE_NODES=${#nodes[@]}
export COURSE_WORLD_SIZE=$((COURSE_NODES * 8))
export MASTER_ADDR=${nodes[0]}
export MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
export COURSE_TORCHRUN=${COURSE_TORCHRUN}
export COURSE_PYTHON=${COURSE_PYTHON}
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
  bash -c 'exec "$COURSE_TORCHRUN" --nnodes="$COURSE_NODES" --nproc-per-node=8 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python "$COURSE_PYTHON" "$@"' bash "$lab" "$@"
