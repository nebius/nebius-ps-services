#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:12:40

#SBATCH --no-requeue
#SBATCH --output=results/12_capstone/logs/%j.out
#SBATCH --error=results/12_capstone/logs/%j.err

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
      source tools/course_env.sh custom-cuda-kernels.12_capstone --runtime
      source tools/course_job_cache.sh
: "${COURSE_BUILD_DIR:?select the completed build directory}"
executable="$COURSE_BUILD_DIR/12_capstone"
[[ -x $executable && ! -L $executable ]] || exit 2
raw="$COURSE_JOB_DIR/logs/12_capstone.txt"
started=$(date +%s)
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off --kernel-name "regex:${COURSE_PROFILE_KERNEL:-.*}" \
  --launch-count 1 --set basic --section SpeedOfLight --section MemoryWorkloadAnalysis --section Occupancy \
  --clock-control none \
  --export "$COURSE_PROFILES_DIR/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "$executable" --workload "$workload" "$@" >"$raw"
ended=$(date +%s)
"${COURSE_PYTHON}" tools/cuda_result.py --input "$raw" --started "$started" --ended "$ended" "$executable" --workload "$workload" "$@"

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
      source tools/course_env.sh custom-cuda-kernels.12_capstone --runtime
      source tools/course_job_cache.sh
: "${COURSE_BUILD_DIR:?select the completed build directory}"
executable="$COURSE_BUILD_DIR/12_capstone"
[[ -x $executable && ! -L $executable ]] || exit 2
raw="$COURSE_JOB_DIR/logs/12_capstone.txt"
started=$(date +%s)
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none --discard-environment=true --force-overwrite=false --duration=300 --kill=none --wait=all --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" "$executable" --workload "$workload" "$@" >"$raw"
ended=$(date +%s)
"${COURSE_PYTHON}" tools/cuda_result.py --input "$raw" --started "$started" --ended "$ended" "$executable" --workload "$workload" "$@"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 1)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    sanitizer_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh custom-cuda-kernels.12_capstone --runtime
      source tools/course_job_cache.sh
set -euo pipefail
umask 077
tool="${COURSE_SANITIZER:?select --sanitizer}"
executable="${COURSE_BUILD_DIR:?select completed build}/12_capstone"
case ${tool} in
  memcheck | racecheck | initcheck | synccheck) ;;
  *) printf 'ERROR: unsupported sanitizer tool: %s\n' "${tool}" >&2; exit 2 ;;
esac
sanitizer_options=(--tool "${tool}" --error-exitcode=3)
if [[ ${tool} == racecheck ]]; then
  : "${SLURM_CPUS_PER_TASK:?Racecheck requires the Slurm CPU allocation}"
  if [[ ! ${SLURM_CPUS_PER_TASK} =~ ^[1-9][0-9]*$ ]]; then
    printf 'ERROR: SLURM_CPUS_PER_TASK must be a positive integer.\n' >&2
    exit 2
  fi
  sanitizer_options+=(--racecheck-num-workers "${SLURM_CPUS_PER_TASK}" --force-synchronization-limit 2)
fi
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 \
  compute-sanitizer "${sanitizer_options[@]}" "${executable}" --workload "$workload" "$@"
      ;;
    trials_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh custom-cuda-kernels.12_capstone --runtime
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
  srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 \
    "${executable_resolved}" \
    --workload "$workload" "$@" --variant-order "${order}" >"${trial_log}"
  ended=$(date +%s)
  COURSE_TRIAL_INDEX=${trial} "${COURSE_PYTHON}" tools/cuda_result.py --input "$trial_log" --started "$started" --ended "$ended" "$executable_resolved" --workload "$workload" "$@" --variant-order "$order"
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
      ;;
    default_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh custom-cuda-kernels.12_capstone --runtime
      source tools/course_job_cache.sh
: "${COURSE_BUILD_DIR:?select the completed build directory}"
executable="$COURSE_BUILD_DIR/12_capstone"
[[ -x $executable && ! -L $executable ]] || exit 2
raw="$COURSE_JOB_DIR/logs/12_capstone.txt"
started=$(date +%s)
srun --nodes=1 --cpus-per-task=8 --ntasks=1 --gpus-per-task=1 "$executable" --workload "$workload" "$@" >"$raw"
ended=$(date +%s)
"${COURSE_PYTHON}" tools/cuda_result.py --input "$raw" --started "$started" --ended "$ended" "$executable" --workload "$workload" "$@"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 12_capstone --launcher "$0" -- "$@"
fi
