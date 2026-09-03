#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=00:45:00

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s LAB.py [lab options]\n' "$0"
  printf '\nRuns torchrun with one rank on each of two H100 nodes.\n'
  printf 'Set COURSE_TORCHRUN to select the torchrun executable.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_usage
  exit 0
fi
if [[ $# -lt 1 ]]; then
  show_usage >&2
  exit 2
fi
if [[ -z "${SLURM_JOB_ID:-}" || -z "${SLURM_JOB_NODELIST:-}" ]]; then
  printf 'ERROR: submit this script with sbatch.\n' >&2
  exit 2
fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi

lab_path="$1"
shift
if [[ ! -f "${lab_path}" ]]; then
  printf 'ERROR: lab file not found: %s\n' "${lab_path}" >&2
  exit 2
fi

command -v scontrol >/dev/null 2>&1 || {
  printf 'ERROR: required command not found: scontrol\n' >&2
  exit 2
}
mapfile -t allocated_nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST}")
if [[ "${#allocated_nodes[@]}" -ne 2 ]]; then
  printf 'ERROR: expected two allocated nodes, received %s.\n' "${#allocated_nodes[@]}" >&2
  exit 2
fi

export MASTER_ADDR="${allocated_nodes[0]}"
if [[ -n "${MASTER_PORT:-}" ]]; then
  if [[ ! ${MASTER_PORT} =~ ^[0-9]+$ ]] || ((MASTER_PORT < 1 || MASTER_PORT > 65535)); then
    printf 'ERROR: MASTER_PORT must be an integer from 1 through 65535.\n' >&2
    exit 2
  fi
else
  MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))
fi
export MASTER_PORT
export COURSE_TORCHRUN="${COURSE_TORCHRUN:-torchrun}"
command -v "${COURSE_TORCHRUN}" >/dev/null 2>&1 || {
  printf 'ERROR: torchrun command not found: %s\n' "${COURSE_TORCHRUN}" >&2
  exit 2
}

# shellcheck disable=SC2016
srun --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  bash -c 'exec "$COURSE_TORCHRUN" --nnodes=2 --nproc-per-node=1 --node-rank="$SLURM_NODEID" --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" "$@"' \
  bash "${lab_path}" "$@"
