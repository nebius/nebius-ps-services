#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-vllm
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

usage() {
  printf 'Usage: sbatch slurm/vllm_two_node.sbatch [MODEL] [PORT] [REVISION] [REQUESTS] [CONCURRENCY]\n'
  printf 'Starts two-node pipeline-parallel vLLM, runs a bounded loopback benchmark, and stops it.\n'
}

if [[ ${1:-} == "--help" ]]; then
  usage
  exit 0
fi
if [[ -z "${SLURM_JOB_ID:-}" || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
if ! command -v scontrol >/dev/null 2>&1; then
  printf 'ERROR: scontrol is required to resolve the first allocated node.\n' >&2
  exit 2
fi

model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
port=${2:-8000}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
requests=${4:-8}
concurrency=${5:-2}
if [[ ! ${port} =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
  printf 'ERROR: PORT must be an integer from 1 through 65535.\n' >&2
  exit 2
fi
if [[ ! ${requests} =~ ^[1-9][0-9]*$ ]]; then
  printf 'ERROR: REQUESTS must be positive.\n' >&2
  exit 2
fi
if [[ ! ${concurrency} =~ ^[1-9][0-9]*$ ]]; then
  printf 'ERROR: CONCURRENCY must be positive.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
if [[ ! -f "${course_dir}/labs/11_serving_client.py" ]]; then
  printf 'ERROR: submit from the course root containing labs/11_serving_client.py.\n' >&2
  exit 2
fi
course_python="${COURSE_PYTHON:-python3}"
command -v "${course_python}" >/dev/null 2>&1 || {
  printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2
  exit 2
}
command -v vllm >/dev/null 2>&1 || {
  printf 'ERROR: activate the pinned serving environment before submission.\n' >&2
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
master_addr=$(scontrol show hostnames "${SLURM_JOB_NODELIST:?}" | head -n 1)
master_port=$((20000 + SLURM_JOB_ID % 20000))
export model port revision master_addr master_port
server_log="${course_dir}/logs/vllm-two-node-run-${COURSE_RUN_ID}.log"

# shellcheck disable=SC2016
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  bash -c '
    set -euo pipefail
    command=(vllm serve "${model}" --tensor-parallel-size 1 --pipeline-parallel-size 2
      --revision "${revision}" --tokenizer-revision "${revision}"
      --distributed-executor-backend mp
      --nnodes 2 --node-rank "${SLURM_PROCID}" --master-addr "${master_addr}"
      --master-port "${master_port}" --host 127.0.0.1 --port "${port}"
      --dtype bfloat16)
    if [[ ${SLURM_PROCID} -ne 0 ]]; then
      command+=(--headless)
    fi
    exec "${command[@]}"
  ' >"${server_log}" 2>&1 &
server_step_pid=$!

cleanup() {
  local pid=${server_step_pid:-}
  if [[ -z ${pid} ]]; then return; fi
  server_step_pid=""
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
for _ in $(seq 1 300); do
  if ! kill -0 "${server_step_pid}" 2>/dev/null; then
    printf 'ERROR: the two-node vLLM step exited before readiness; inspect %s.\n' "${server_log}" >&2
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
  printf 'ERROR: two-node vLLM did not become ready within 600 seconds.\n' >&2
  exit 2
fi

"${course_python}" "${course_dir}/labs/11_serving_client.py" \
  --base-url "http://127.0.0.1:${port}" --model "${model}" \
  --revision "${revision}" --requests "${requests}" \
  --concurrency "${concurrency}" \
  --output "${course_dir}/results/11_serving_client-two-node-run-${COURSE_RUN_ID}.json"
"${course_python}" -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
  >"${course_dir}/results/vllm-two-node-metrics-run-${COURSE_RUN_ID}.prom"
