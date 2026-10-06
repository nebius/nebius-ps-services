#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --no-requeue
#SBATCH --output=results/30_engine_profile/logs/%j.out
#SBATCH --error=results/30_engine_profile/logs/%j.err

set -euo pipefail

show_usage() {
  printf 'Usage:\n  sbatch [Slurm --name=value options] %s [MODEL] [REVISION]\n' "$0"
  printf '\nStarts a loopback-only vLLM server, runs the bounded OpenAI protocol probe, and stops the server.\n'
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
# Load this launcher's persistent installation inside the allocated job.
source tools/course_env.sh 30_engine_profile.sbatch
umask 077
for directory in results results/30_engine_profile results/30_engine_profile/logs results/30_engine_profile/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
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
source tools/course_job_cache.sh

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
  local pid=${server_pid:-}
  if [[ -z ${pid} ]]; then return; fi
  server_pid=""
  kill -- "-${pid}" 2>/dev/null || true
  local stop_attempts=50
  for ((attempt=0; attempt<stop_attempts; attempt++)); do
    if ! kill -0 -- "-${pid}" 2>/dev/null; then
      wait "${pid}" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done
  kill -KILL -- "-${pid}" 2>/dev/null || true
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
