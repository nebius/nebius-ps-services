#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-prefix-cache
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:40:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

usage() {
  printf 'Usage: sbatch slurm/vllm_prefix_cache.sbatch [MODEL] [REQUESTS_PER_COHORT] [REVISION]\n'
  printf 'Runs three independent, counterbalanced prefix-cache trials per policy.\n'
}

if [[ ${1:-} == "--help" ]]; then
  usage
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image as an OCI digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: the image must use @sha256 and COURSE_CONTAINER_RUNNER must be executable.\n' >&2
  exit 2
fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
requests=${2:-8}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
if [[ ! ${requests} =~ ^[0-9]+$ ]] || ((requests < 2)); then
  printf 'ERROR: REQUESTS_PER_COHORT must be an integer of at least two.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
if [[ ! -f ${course_dir}/labs/20_prefix_cache_client.py ]]; then
  printf 'ERROR: submit from the course root containing the prefix-cache client.\n' >&2
  exit 2
fi
course_python=${COURSE_PYTHON:-python3}
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
if [[ -z ${COURSE_RUN_ID:-} ]]; then
  COURSE_RUN_ID=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
fi
if [[ ! ${COURSE_RUN_ID} =~ ^[0-9a-f]{12}$ ]]; then
  printf 'ERROR: COURSE_RUN_ID must be exactly 12 lowercase hex characters.\n' >&2
  exit 2
fi
export COURSE_RUN_ID
mkdir -p "${course_dir}/logs" "${course_dir}/results"
port=$((20000 + SLURM_JOB_ID % 20000))
server_pid=""

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
handle_signal() {
  trap - INT TERM
  exit "$1"
}
trap stop_server EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

run_trial() {
  local variant=$1
  local trial=$2
  local phase=$3
  local server_log="${course_dir}/logs/vllm-prefix-${phase}-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  command=(
    "${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}" vllm serve "${model}"
    --revision "${revision}"
    --tokenizer-revision "${revision}"
    --host 127.0.0.1
    --port "${port}"
    --dtype bfloat16
    --max-model-len 2048
  )
  if [[ ${variant} == enabled ]]; then
    command+=(--enable-prefix-caching)
  else
    command+=(--no-enable-prefix-caching)
  fi
  "${command[@]}" >"${server_log}" 2>&1 &
  server_pid=$!
  ready=0
  for _ in {1..180}; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      printf 'ERROR: %s server exited before readiness; inspect %s.\n' \
        "${variant}" "${server_log}" >&2
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
    printf 'ERROR: %s server did not become ready within 360 seconds.\n' \
      "${variant}" >&2
    exit 2
  fi
  if [[ ${phase} == probe ]]; then
    "${course_python}" "${course_dir}/labs/34_policy_equivalence_client.py" \
      --base-url "http://127.0.0.1:${port}" --model "${model}" \
      --revision "${revision}" --workload prefix-cache --variant "${variant}" \
      --output "${course_dir}/results/20-prefix-cache-${variant}-trial-${trial}-digests-run-${COURSE_RUN_ID}.json"
  else
    "${course_python}" "${course_dir}/labs/20_prefix_cache_client.py" \
      --base-url "http://127.0.0.1:${port}" \
      --model "${model}" \
      --revision "${revision}" \
      --requests-per-cohort "${requests}" \
      --output "${course_dir}/results/20_prefix-cache-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.json"
    "${course_python}" -c \
      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
      >"${course_dir}/results/20_prefix-cache-${variant}-trial-${trial}-metrics-run-${COURSE_RUN_ID}.prom"
  fi
  stop_server
}

# Correctness is gated before any latency profile is collected.
compare_pair() {
  local trial=$1
  "${course_python}" -c \
    'import json,sys; a=json.load(open(sys.argv[1], encoding="utf-8")); b=json.load(open(sys.argv[2], encoding="utf-8")); raise SystemExit(0 if a["measurements"]["response_digests"] == b["measurements"]["response_digests"] else "prefix-cache policies changed greedy outputs")' \
    "${course_dir}/results/20-prefix-cache-disabled-trial-${trial}-digests-run-${COURSE_RUN_ID}.json" \
    "${course_dir}/results/20-prefix-cache-enabled-trial-${trial}-digests-run-${COURSE_RUN_ID}.json"
}

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
