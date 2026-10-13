#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --time=00:32:30
#SBATCH --mem=64G
#SBATCH --no-requeue
#SBATCH --output=results/11_serving_client/logs/%j.out
#SBATCH --error=results/11_serving_client/logs/%j.err

# Reserve the largest selectable allocation; every srun below limits its step.
# Each stage runs in a fresh shell with separate original results and reports.
course_stage() {
  set -euo pipefail
  umask 077
  local selection=$1 workload=$2
  shift 2
  export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
  case "$selection" in
    default_nsys_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-inference.10_vllm_offline.ncu --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [MODEL] [CONCURRENCY] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the course client, and stops the server.\n'
}

control_profile() {
  local code
  code=$(curl --fail --silent --show-error --max-time 90 --output /dev/null --write-out '%{http_code}' --request POST --data '' "http://127.0.0.1:${port}/${1}_profile")
  [[ $code == 200 ]] || { printf 'ERROR: server did not acknowledge profiler control.\n' >&2; return 2; }
}
command -v setsid >/dev/null
export VLLM_WORKER_MULTIPROC_METHOD=spawn
set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
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
server_log="${COURSE_JOB_DIR}/logs/vllm-server-run-${COURSE_RUN_ID}.log"

setsid env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  --trace-fork-before-exec=true --cuda-graph-trace=node --capture-range=cudaProfilerApi --capture-range-end=stop "${COURSE_VLLM:?run serving-lab-setup.py}" serve --profiler-config.profiler cuda "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 \
  --max-model-len 2048 >"${server_log}" 2>&1 &
server_pid=$!
stop_server() {
  local pid=${server_pid:-} status=0 attempt
  [[ -n $pid ]] || return 0
  server_pid=""
  # The server/profiler owns a separate process group. Stop and reap it before
  # checking the report, allowing Nsight to finish serializing the capture.
  kill -INT -- "-$pid" 2>/dev/null || true
  for ((attempt=0; attempt<600; attempt++)); do
    if ! kill -0 -- "-$pid" 2>/dev/null; then break; fi
    sleep 0.1
  done
  if kill -0 -- "-$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    printf 'ERROR: server/profiler required forced shutdown.\n' >&2
    return 2
  fi
  wait "$pid" || status=$?
  [[ $status == 0 || $status == 130 ]] || {
    printf 'ERROR: server/profiler exited with status %s.\n' "$status" >&2; return 2;
  }
}
handle_signal() {
  trap - INT TERM
  exit "$1"
}
trap 'stop_server || true' EXIT
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

control_profile start
"${course_python}" "${course_dir}/labs/11_serving_client.py" \
  --base-url "http://127.0.0.1:${port}" --model "${model}" \
  --revision "${revision}" \
  --concurrency "${concurrency}" \
  --output "${COURSE_RESULTS_DIR}/11_serving_client-run-${COURSE_RUN_ID}.json"
"${course_python}" -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
  >"${COURSE_RESULTS_DIR}/vllm-metrics-run-${COURSE_RUN_ID}.prom"

control_profile stop

stop_server

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 1)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
      ;;
    default_none_cuda)
      # Actual scope: 1 node(s), 1 task(s), 1 GPU(s) per task.
      source tools/course_env.sh llm-inference.10_vllm_offline.ncu --runtime
      source tools/course_job_cache.sh
show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [MODEL] [CONCURRENCY] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the course client, and stops the server.\n'
}

set -euo pipefail
set -o noclobber
umask 077
export PYTHONDONTWRITEBYTECODE=1



if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then show_usage; exit 0; fi
if [[ -z "${SLURM_JOB_ID:-}" ]]; then printf 'ERROR: submit this script with sbatch.\n' >&2; exit 2; fi
if [[ ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then printf 'ERROR: SLURM_JOB_ID must be numeric.\n' >&2; exit 2; fi
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
server_log="${COURSE_JOB_DIR}/logs/vllm-server-run-${COURSE_RUN_ID}.log"

setsid "${COURSE_VLLM:?run serving-lab-setup.py}" serve "${model}" --revision "${revision}" --tokenizer-revision "${revision}" \
  --host 127.0.0.1 --port "${port}" --dtype bfloat16 \
  --max-model-len 2048 >"${server_log}" 2>&1 &
server_pid=$!
cleanup() {
  local pid=${server_pid:-} status=0
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill -- "-${pid}" 2>/dev/null || true
  local stop_attempts=50
  if [[ ${COURSE_PROFILE_TOOL:-none} == nsys ]]; then stop_attempts=600; fi
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
      ;;
    *) printf 'ERROR: unknown stage selection.\n' >&2; return 2 ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  exec python3.12 -B tools/course_job.py --lab 11_serving_client --launcher "$0" -- "$@"
fi
