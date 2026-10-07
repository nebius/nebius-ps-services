#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --output=results/12_capstone/logs/%j.out
#SBATCH --error=results/12_capstone/logs/%j.err

set -euo pipefail

# Workload size is independent of the profiler selected by this job file.
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  printf 'Usage: sbatch %s [--workload small|large] [lab options]\n' "$0"
  exit 0
fi
workload=${COURSE_WORKLOAD:-large}
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
source tools/course_env.sh 12_capstone.trials.sbatch
umask 077
for directory in results results/12_capstone results/12_capstone/logs results/12_capstone/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/12_capstone/jobs/$SLURM_JOB_ID"
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

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: sbatch %s EXECUTABLE [--workload small]\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$  ]]; then
  printf 'ERROR: submit with sbatch and provide the built Lab 12 executable.\n' >&2
  exit 2
fi
executable="${COURSE_BUILD_DIR:?select the completed build directory}/12_capstone"
if [[ ! -x ${executable} ]]; then
  printf 'ERROR: executable not found or not executable: %s\n' "${executable}" >&2
  exit 2
fi
course_dir=$PWD
command -v realpath >/dev/null 2>&1 || { printf 'ERROR: realpath is required.\n' >&2; exit 2; }
command -v sha256sum >/dev/null 2>&1 || { printf 'ERROR: sha256sum is required.\n' >&2; exit 2; }
executable_resolved=$(realpath "${executable}")
case ${executable_resolved} in
  "${course_dir}"/*/12_capstone) ;;
  *) printf 'ERROR: executable must be a course-local built 12_capstone binary.\n' >&2; exit 2 ;;
esac
executable_sha256=$(sha256sum "${executable_resolved}")
executable_sha256=${executable_sha256%% *}
run_id=$(date -u +%Y%m%dT%H%M%SZ)-${SLURM_JOB_ID}
result_dir="${COURSE_RESULTS_DIR}/cuda-capstone-${run_id}"
mkdir -p "${COURSE_RESULTS_DIR}"
mkdir -m 700 "${result_dir}"
trial_logs=()

for trial in 1 2 3; do
  order=baseline-first
  if ((trial % 2 == 0)); then order=candidate-first; fi
  trial_log="${result_dir}/trial-${trial}.txt"
  started=$(date +%s)
  srun --ntasks=1 --gpus-per-task=1 \
    "${executable_resolved}" \
    --workload "$workload" "$@" --variant-order "${order}" >"${trial_log}"
  ended=$(date +%s)
  "${COURSE_PYTHON}" tools/cuda_result.py --input "$trial_log" --started "$started" --ended "$ended" "$executable_resolved" --workload "$workload" "$@" --variant-order "$order"
  if ! grep -qx 'schema=gpu-course-result/v1' "${trial_log}" || \
     ! grep -qx 'lab_id=12_capstone' "${trial_log}" || \
     ! grep -qx 'trial_kind=one-fresh-process' "${trial_log}" || \
     ! grep -qx "variant_order=${order}" "${trial_log}" || \
     ! grep -qx 'baseline_correctness=passed' "${trial_log}" || \
     ! grep -qx 'candidate_correctness=passed' "${trial_log}"; then
    printf 'ERROR: identity, order, or correctness gate failed in %s.\n' "${trial_log}" >&2
    exit 2
  fi
  trial_logs+=("${trial_log}")
done

summary="${result_dir}/summary.txt"
awk -F= -v runtime_id="${COURSE_RUNTIME_ID}" \
  -v executable_path="${executable_resolved}" \
  -v executable_sha256="${executable_sha256}" '
  FNR == 1 { trial++ }
  /^variant_order=/ {
    if (++fields[trial, $1] != 1 || NF != 2 ||
        $2 != (trial == 2 ? "candidate-first" : "baseline-first")) invalid_contract = 1
  }
  /^elements=/ {
    if (++fields[trial, $1] != 1 || NF != 2 || $2 !~ /^[1-9][0-9]*$/) invalid_contract = 1
    if (elements == "") elements = $2
    else if (elements != $2) invalid_contract = 1
  }
  /^gpu_name=/ {
    if (++fields[trial, $1] != 1 || NF != 2 ||
        $2 !~ /^NVIDIA H(100|200)( |$)/ || $2 ~ /MIG/) invalid_contract = 1
    if (gpu_name == "") gpu_name = $2
    else if (gpu_name != $2) invalid_contract = 1
  }
  /^baseline_median_ms=/ {
    if (++fields[trial, $1] != 1 || NF != 2 || !positive_number($2)) invalid_contract = 1
    baseline[trial] = $2 + 0
  }
  /^candidate_median_ms=/ {
    if (++fields[trial, $1] != 1 || NF != 2 || !positive_number($2)) invalid_contract = 1
    candidate[trial] = $2 + 0
  }
  function positive_number(value, number) {
    if (value !~ /^[+]?[0-9]+([.][0-9]*)?([eE][+-]?[0-9]+)?$/) return 0
    number = value + 0
    return number > 0 && tolower(sprintf("%g", number)) !~ /inf|nan/
  }
  function median3(values, a, b, c) {
    a=values[1]; b=values[2]; c=values[3]
    if (a>b) { t=a; a=b; b=t } if (b>c) { t=b; b=c; c=t }
    if (a>b) { t=a; a=b; b=t } return b
  }
  END {
    if (trial != 3 || invalid_contract) exit 2
    for (i=1; i<=3; i++) {
      if (fields[i, "variant_order"] != 1 || fields[i, "elements"] != 1 ||
          fields[i, "gpu_name"] != 1 ||
          fields[i, "baseline_median_ms"] != 1 || fields[i, "candidate_median_ms"] != 1) exit 2
    }
    bm=median3(baseline); cm=median3(candidate)
    ratio=bm/cm
    if (!positive_number(sprintf("%.17g", ratio))) exit 2
    print "schema=gpu-course-capstone/v1"
    print "three_fresh_processes=true"
    print "counterbalanced_order=true"
    print "all_correctness_gates_passed=true"
    print "elements=" elements
    print "gpu_name=" gpu_name
    print "course_runtime_id=" runtime_id
    print "executable_path=" executable_path
    print "executable_sha256=" executable_sha256
    print "baseline_median_of_medians_ms=" bm
    print "candidate_median_of_medians_ms=" cm
    print "baseline_to_candidate_ratio=" ratio
    print "decision=" (cm < bm ? "candidate-for-scoped-keep" : "reject-under-tested-contract")
    print "claim_scope=exact single-GPU (" gpu_name ") executable, input size, image, and measurement contract"
    print "sanitizer_profiler_and_integration_evidence_required=true"
  }
' "${trial_logs[@]}" >"${summary}" || {
  printf 'ERROR: the three capstone trials could not be aggregated.\n' >&2
  exit 2
}
printf 'Wrote scoped CUDA capstone decision: %s\n' "${summary}"
