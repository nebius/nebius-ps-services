#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-cuda-capstone
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  printf 'Usage: sbatch %s EXECUTABLE [--smoke]\n' "$0"
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ || $# -lt 1 ]]; then
  printf 'ERROR: submit with sbatch and provide the built Lab 12 executable.\n' >&2
  exit 2
fi
: "${CUDA_IMAGE_DIGEST:?set the exact qualified CUDA development image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${CUDA_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: immutable image and executable runner required.\n' >&2
  exit 2
fi
executable=$1
shift
if [[ ! -x ${executable} ]]; then
  printf 'ERROR: executable not found or not executable: %s\n' "${executable}" >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
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
result_dir="${course_dir}/results/cuda-capstone-${run_id}"
mkdir -p "${course_dir}/results"
mkdir -m 700 "${result_dir}"
trial_logs=()

for trial in 1 2 3; do
  order=baseline-first
  if ((trial % 2 == 0)); then order=candidate-first; fi
  trial_log="${result_dir}/trial-${trial}.txt"
  srun --ntasks=1 --gpus-per-task=1 \
    "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" "${executable_resolved}" \
    "$@" --variant-order "${order}" >"${trial_log}"
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
awk -F= -v image_digest="${CUDA_IMAGE_DIGEST}" \
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
    print "cuda_image_digest=" image_digest
    print "executable_path=" executable_path
    print "executable_sha256=" executable_sha256
    print "baseline_median_of_medians_ms=" bm
    print "candidate_median_of_medians_ms=" cm
    print "baseline_to_candidate_ratio=" ratio
    print "decision=" (cm < bm ? "candidate-for-scoped-keep" : "reject-under-tested-contract")
    print "claim_scope=exact single-H100 executable, input size, image, and measurement contract"
    print "sanitizer_profiler_and_integration_evidence_required=true"
  }
' "${trial_logs[@]}" >"${summary}" || {
  printf 'ERROR: the three capstone trials could not be aggregated.\n' >&2
  exit 2
}
printf 'Wrote scoped CUDA capstone decision: %s\n' "${summary}"
