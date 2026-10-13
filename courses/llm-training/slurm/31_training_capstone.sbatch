#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:32:00

#SBATCH --no-requeue
#SBATCH --output=results/31_training_capstone/logs/%j.out
#SBATCH --error=results/31_training_capstone/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_ncu_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-training.01_tiny_transformer_train.ncu --runtime
      source tools/course_job_cache.sh
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off --kernel-name "regex:${COURSE_PROFILE_KERNEL:-.*}" \
  --launch-count 1 --set basic --section SpeedOfLight --section MemoryWorkloadAnalysis --section Occupancy \
  --clock-control none --force-overwrite false \
  --export "$COURSE_PROFILES_DIR/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "${COURSE_PYTHON}" labs/31_training_capstone.py --workload "$workload" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.ncu-rep' -print0)
((report_count == 1)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    default_nsys_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-training.01_tiny_transformer_train.ncu --runtime
      source tools/course_job_cache.sh
srun --nodes=1 --cpus-per-task=16 --ntasks=1 --gpus-per-task=1 \
  env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON}" labs/31_training_capstone.py --workload "$workload" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 1)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    trials_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-training.01_tiny_transformer_train.ncu --runtime
      source tools/course_job_cache.sh
set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: sbatch %s [--workload small|large] [other Lab 31 options]\n' "$0"
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
if [[ ! -f ${course_dir}/labs/31_training_capstone.py ]]; then
  printf 'ERROR: submit from the LLM Training course root.\n' >&2
  exit 2
fi
campaign_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
campaign_dir="${COURSE_RESULTS_DIR}/training-capstone-campaign-${SLURM_JOB_ID}-${campaign_run_id}"
mkdir -p -- "${COURSE_RESULTS_DIR}"
chmod 700 -- "${COURSE_RESULTS_DIR}"
mkdir -m 700 -- "${campaign_dir}"
trial_paths=()

for trial in 1 2 3; do
  order=baseline-first
  if ((trial % 2 == 0)); then order=candidate-first; fi
  trial_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
  COURSE_RUN_ID=${trial_run_id} srun --nodes=1 --cpus-per-task=16 --ntasks=1 --gpus-per-task=1 \
    "${course_python}" "${course_dir}/labs/31_training_capstone.py" \
    "$@" --seed "$((16 + trial))" --variant-order "${order}" \
    --output-dir "${campaign_dir}"
  trial_paths+=("${campaign_dir}/31_training_capstone-run-${trial_run_id}.json")
done
"${course_python}" "${course_dir}/tools/aggregate_capstone.py" \
  --input "${trial_paths[@]}" \
  --output "${campaign_dir}/training-capstone-summary-${campaign_run_id}.json"
      ;;
    default_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-training.01_tiny_transformer_train.ncu --runtime
      source tools/course_job_cache.sh
srun --nodes=1 --cpus-per-task=16 --ntasks=1 --gpus-per-task=1 "${COURSE_PYTHON}" labs/31_training_capstone.py --workload "$workload" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 31_training_capstone --launcher "$0" -- "$@"
fi
