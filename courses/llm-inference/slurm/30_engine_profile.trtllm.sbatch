#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --output=results/30_engine_profile/logs/%j.out
#SBATCH --error=results/30_engine_profile/logs/%j.err

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
for directory in results results/30_engine_profile results/30_engine_profile/logs results/30_engine_profile/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run course_setup.py prepare; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/30_engine_profile/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=none COURSE_CAPTURE=0 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"

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
mkdir -p "${COURSE_JOB_DIR}/logs" "${COURSE_RESULTS_DIR}"
port_offset=$((job_id % 9000))
http_port=$((20000 + port_offset))
grpc_port=$((30000 + port_offset))
metrics_port=$((40000 + port_offset))
server_log="${COURSE_JOB_DIR}/logs/trtllm-triton-run-${COURSE_RUN_ID}.log"

srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${TRTLLM_IMAGE_DIGEST}" \
  mpirun --allow-run-as-root --oversubscribe -n 1 \
  tritonserver --model-repository="${MODEL_REPOSITORY}" --exit-on-error=true \
  --disable-auto-complete-config \
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
  --output-dir "${COURSE_RESULTS_DIR}"
printf 'PASS: private Triton log and sanitized Lab 30 evidence recorded for run %s.\n' "${COURSE_RUN_ID}"
