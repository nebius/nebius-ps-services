#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --output=results/32_inference_capstone/logs/%j.out
#SBATCH --error=results/32_inference_capstone/logs/%j.err

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
source tools/course_env.sh 32_inference_capstone.trials.sbatch
umask 077
for directory in results results/32_inference_capstone results/32_inference_capstone/logs results/32_inference_capstone/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/32_inference_capstone/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=none COURSE_CAPTURE=0 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"
source tools/course_job_cache.sh

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: sbatch %s [--workload small|large] [other Lab 32 options]\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
course_dir=$PWD
course_python=${COURSE_PYTHON}
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
if [[ ! -f ${course_dir}/labs/32_inference_capstone.py ]]; then
  printf 'ERROR: submit from the LLM Inference course root.\n' >&2
  exit 2
fi
campaign_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
campaign_dir="${COURSE_RESULTS_DIR}/inference-capstone-campaign-${SLURM_JOB_ID}-${campaign_run_id}"
mkdir -p -- "${COURSE_RESULTS_DIR}"
chmod 700 -- "${COURSE_RESULTS_DIR}"
mkdir -m 700 -- "${campaign_dir}"
trial_paths=()

for trial in 1 2 3; do
  order=baseline-first
  if ((trial % 2 == 0)); then order=candidate-first; fi
  trial_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
  COURSE_RUN_ID=${trial_run_id} srun --ntasks=1 --gpus-per-task=1 \
    "${course_python}" "${course_dir}/labs/32_inference_capstone.py" \
    "$@" --seed "$((16 + trial))" --variant-order "${order}" \
    --output-dir "${campaign_dir}"
  trial_paths+=("${campaign_dir}/32_inference_capstone-run-${trial_run_id}.json")
done
"${course_python}" "${course_dir}/tools/aggregate_capstone.py" \
  --input "${trial_paths[@]}" \
  --output "${campaign_dir}/inference-capstone-summary-${campaign_run_id}.json"
