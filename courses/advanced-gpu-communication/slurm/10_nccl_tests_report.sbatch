#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=16
#SBATCH --ntasks-per-node=8
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=4
#SBATCH --time=00:27:00

#SBATCH --no-requeue
#SBATCH --output=results/10_nccl_tests_report/logs/%j.out
#SBATCH --error=results/10_nccl_tests_report/logs/%j.err

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
      # Actual scope: 2 node(s), 16 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.10_nccl_tests_report --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s VARIANT [lab options]\n' "$0"
  printf '\nRuns MPI-enabled NCCL Tests on two nodes, eight full H100 GPUs per node.\n'
  printf 'Set COURSE_NCCL_TESTS to the shared all_reduce_perf executable.\n'
  printf 'Set COURSE_MPI to the site-qualified pmix/pmix_vN/pmi2 integration.\n'
  printf 'Optional COURSE_PYTHON selects Python; remaining options go to Lab 10.\n'
  printf 'Variants: default, socket, gdr-off, ring, tree, qp1, qp4.\n'
  printf 'Use --diagnostic for INFO logs; run at least three separate timing jobs.\n'
}

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
course_python="${COURSE_PYTHON}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python unavailable.\n' >&2; exit 2; }
if [[ ! -f labs/10_nccl_tests_report.py ]]; then
  printf 'ERROR: submit from the Advanced GPU Communication course root.\n' >&2; exit 2
fi
srun --cpus-per-task=4 --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "${course_python}" tools/fabric_guard.py
"${course_python}" labs/10_nccl_tests_report.py "$@" \
  --nodes "$SLURM_JOB_NUM_NODES" --mode run --binary "${COURSE_NCCL_TESTS}" --mpi "${COURSE_MPI}" --variant "${variant}" \
 --worker-prefix env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt,nccl --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none --discard-environment=true --force-overwrite=false --duration=300 --kill=none --wait=all --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == $((SLURM_JOB_NUM_NODES * 8)))) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    default_none_cuda)
      # Actual scope: 2 node(s), 16 task(s), 8 GPU(s) per task.
      source tools/course_env.sh advanced-gpu-communication.10_nccl_tests_report --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s VARIANT [lab options]\n' "$0"
  printf '\nRuns MPI-enabled NCCL Tests on two nodes, eight full H100 GPUs per node.\n'
  printf 'Set COURSE_NCCL_TESTS to the shared all_reduce_perf executable.\n'
  printf 'Set COURSE_MPI to the site-qualified pmix/pmix_vN/pmi2 integration.\n'
  printf 'Optional COURSE_PYTHON selects Python; remaining options go to Lab 10.\n'
  printf 'Variants: default, socket, gdr-off, ring, tree, qp1, qp4.\n'
  printf 'Use --diagnostic for INFO logs; run at least three separate timing jobs.\n'
}

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
course_python="${COURSE_PYTHON}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python unavailable.\n' >&2; exit 2; }
if [[ ! -f labs/10_nccl_tests_report.py ]]; then
  printf 'ERROR: submit from the Advanced GPU Communication course root.\n' >&2; exit 2
fi
srun --cpus-per-task=4 --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --gpus-per-task=8 \
  "${course_python}" tools/fabric_guard.py
"${course_python}" labs/10_nccl_tests_report.py "$@" \
  --nodes "$SLURM_JOB_NUM_NODES" --mode run --binary "${COURSE_NCCL_TESTS}" --mpi "${COURSE_MPI}" --variant "${variant}"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 10_nccl_tests_report --launcher "$0" -- "$@"
fi
