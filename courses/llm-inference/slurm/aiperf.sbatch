#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-aiperf
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:45:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s [MODEL] [REVISION]\n' "$0"
  printf '\nStarts pinned vLLM on loopback, runs a bounded streaming AIPerf profile, and stops the server.\n'
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
mkdir -p "${course_dir}/logs" "${course_dir}/results"
artifact_dir="${course_dir}/results/aiperf-run-${COURSE_RUN_ID}"
mkdir -m 700 "${artifact_dir}"
port=$((20000 + SLURM_JOB_ID % 20000))
server_log="${course_dir}/logs/aiperf-vllm-server-run-${COURSE_RUN_ID}.log"
cd "${course_dir}"

"${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}" \
  vllm serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 --max-model-len 2048 \
  >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
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
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

ready=0
for _ in {1..120}; do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    printf 'ERROR: vLLM exited before readiness; inspect %s.\n' "${server_log}" >&2
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
if [[ ${ready} -ne 1 ]]; then printf 'ERROR: vLLM did not become ready within 240 seconds.\n' >&2; exit 2; fi

"${COURSE_CONTAINER_RUNNER}" "${AIPERF_IMAGE_DIGEST}" \
  aiperf profile --model "${model}" --tokenizer-revision "${revision}" --url "http://127.0.0.1:${port}" \
  --endpoint-type completions --streaming \
  --synthetic-input-tokens-mean 512 --synthetic-input-tokens-stddev 0 \
  --output-tokens-mean 128 --output-tokens-stddev 0 \
  --concurrency 4 --warmup-request-count 4 --request-count 24 \
  --output-artifact-dir "${artifact_dir}" --profile-export-prefix profile
