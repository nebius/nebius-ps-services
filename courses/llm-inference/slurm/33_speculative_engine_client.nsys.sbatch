#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --no-requeue
#SBATCH --output=results/33_speculative_engine_client/logs/%j.out
#SBATCH --error=results/33_speculative_engine_client/logs/%j.err

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
umask 077
for directory in results results/33_speculative_engine_client results/33_speculative_engine_client/logs results/33_speculative_engine_client/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run course_setup.py prepare; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/33_speculative_engine_client/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=nsys COURSE_CAPTURE=1 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"


control_profile() {
  local code
  code=$(curl --fail --silent --show-error --max-time 90 --output /dev/null --write-out '%{http_code}' --request POST --data '' "http://127.0.0.1:${port}/${1}_profile")
  [[ $code == 200 ]] || { printf 'ERROR: server did not acknowledge profiler control.\n' >&2; return 2; }
}
command -v setsid >/dev/null
export VLLM_WORKER_MULTIPROC_METHOD=spawn
set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1
export COURSE_RUNTIME_ID="${VLLM_IMAGE_DIGEST:-unqualified}"

usage() {
  printf 'Usage: sbatch %s TARGET_MODEL TARGET_REVISION DRAFT_MODEL DRAFT_REVISION\n' "$0"
  printf 'Runs three independent, counterbalanced target-only/speculative trials.\n'
}

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then usage; exit 0; fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
if (($# != 4)); then usage >&2; exit 2; fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image digest}"
: "${AIPERF_IMAGE_DIGEST:?set the exact qualified AIPerf image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ${AIPERF_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: both images must use @sha256 and the runner must be executable.\n' >&2
  exit 2
fi
target_model=$1
target_revision=$2
draft_model=$3
draft_revision=$4
if [[ ! ${target_revision} =~ ^[0-9a-f]{40}$ || ! ${draft_revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: target and draft revisions must be immutable 40-character Hugging Face commit IDs.\n' >&2
  exit 2
fi
course_dir=$PWD
course_python=${COURSE_PYTHON:-python3}
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
if [[ ! -f ${course_dir}/labs/33_speculative_engine_client.py ]]; then
  printf 'ERROR: submit from the course root containing Lab 33.\n' >&2
  exit 2
fi
if [[ -z ${COURSE_RUN_ID:-} ]]; then
  COURSE_RUN_ID=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
fi
if [[ ! ${COURSE_RUN_ID} =~ ^[0-9a-f]{12}$ ]]; then
  printf 'ERROR: COURSE_RUN_ID must be exactly 12 lowercase hex characters.\n' >&2
  exit 2
fi
export COURSE_RUN_ID
base_dir="${COURSE_RESULTS_DIR}/speculative-ab-run-${COURSE_RUN_ID}"
mkdir -p "${COURSE_JOB_DIR}/logs" "${COURSE_RESULTS_DIR}"
mkdir -m 700 "${base_dir}"
port=$((20000 + SLURM_JOB_ID % 20000))
server_pid=""
cd "${course_dir}"

stop_server() {
  local pid=${server_pid:-} status=0 attempt
  [[ -n $pid ]] || return 0
  server_pid=""
  # The server/profiler owns a separate process group. Stop and reap it before
  # checking the report, allowing Nsight to finish serializing the capture.
  kill -INT -- "-$pid" 2>/dev/null || true
  for ((attempt=0; attempt<600; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then break; fi
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    printf 'ERROR: server/profiler required forced shutdown.\n' >&2
    return 2
  fi
  wait "$pid" || status=$?
  [[ $status == 0 || $status == 130 ]] || {
    printf 'ERROR: server/profiler exited with status %s.\n' "$status" >&2; return 2;
  }
}
handle_signal() { trap - INT TERM; exit "$1"; }
trap 'stop_server || true' EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

run_trial() {
  local variant=$1
  local trial=$2
  local server_log="${COURSE_JOB_DIR}/logs/vllm-speculative-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  local command=(
    "${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}"
    env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile '--trace=cuda,nvtx,osrt' \
      --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
      --discard-environment=true --force-overwrite=false \
      --duration=300 --kill=none --wait=all \
      --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
      --trace-fork-before-exec=true --cuda-graph-trace=node --capture-range=cudaProfilerApi --capture-range-end=stop vllm serve --profiler-config.profiler cuda "${target_model}" --revision "${target_revision}"
    --tokenizer-revision "${target_revision}" --host 127.0.0.1 --port "${port}"
    --dtype bfloat16 --max-model-len 2048
  )
  if [[ ${variant} == speculative ]]; then
    local speculative_config
    speculative_config=$("${course_python}" -c \
      'import json,sys; print(json.dumps({"method":"draft_model","model":sys.argv[1],"revision":sys.argv[2],"num_speculative_tokens":4}, separators=(",",":")))' \
      "${draft_model}" "${draft_revision}")
    command+=(--speculative-config "${speculative_config}")
  fi
  setsid "${command[@]}" >"${server_log}" 2>&1 &
  server_pid=$!
  local ready=0
  for _ in {1..180}; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      printf 'ERROR: %s trial %s exited before readiness; inspect %s.\n' \
        "${variant}" "${trial}" "${server_log}" >&2
      exit 2
    fi
    if "${course_python}" -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${port}/health', timeout=2)" \
      >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [[ ${ready} -ne 1 ]]; then
    printf 'ERROR: %s trial %s did not become ready.\n' "${variant}" "${trial}" >&2
    exit 2
  fi

  control_profile start
  "${course_python}" labs/33_speculative_engine_client.py \
    --base-url "http://127.0.0.1:${port}" --model "${target_model}" \
    --target-revision "${target_revision}" --draft-model "${draft_model}" \
    --draft-revision "${draft_revision}" --variant "${variant}" --requests 4 \
    --output "${base_dir}/${variant}-trial-${trial}-digests.json"
  local artifact_dir="${base_dir}/${variant}-trial-${trial}-aiperf"
  mkdir -m 700 "${artifact_dir}"
  "${COURSE_CONTAINER_RUNNER}" "${AIPERF_IMAGE_DIGEST}" \
    aiperf profile --model "${target_model}" --tokenizer-revision "${target_revision}" --url "http://127.0.0.1:${port}" \
    --endpoint-type completions --streaming \
    --synthetic-input-tokens-mean 512 --synthetic-input-tokens-stddev 0 \
    --output-tokens-mean 128 --output-tokens-stddev 0 \
    --concurrency 2 --warmup-request-count 4 --request-count 24 \
    --random-seed 100 --output-artifact-dir "${artifact_dir}" \
    --profile-export-prefix profile
  "${course_python}" -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
    >"${base_dir}/${variant}-trial-${trial}-metrics.prom"
  control_profile stop
  stop_server
}

compare_pair() {
  local trial=$1
  "${course_python}" -c \
    'import json,sys; a=json.load(open(sys.argv[1], encoding="utf-8")); b=json.load(open(sys.argv[2], encoding="utf-8")); left=a["measurements"]["response_digests"]; right=b["measurements"]["response_digests"]; raise SystemExit(0 if left == right else "target-only and speculative greedy outputs differ")' \
    "${base_dir}/target-only-trial-${trial}-digests.json" \
    "${base_dir}/speculative-trial-${trial}-digests.json"
}

for trial in 1 2 3; do
  if ((trial % 2 == 1)); then
    run_trial target-only "${trial}"
    run_trial speculative "${trial}"
  else
    run_trial speculative "${trial}"
    run_trial target-only "${trial}"
  fi
  compare_pair "${trial}"
done
printf 'Completed correctness-gated speculative-decoding A/B: %s\n' "${base_dir}"

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 6)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
