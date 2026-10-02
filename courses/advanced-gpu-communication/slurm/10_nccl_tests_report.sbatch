#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=8
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00
#SBATCH --no-requeue
#SBATCH --output=results/10_nccl_tests_report/logs/%j.out
#SBATCH --error=results/10_nccl_tests_report/logs/%j.err

set -euo pipefail

show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s VARIANT [lab options]\n' "$0"
  printf '\nRuns MPI-enabled NCCL Tests on two nodes, eight full H100 GPUs per node.\n'
  printf 'Set COURSE_NCCL_TESTS to the shared all_reduce_perf executable.\n'
  printf 'Set COURSE_MPI to the site-qualified pmix/pmix_vN/pmi2 integration.\n'
  printf 'Optional COURSE_PYTHON selects Python; remaining options go to Lab 10.\n'
  printf 'Variants: default, socket, gdr-off, ring, tree, qp1, qp4.\n'
  printf 'Use --diagnostic for INFO logs; run at least three separate timing jobs.\n'
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
umask 077
for directory in results results/10_nccl_tests_report results/10_nccl_tests_report/logs results/10_nccl_tests_report/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run course_setup.py prepare; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/10_nccl_tests_report/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=none COURSE_CAPTURE=0 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" || "${SLURM_JOB_NUM_NODES:-}" != 1 && "${SLURM_JOB_NUM_NODES:-}" != 2 ]]; then
  printf 'ERROR: submit this script in Slurm with one or two eight-H100 nodes.\n' >&2; exit 2
fi
if [[ $# -lt 1 ]]; then show_usage >&2; exit 2; fi
variant="$1"
shift
case "${variant}" in default|socket|gdr-off|ring|tree|qp1|qp4) ;; *) printf 'ERROR: invalid variant.\n' >&2; exit 2 ;; esac
if [[ -z "${COURSE_NCCL_TESTS:-}" || -z "${COURSE_MPI:-}" ]]; then
  printf 'ERROR: set COURSE_NCCL_TESTS and the qualified COURSE_MPI mode.\n' >&2; exit 2
fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python unavailable.\n' >&2; exit 2; }
if [[ ! -f labs/10_nccl_tests_report.py ]]; then
  printf 'ERROR: submit from the Advanced GPU Communication course root.\n' >&2; exit 2
fi
srun --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "${course_python}" tools/fabric_guard.py
"${course_python}" labs/10_nccl_tests_report.py "$@" \
  --nodes "$SLURM_JOB_NUM_NODES" --mode run --binary "${COURSE_NCCL_TESTS}" --mpi "${COURSE_MPI}" --variant "${variant}"
