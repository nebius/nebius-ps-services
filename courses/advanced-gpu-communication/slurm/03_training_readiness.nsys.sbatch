#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --no-requeue
#SBATCH --output=results/03_training_readiness/logs/%j.out
#SBATCH --error=results/03_training_readiness/logs/%j.err

set -euo pipefail

show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [--workload small|large] [lab options]\n' "$0"
  printf '\nRuns torchrun with one rank on each of two eight-H100 fabric nodes (bounded mechanics).\n'
  printf 'Set COURSE_TORCHRUN to select the torchrun executable.\n'
}

# Workload size is independent of the profiler selected by this job file.
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  show_usage
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
source tools/course_env.sh 03_training_readiness.nsys.sbatch
umask 077
for directory in results results/03_training_readiness results/03_training_readiness/logs results/03_training_readiness/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/03_training_readiness/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=nsys COURSE_CAPTURE=1 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"
source tools/course_job_cache.sh

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ ! -f labs/03_training_readiness.py ]]; then show_usage >&2; exit 2; fi
if [[ -z "${SLURM_JOB_ID:-}" || -z "${SLURM_JOB_NODELIST:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
lab_path=labs/03_training_readiness.py
if [[ ! -f "${lab_path}" ]]; then printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2; exit 2; fi
command -v scontrol >/dev/null 2>&1 || { printf 'ERROR: required command not found: scontrol\n' >&2; exit 2; }
mapfile -t allocated_nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST}")
if [[ "${#allocated_nodes[@]}" -ne 2 ]]; then printf 'ERROR: expected two allocated nodes, received %s.\n' "${#allocated_nodes[@]}" >&2; exit 2; fi
export MASTER_ADDR="${allocated_nodes[0]}"
if [[ -n "${MASTER_PORT:-}" ]]; then
  if [[ ! ${MASTER_PORT} =~ ^[0-9]+$ ]] || ((MASTER_PORT < 1 || MASTER_PORT > 65535)); then printf 'ERROR: MASTER_PORT must be an integer from 1 through 65535.\n' >&2; exit 2; fi
else
  MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
fi
export MASTER_PORT
export COURSE_TORCHRUN="${COURSE_TORCHRUN}"
command -v "${COURSE_TORCHRUN}" >/dev/null 2>&1 || { printf 'ERROR: torchrun command not found: %s\n' "${COURSE_TORCHRUN}" >&2; exit 2; }
srun --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 "${COURSE_PYTHON}" tools/fabric_guard.py
# shellcheck disable=SC2016
srun --kill-on-bad-exit=1 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 \
  bash -c '"$COURSE_TORCHRUN" --nnodes=2 --nproc-per-node=1 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" --no-python env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt,nccl --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none --discard-environment=true --force-overwrite=false --duration=300 --kill=none --wait=all --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "${COURSE_PYTHON}" "$@"' \
  bash "${lab_path}" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 2)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
