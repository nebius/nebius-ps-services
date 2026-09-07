#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-vllm-chunk-ab
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s [MODEL] [REVISION]\n' "$0"
  printf '\nRuns three independent, counterbalanced vLLM/AIPerf trials per policy.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image digest}"
: "${AIPERF_IMAGE_DIGEST:?set the exact qualified AIPerf image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ${AIPERF_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: both images must use @sha256 and the runner must be executable.\n' >&2
  exit 2
fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
revision=${2:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
course_python=${COURSE_PYTHON:-python3}
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2; exit 2; }
if [[ -z ${COURSE_RUN_ID:-} ]]; then
  COURSE_RUN_ID=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
fi
if [[ ! ${COURSE_RUN_ID} =~ ^[0-9a-f]{12}$ ]]; then
  printf 'ERROR: COURSE_RUN_ID must be exactly 12 lowercase hex characters.\n' >&2
  exit 2
fi
base_dir="${course_dir}/results/chunked-prefill-ab-run-${COURSE_RUN_ID}"
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
    if ! kill -0 "${pid}" 2>/dev/null; then wait "${pid}" 2>/dev/null || true; return; fi
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
  local phase=$3
  local chunk_flag=--no-enable-chunked-prefill
  if [[ ${variant} == enabled ]]; then chunk_flag=--enable-chunked-prefill; fi
  local server_log="${course_dir}/logs/vllm-chunk-${phase}-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  local command=(
    "${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}"
    vllm serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}"
    --host 127.0.0.1 --port "${port}" --dtype bfloat16 --max-model-len 2048
    "${chunk_flag}"
  )
  if [[ ${variant} == enabled ]]; then
    command+=(--max-num-batched-tokens 256)
  fi
  "${command[@]}" >"${server_log}" 2>&1 &
  server_pid=$!
  local ready=0
  for _ in {1..120}; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      printf 'ERROR: vLLM %s exited before readiness; inspect %s.\n' "${variant}" "${server_log}" >&2
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
  if [[ ${ready} -ne 1 ]]; then printf 'ERROR: vLLM %s did not become ready.\n' "${variant}" >&2; exit 2; fi

  if [[ ${phase} == probe ]]; then
    "${course_python}" labs/34_policy_equivalence_client.py \
      --base-url "http://127.0.0.1:${port}" --model "${model}" \
      --revision "${revision}" --workload chunked-prefill --variant "${variant}" \
      --output "${base_dir}/${variant}-trial-${trial}-digests.json"
  else
    local artifact_dir="${base_dir}/${variant}-trial-${trial}-aiperf"
    mkdir -m 700 "${artifact_dir}"
    "${COURSE_CONTAINER_RUNNER}" "${AIPERF_IMAGE_DIGEST}" \
      aiperf profile --model "${model}" --tokenizer-revision "${revision}" --url "http://127.0.0.1:${port}" \
      --endpoint-type completions --streaming \
      --synthetic-input-tokens-mean 1024 --synthetic-input-tokens-stddev 0 \
      --output-tokens-mean 128 --output-tokens-stddev 0 \
      --concurrency 4 --warmup-request-count 4 --request-count 24 \
      --random-seed 100 --output-artifact-dir "${artifact_dir}" \
      --profile-export-prefix profile
  fi
  stop_server
}

compare_pair() {
  local trial=$1
  "${course_python}" -c \
    'import json,sys; a=json.load(open(sys.argv[1], encoding="utf-8")); b=json.load(open(sys.argv[2], encoding="utf-8")); raise SystemExit(0 if a["measurements"]["response_digests"] == b["measurements"]["response_digests"] else "chunked-prefill policies changed greedy outputs")' \
    "${base_dir}/disabled-trial-${trial}-digests.json" \
    "${base_dir}/enabled-trial-${trial}-digests.json"
}

# Correctness is gated before any performance profile is collected.
for trial in 1 2 3; do
  if ((trial % 2 == 1)); then
    run_trial disabled "${trial}" probe
    run_trial enabled "${trial}" probe
  else
    run_trial enabled "${trial}" probe
    run_trial disabled "${trial}" probe
  fi
  compare_pair "${trial}"
done
for trial in 1 2 3; do
  if ((trial % 2 == 1)); then
    run_trial disabled "${trial}" profile
    run_trial enabled "${trial}" profile
  else
    run_trial enabled "${trial}" profile
    run_trial disabled "${trial}" profile
  fi
done
printf 'Completed fixed-workload chunked-prefill A/B: %s\n' "${base_dir}"
