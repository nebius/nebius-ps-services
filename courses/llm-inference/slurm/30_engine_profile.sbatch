#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:01:30
#SBATCH --mem=64G
#SBATCH --no-requeue
#SBATCH --output=results/30_engine_profile/logs/%j.out
#SBATCH --error=results/30_engine_profile/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    trtllm_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-inference.30_engine_profile.trtllm --runtime
      source tools/course_job_cache.sh
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
course_dir=$PWD
if [[ ! -f ${course_dir}/labs/30_engine_profile.py ]]; then
  printf 'ERROR: submit from the course root containing labs/30_engine_profile.py.\n' >&2
  exit 2
fi
course_python=${COURSE_PYTHON}
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
mkdir -p "${COURSE_JOB_DIR}/logs" "${COURSE_RESULTS_DIR}"
port_offset=$((job_id % 9000))
http_port=$((20000 + port_offset))
grpc_port=$((30000 + port_offset))
metrics_port=$((40000 + port_offset))
server_log="${COURSE_JOB_DIR}/logs/trtllm-triton-run-${COURSE_RUN_ID}.log"

srun --nodes=1 --cpus-per-task=16 --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${TRTLLM_IMAGE_DIGEST}" \
  mpirun --allow-run-as-root --oversubscribe -n 1 \
  tritonserver --model-repository="${MODEL_REPOSITORY}" --exit-on-error=true \
  --disable-auto-complete-config \
  --http-address=127.0.0.1 --http-port="${http_port}" \
  --grpc-address=127.0.0.1 --grpc-port="${grpc_port}" \
  --metrics-address=127.0.0.1 --metrics-port="${metrics_port}" \
  >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
  local pid=${server_pid:-} status=0
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill "${pid}" 2>/dev/null || true
  for _ in {1..50}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      wait "${pid}" 2>/dev/null || status=$?
      [[ $status == 0 || $status == 143 ]] || return 2
      return
    fi
    sleep 0.1
  done
  kill -KILL "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
  printf "ERROR: server required forced shutdown.\n" >&2
  return 2
}
handle_signal() {
  trap - INT TERM
  exit "$1"
}
trap 'status=$?; cleanup || status=2; exit "$status"' EXIT
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
  --output-dir "${COURSE_RESULTS_DIR}"
printf 'PASS: private Triton log and sanitized Lab 30 evidence recorded for run %s.\n' "${COURSE_RUN_ID}"
      ;;
    default_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-inference.10_vllm_offline.ncu --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [MODEL] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the bounded OpenAI protocol probe, and stops the server.\n'
}

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
revision=${2:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
if [[ $# -gt 2 ]]; then show_usage >&2; exit 2; fi
course_dir=$PWD
if [[ ! -f "${course_dir}/labs/30_engine_profile.py" ]]; then
  printf 'ERROR: submit from the course root containing labs/30_engine_profile.py.\n' >&2
  exit 2
fi
course_python="${COURSE_PYTHON}"
command -v "${course_python}" >/dev/null 2>&1 || { printf 'ERROR: Python command not found: %s\n' "${course_python}" >&2; exit 2; }
if [[ -z ${COURSE_RUN_ID:-} ]]; then
  COURSE_RUN_ID=$("${course_python}" -c 'import secrets; print(secrets.token_hex(6))')
fi
if [[ ! ${COURSE_RUN_ID} =~ ^[0-9a-f]{12}$ ]]; then
  printf 'ERROR: COURSE_RUN_ID must be exactly 12 lowercase hex characters.\n' >&2
  exit 2
fi
export COURSE_RUN_ID
mkdir -p "${COURSE_JOB_DIR}/logs" "${COURSE_RESULTS_DIR}"
port=$((20000 + SLURM_JOB_ID % 20000))
owned_model="course-lab30-${COURSE_RUN_ID}"
"${course_python}" - "${port}" <<'PY'
import socket
import sys
with socket.socket() as listener:
    try:
        listener.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit("ERROR: selected loopback port is already occupied.")
PY
server_log="${COURSE_JOB_DIR}/logs/vllm-server-run-${COURSE_RUN_ID}.log"

setsid "${COURSE_VLLM:?run serving-lab-setup.py}" serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 \
  --served-model-name "${model}" "${owned_model}" \
  --max-model-len 2048 >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
  local pid=${server_pid:-} status=0
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill -- "-${pid}" 2>/dev/null || true
  local stop_attempts=50
  for ((attempt=0; attempt<stop_attempts; attempt++)); do
    if ! kill -0 -- "-${pid}" 2>/dev/null; then
      wait "${pid}" 2>/dev/null || status=$?
      [[ $status == 0 || $status == 143 ]] || return 2
      return
    fi
    sleep 0.1
  done
  kill -KILL -- "-${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
  printf "ERROR: server required forced shutdown.\n" >&2
  return 2
}
handle_signal() {
  trap - INT TERM
  exit "$1"
}
trap 'status=$?; cleanup || status=2; exit "$status"' EXIT
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
    "${course_python}" - "${port}" "${owned_model}" <<'PY'
import json
import sys
import urllib.request
with urllib.request.urlopen(
    f"http://127.0.0.1:{sys.argv[1]}/v1/models", timeout=2
) as response:
    models = json.load(response).get("data", [])
if not any(model.get("id") == sys.argv[2] for model in models):
    raise SystemExit("ERROR: ready server does not match this run's model identity.")
PY
    ready=1
    break
  fi
  sleep 2
done
if [[ ${ready} -ne 1 ]]; then printf 'ERROR: vLLM did not become ready within 240 seconds.\n' >&2; exit 2; fi

"${course_python}" "${course_dir}/labs/30_engine_profile.py" \
  --protocol openai --server-url "http://127.0.0.1:${port}" --model "${model}" \
  --output-dir "${COURSE_RESULTS_DIR}"
printf 'PASS: private vLLM log and sanitized Lab 30 evidence recorded for run %s.\n' "${COURSE_RUN_ID}"
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 30_engine_profile --launcher "$0" -- "$@"
fi
