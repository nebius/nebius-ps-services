#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-vllm-bench
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

show_usage() {
  printf 'Usage:\n  sbatch [site options] %s [MODEL] [CONCURRENCY] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the course client, and stops the server.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
concurrency=${2:-4}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${concurrency} =~ ^[1-9][0-9]*$ ]]; then printf 'ERROR: concurrency must be positive.\n' >&2; exit 2; fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
if [[ ! -f "${course_dir}/labs/11_serving_client.py" ]]; then
  printf 'ERROR: submit from the course root containing labs/11_serving_client.py.\n' >&2
  exit 2
fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2; exit 2; }
command -v vllm >/dev/null 2>&1 || { printf 'ERROR: activate the pinned serving environment before submission.\n' >&2; exit 2; }
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
server_log="${course_dir}/logs/vllm-server-run-${COURSE_RUN_ID}.log"

vllm serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 \
  --max-model-len 2048 >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
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
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

ready=0
for _ in $(seq 1 120); do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    printf 'ERROR: vLLM exited before becoming ready; inspect %s.\n' "${server_log}" >&2
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

"${course_python}" "${course_dir}/labs/11_serving_client.py" \
  --base-url "http://127.0.0.1:${port}" --model "${model}" \
  --revision "${revision}" \
  --concurrency "${concurrency}" \
  --output "${course_dir}/results/11_serving_client-run-${COURSE_RUN_ID}.json"
"${course_python}" -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
  >"${course_dir}/results/vllm-metrics-run-${COURSE_RUN_ID}.prom"
