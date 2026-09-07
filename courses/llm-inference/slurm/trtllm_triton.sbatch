#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00

set -euo pipefail
set -o noclobber
umask 077

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  printf 'Usage: TRTLLM_IMAGE_DIGEST=... MODEL_REPOSITORY=... TRITON_REPOSITORY_PROFILE=... TRITON_MODEL_NAME=... TRITON_MAX_TOKEN_FIELD=... sbatch %s\n' "$0"
  printf 'Profiles: llmapi+tensorrt_llm+sampling_param_max_tokens, or inflight_batcher+(ensemble|tensorrt_llm_bls)+max_tokens.\n'
  exit 0
fi
job_id=${SLURM_JOB_ID:-}
if [[ -z ${job_id} || ! ${job_id} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
: "${TRTLLM_IMAGE_DIGEST:?set the exact qualified TensorRT-LLM/Triton image digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
: "${MODEL_REPOSITORY:?set the reviewed model repository path}"
: "${TRITON_REPOSITORY_PROFILE:?set llmapi or inflight_batcher for the reviewed repository}"
: "${TRITON_MODEL_NAME:?set the reviewed repository model name}"
: "${TRITON_MAX_TOKEN_FIELD:?set the token field declared by that repository profile}"
if [[ ${TRTLLM_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: image must use @sha256 and the runner must be executable.\n' >&2
  exit 2
fi
if [[ ! -d ${MODEL_REPOSITORY} ]]; then
  printf 'ERROR: MODEL_REPOSITORY must be an existing reviewed directory.\n' >&2
  exit 2
fi
profile=${TRITON_REPOSITORY_PROFILE}
model=${TRITON_MODEL_NAME}
max_token_field=${TRITON_MAX_TOKEN_FIELD}
valid_profile=0
if [[ ${profile} == llmapi && ${model} == tensorrt_llm && ${max_token_field} == sampling_param_max_tokens ]]; then
  valid_profile=1
elif [[ ${profile} == inflight_batcher && ( ${model} == ensemble || ${model} == tensorrt_llm_bls ) && ${max_token_field} == max_tokens ]]; then
  valid_profile=1
fi
if [[ ${valid_profile} -ne 1 ]]; then
  printf 'ERROR: repository profile, model, and token field do not match a supported course contract.\n' >&2
  exit 2
fi
if [[ ! -f ${MODEL_REPOSITORY}/${model}/config.pbtxt ]]; then
  printf 'ERROR: the selected model has no config.pbtxt in MODEL_REPOSITORY.\n' >&2
  exit 2
fi
course_dir=${SLURM_SUBMIT_DIR:?Submit from the llm-inference course root.}
if [[ ! -f ${course_dir}/labs/30_engine_profile.py ]]; then
  printf 'ERROR: submit from the course root containing labs/30_engine_profile.py.\n' >&2
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
port_offset=$((job_id % 9000))
http_port=$((20000 + port_offset))
grpc_port=$((30000 + port_offset))
metrics_port=$((40000 + port_offset))
server_log="${course_dir}/logs/trtllm-triton-run-${COURSE_RUN_ID}.log"

srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${TRTLLM_IMAGE_DIGEST}" \
  tritonserver --model-repository="${MODEL_REPOSITORY}" --exit-on-error=true \
  --http-address=127.0.0.1 --http-port="${http_port}" \
  --grpc-address=127.0.0.1 --grpc-port="${grpc_port}" \
  --metrics-address=127.0.0.1 --metrics-port="${metrics_port}" \
  >"${server_log}" 2>&1 &
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
    printf 'ERROR: Triton exited before readiness; inspect the private run log.\n' >&2
    exit 2
  fi
  if "${course_python}" -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${http_port}/v2/health/ready', timeout=2)" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ ${ready} -ne 1 ]]; then
  printf 'ERROR: Triton did not become ready within 240 seconds.\n' >&2
  exit 2
fi

"${course_python}" "${course_dir}/labs/30_engine_profile.py" \
  --protocol triton --server-url "http://127.0.0.1:${http_port}" \
  --model "${model}" --triton-repository-profile "${profile}" \
  --triton-max-token-field "${max_token_field}" \
  --output-dir "${course_dir}/results"
printf 'PASS: private Triton log and sanitized Lab 30 evidence recorded for run %s.\n' "${COURSE_RUN_ID}"
