#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --time=00:37:00

#SBATCH --no-requeue
#SBATCH --output=results/31_context_parallel/logs/%j.out
#SBATCH --error=results/31_context_parallel/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_nsys_cuda)
      # Actual scope: all 1 or 2 allocated nodes, one task and eight workers per node.
      source tools/course_env.sh advanced-gpu-communication.31_context_parallel --runtime
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
lab=labs/31_context_parallel.py
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
srun --cpus-per-task=32 --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "$COURSE_PYTHON" tools/fabric_guard.py
# One Slurm task launches eight torchrun workers on each node.
# shellcheck disable=SC2016
srun --cpus-per-task=32 --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 --kill-on-bad-exit=1 \
  bash -c '"$COURSE_TORCHRUN" --nnodes="$COURSE_NODES" --nproc-per-node=8 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt,nccl --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none --discard-environment=true --force-overwrite=false --duration=300 --kill=none --wait=all --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "$COURSE_PYTHON" "$@"' bash "$lab" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == COURSE_WORLD_SIZE)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    default_none_cuda)
      # Actual scope: all 1 or 2 allocated nodes, one task and eight workers per node.
      source tools/course_env.sh advanced-gpu-communication.31_context_parallel --runtime
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
lab=labs/31_context_parallel.py
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
srun --cpus-per-task=32 --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "$COURSE_PYTHON" tools/fabric_guard.py
# One Slurm task launches eight torchrun workers on each node.
# shellcheck disable=SC2016
srun --cpus-per-task=32 --nodes="$COURSE_NODES" --ntasks="$COURSE_NODES" --ntasks-per-node=1 --gpus-per-task=8 --kill-on-bad-exit=1 \
  bash -c 'exec "$COURSE_TORCHRUN" --nnodes="$COURSE_NODES" --nproc-per-node=8 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python "$COURSE_PYTHON" "$@"' bash "$lab" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 31_context_parallel --launcher "$0" -- "$@"
fi
