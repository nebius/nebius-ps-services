#!/usr/bin/env bash
#SBATCH --job-name=gpu-course-vllm-two-node
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1

usage() {
  printf 'Usage: sbatch slurm/vllm_two_node.sbatch MODE [MODEL] [REVISION] [REQUESTS] [CONCURRENCY]\n'
  printf 'MODE is pp, tp, or ep. EP requires an explicitly reviewed MoE MODEL and REVISION.\n'
}

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image as an OCI digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: the image must use @sha256 and COURSE_CONTAINER_RUNNER must be executable.\n' >&2
  exit 2
fi
command -v scontrol >/dev/null 2>&1 || { printf 'ERROR: scontrol is required.\n' >&2; exit 2; }

mode=${1:-}
case ${mode} in
  pp | tp | ep) ;;
  *) printf 'ERROR: MODE must be pp, tp, or ep.\n' >&2; exit 2 ;;
esac
if [[ ${mode} == ep && $# -lt 3 ]]; then
  printf 'ERROR: EP requires an explicitly reviewed MoE MODEL and immutable REVISION.\n' >&2
  exit 2
fi
model=${2:-Qwen/Qwen2.5-0.5B-Instruct}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
requests=${4:-8}
concurrency=${5:-2}
if [[ ! ${requests} =~ ^[1-9][0-9]*$ || ! ${concurrency} =~ ^[1-9][0-9]*$ ]]; then
  printf 'ERROR: REQUESTS and CONCURRENCY must be positive integers.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit this script from the course root directory.}
for lab in labs/11_serving_client.py labs/15_streaming_client.py; do
  if [[ ! -f ${course_dir}/${lab} ]]; then printf 'ERROR: missing %s.\n' "${lab}" >&2; exit 2; fi
done
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
allocated_nodes=$(scontrol show hostnames "${SLURM_JOB_NODELIST:?}")
master_addr=${allocated_nodes%%$'\n'*}
if [[ -z ${master_addr} ]]; then printf 'ERROR: unable to resolve the first allocated node.\n' >&2; exit 2; fi
master_port=$((20000 + SLURM_JOB_ID % 20000))
http_port=$((40000 + SLURM_JOB_ID % 10000))
export model revision master_addr master_port http_port mode
export VLLM_IMAGE_DIGEST COURSE_CONTAINER_RUNNER
server_step_pid=""

cleanup() {
  local pid=${server_step_pid:-}
  if [[ -z ${pid} ]]; then return; fi
  server_step_pid=""
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

start_server() {
  local trial=$1
  local server_log="${course_dir}/logs/vllm-two-node-${mode}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  # shellcheck disable=SC2016
  srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
    bash -c '
      set -euo pipefail
      parallel=(--tensor-parallel-size 1 --pipeline-parallel-size 2)
      if [[ ${mode} == tp ]]; then
        parallel=(--tensor-parallel-size 2 --pipeline-parallel-size 1)
      elif [[ ${mode} == ep ]]; then
        parallel=(--tensor-parallel-size 2 --pipeline-parallel-size 1 --enable-expert-parallel)
      fi
      command=("${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}" vllm serve "${model}"
        "${parallel[@]}" --revision "${revision}" --tokenizer-revision "${revision}"
        --distributed-executor-backend mp --nnodes 2 --node-rank "${SLURM_PROCID}"
        --master-addr "${master_addr}" --master-port "${master_port}"
        --host 127.0.0.1 --port "${http_port}" --dtype bfloat16 --max-model-len 2048)
      if [[ ${SLURM_PROCID} -ne 0 ]]; then command+=(--headless); fi
      exec "${command[@]}"
    ' >"${server_log}" 2>&1 &
  server_step_pid=$!

  local ready=0
  for _ in {1..300}; do
    if ! kill -0 "${server_step_pid}" 2>/dev/null; then
      printf 'ERROR: trial %s exited before readiness; inspect %s.\n' "${trial}" "${server_log}" >&2
      exit 2
    fi
    if "${course_python}" -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${http_port}/health', timeout=2)" \
      >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [[ ${ready} -ne 1 ]]; then
    printf 'ERROR: trial %s did not become ready within 600 seconds.\n' "${trial}" >&2
    exit 2
  fi
}

run_clients() {
  local trial=$1
  trial_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
  COURSE_RUN_ID=${trial_run_id} "${course_python}" "${course_dir}/labs/11_serving_client.py" \
    --base-url "http://127.0.0.1:${http_port}" --model "${model}" --revision "${revision}" \
    --requests "${requests}" --concurrency "${concurrency}" \
    --output "${course_dir}/results/11_serving_client-${mode}-trial-${trial}-run-${trial_run_id}.json"
  trial_run_id=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
  COURSE_RUN_ID=${trial_run_id} "${course_python}" "${course_dir}/labs/15_streaming_client.py" \
    --base-url "http://127.0.0.1:${http_port}" --model "${model}" --revision "${revision}" \
    --requests "${requests}" --concurrency "${concurrency}" \
    --output "${course_dir}/results/15_streaming_client-${mode}-trial-${trial}-run-${trial_run_id}.json"
  "${course_python}" -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${http_port}/metrics', timeout=10).read().decode(), end='')" \
    >"${course_dir}/results/vllm-two-node-${mode}-trial-${trial}-metrics-run-${COURSE_RUN_ID}.prom"
}

for trial in 1 2 3; do
  start_server "${trial}"
  run_clients "${trial}"
  cleanup
done
