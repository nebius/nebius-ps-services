#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-training-capstone
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: sbatch %s [--profile smoke|h100] [other Lab 31 options]\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
course_python=${COURSE_PYTHON:-python3}
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
if [[ ! -f ${course_dir}/labs/31_training_capstone.py ]]; then
  printf 'ERROR: submit from the LLM Training course root.\n' >&2
  exit 2
fi
campaign_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
campaign_dir="${course_dir}/results/training-capstone-campaign-${SLURM_JOB_ID}-${campaign_run_id}"
mkdir -p -- "${course_dir}/results"
chmod 700 -- "${course_dir}/results"
mkdir -m 700 -- "${campaign_dir}"
trial_paths=()

for trial in 1 2 3; do
  order=baseline-first
  if ((trial % 2 == 0)); then order=candidate-first; fi
  trial_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
  COURSE_RUN_ID=${trial_run_id} srun --ntasks=1 --gpus-per-task=1 \
    "${course_python}" "${course_dir}/labs/31_training_capstone.py" \
    "$@" --seed "$((16 + trial))" --variant-order "${order}" \
    --output-dir "${campaign_dir}"
  trial_paths+=("${campaign_dir}/31_training_capstone-run-${trial_run_id}.json")
done
"${course_python}" "${course_dir}/tools/aggregate_capstone.py" \
  --input "${trial_paths[@]}" \
  --output "${campaign_dir}/training-capstone-summary-${campaign_run_id}.json"
