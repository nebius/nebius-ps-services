#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-speculative-ab
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

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
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
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
base_dir="${course_dir}/results/speculative-ab-run-${COURSE_RUN_ID}"
mkdir -p "${course_dir}/logs" "${course_dir}/results"
mkdir -m 700 "${base_dir}"
port=$((20000 + SLURM_JOB_ID % 20000))
server_pid=""
cd "${course_dir}"

stop_server() {
  local pid=${server_pid:-}
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill "${pid}" 2>/dev/null || true
  for _ in {1..50}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      wait "${pid}" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done
  kill -KILL "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
}
handle_signal() { trap - INT TERM; exit "$1"; }
trap stop_server EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

run_trial() {
  local variant=$1
  local trial=$2
  local server_log="${course_dir}/logs/vllm-speculative-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  local command=(
    "${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}"
    vllm serve "${target_model}" --revision "${target_revision}"
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
  "${command[@]}" >"${server_log}" 2>&1 &
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
