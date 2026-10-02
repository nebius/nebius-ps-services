#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --no-requeue
#SBATCH --output=results/11_serving_client/logs/%j.out
#SBATCH --error=results/11_serving_client/logs/%j.err

set -euo pipefail

show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [MODEL] [CONCURRENCY] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the course client, and stops the server.\n'
}

# Workload size is independent of the profiler selected by this job file.
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  show_usage
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
for directory in results results/11_serving_client results/11_serving_client/logs results/11_serving_client/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run course_setup.py prepare; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/11_serving_client/jobs/$SLURM_JOB_ID"
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
export PYTHONDONTWRITEBYTECODE=1
export COURSE_RUNTIME_ID="${VLLM_IMAGE_DIGEST:-unqualified}"



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
: "${VLLM_IMAGE_DIGEST:?set the exact qualified vLLM image as an OCI digest}"
: "${COURSE_CONTAINER_RUNNER:?set the shared path to the reviewed container runner}"
if [[ ${VLLM_IMAGE_DIGEST} != *@sha256:* || ! -x ${COURSE_CONTAINER_RUNNER} ]]; then
  printf 'ERROR: the image must use @sha256 and COURSE_CONTAINER_RUNNER must be executable.\n' >&2
  exit 2
fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
concurrency=${2:-4}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
if [[ ! ${concurrency} =~ ^[1-9][0-9]*$ ]]; then printf 'ERROR: concurrency must be positive.\n' >&2; exit 2; fi
course_dir=$PWD
if [[ ! -f "${course_dir}/labs/11_serving_client.py" ]]; then
  printf 'ERROR: submit from the course root containing labs/11_serving_client.py.\n' >&2
  exit 2
fi
course_python="${COURSE_PYTHON:-python3}"
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
server_log="${COURSE_JOB_DIR}/logs/vllm-server-run-${COURSE_RUN_ID}.log"

"${COURSE_CONTAINER_RUNNER}" "${VLLM_IMAGE_DIGEST}" vllm serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 \
  --max-model-len 2048 >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
  local pid=${server_pid:-}
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill "${pid}" 2>/dev/null || true
  local stop_attempts=50
  if [[ ${COURSE_PROFILE_TOOL:-none} == nsys ]]; then stop_attempts=600; fi
  for ((attempt=0; attempt<stop_attempts; attempt++)); do
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
  --output "${COURSE_RESULTS_DIR}/11_serving_client-run-${COURSE_RUN_ID}.json"
"${course_python}" -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
  >"${COURSE_RESULTS_DIR}/vllm-metrics-run-${COURSE_RUN_ID}.prom"
