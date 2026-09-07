#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s VARIANT [lab options]\n' "$0"
  printf '\nRuns MPI-enabled NCCL Tests on two nodes, one full H100 per node.\n'
  printf 'Set COURSE_NCCL_TESTS to the shared all_reduce_perf executable.\n'
  printf 'Set COURSE_MPI to the site-qualified pmix/pmix_vN/pmi2 integration.\n'
  printf 'Optional COURSE_PYTHON selects Python; remaining options go to Lab 18.\n'
  printf 'Variants: default, socket, gdr-off, ring, tree, qp1, qp4.\n'
  printf 'Use --diagnostic for INFO logs; run at least three separate timing jobs.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" || "${SLURM_JOB_NUM_NODES:-}" != 2 || "${SLURM_NTASKS:-}" != 2 ]]; then
  printf 'ERROR: submit this script in Slurm with exactly two nodes and two tasks.\n' >&2; exit 2
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
if [[ ! -f labs/18_nccl_tests_report.py ]]; then
  printf 'ERROR: submit from the GPU Optimizations course root.\n' >&2; exit 2
fi
exec "${course_python}" labs/18_nccl_tests_report.py "$@" \
  --mode run --binary "${COURSE_NCCL_TESTS}" --mpi "${COURSE_MPI}" --variant "${variant}"
