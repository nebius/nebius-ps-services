#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:40:00
#SBATCH --no-requeue
#SBATCH --output=results/20_prefix_cache_client/logs/%j.out
#SBATCH --error=results/20_prefix_cache_client/logs/%j.err

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
# Load this launcher's persistent installation inside the allocated job.
source tools/course_env.sh 20_prefix_cache_client.nsys.sbatch
umask 077
for directory in results results/20_prefix_cache_client results/20_prefix_cache_client/logs results/20_prefix_cache_client/jobs; do
  [[ -d $directory && ! -L $directory && $(stat -c '%a:%u' "$directory") == "700:$(id -u)" ]] || {
    printf 'ERROR: run regular-lab-setup.py; require private owned directory: %s\n' "$directory" >&2; exit 2;
  }
done
export COURSE_JOB_DIR="$PWD/results/20_prefix_cache_client/jobs/$SLURM_JOB_ID"
mkdir -m 700 -- "$COURSE_JOB_DIR"
mkdir -m 700 -- "$COURSE_JOB_DIR/results" "$COURSE_JOB_DIR/profiles" "$COURSE_JOB_DIR/logs" "$COURSE_JOB_DIR/artifacts"
export SLURM_STEP_ID=${SLURM_STEP_ID:-batch} SLURM_PROCID=${SLURM_PROCID:-0}
export COURSE_RESULTS_DIR="$COURSE_JOB_DIR/results"
export COURSE_PROFILES_DIR="$COURSE_JOB_DIR/profiles"
export COURSE_PROFILE_TOOL=nsys COURSE_CAPTURE=1 PYTHONDONTWRITEBYTECODE=1
printf 'Job results: %s\n' "$COURSE_JOB_DIR"
source tools/course_job_cache.sh


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

usage() {
  printf 'Usage: sbatch slurm/vllm_prefix_cache.sbatch [MODEL] [REQUESTS_PER_COHORT] [REVISION]\n'
  printf 'Runs three independent, counterbalanced prefix-cache trials per policy.\n'
  printf 'Uses batch-invariant execution and TRITON_ATTN in every policy and phase.\n'
}

if [[ ${1:-} == "--help" ]]; then
  usage
  exit 0
fi
if [[ -z ${SLURM_JOB_ID:-} || ! ${SLURM_JOB_ID} =~ ^[0-9]+$ ]]; then
  printf 'ERROR: submit with sbatch so SLURM_JOB_ID is numeric.\n' >&2
  exit 2
fi
model=${1:-Qwen/Qwen2.5-0.5B-Instruct}
requests=${2:-8}
revision=${3:-7ae557604adf67be50417f59c2c2f167def9a775}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: REVISION must be an immutable 40-character Hugging Face commit ID.\n' >&2
  exit 2
fi
if [[ ! ${requests} =~ ^[0-9]+$ ]] || ((requests < 2)); then
  printf 'ERROR: REQUESTS_PER_COHORT must be an integer of at least two.\n' >&2
  exit 2
fi
course_dir=$PWD
if [[ ! -f ${course_dir}/labs/20_prefix_cache_client.py ]]; then
  printf 'ERROR: submit from the course root containing the prefix-cache client.\n' >&2
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
port=$((20000 + SLURM_JOB_ID % 20000))
server_pid=""

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

run_trial() {
  local variant=$1
  local trial=$2
  local phase=$3
  local server_log="${COURSE_JOB_DIR}/logs/vllm-prefix-${phase}-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.log"
  command=(
    env VLLM_BATCH_INVARIANT=1 env -u DEBUGINFOD_URLS timeout --signal=TERM --kill-after=15s 900s nsys profile '--trace=cuda,nvtx,osrt' \
      --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
      --discard-environment=true --force-overwrite=false \
      --duration=300 --kill=none --wait=all \
      --output "$COURSE_PROFILES_DIR/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
      --trace-fork-before-exec=true --cuda-graph-trace=node --capture-range=cudaProfilerApi --capture-range-end=stop "${COURSE_VLLM:?run serving-lab-setup.py}" serve --profiler-config.profiler cuda "${model}"
    --revision "${revision}"
    --tokenizer-revision "${revision}"
    --host 127.0.0.1
    --port "${port}"
    --dtype bfloat16
    --attention-config '{"backend":"TRITON_ATTN"}'
    --max-model-len 2048
  )
  if [[ ${variant} == enabled ]]; then
    command+=(--enable-prefix-caching)
  else
    command+=(--no-enable-prefix-caching)
  fi
  setsid "${command[@]}" >"${server_log}" 2>&1 &
  server_pid=$!
  ready=0
  for _ in {1..180}; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      printf 'ERROR: %s server exited before readiness; inspect %s.\n' \
        "${variant}" "${server_log}" >&2
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
    printf 'ERROR: %s server did not become ready within 360 seconds.\n' \
      "${variant}" >&2
    exit 2
  fi
  if [[ ${phase} == probe ]]; then
    control_profile start
    "${course_python}" "${course_dir}/labs/34_policy_equivalence_client.py" \
      --base-url "http://127.0.0.1:${port}" --model "${model}" \
      --revision "${revision}" --workload prefix-cache --variant "${variant}" \
      --output "${COURSE_RESULTS_DIR}/20-prefix-cache-${variant}-trial-${trial}-digests-run-${COURSE_RUN_ID}.json"
  else
    control_profile start
    "${course_python}" "${course_dir}/labs/20_prefix_cache_client.py" --variant "${variant}" \
      --base-url "http://127.0.0.1:${port}" \
      --model "${model}" \
      --revision "${revision}" \
      --requests-per-cohort "${requests}" \
      --output "${COURSE_RESULTS_DIR}/20_prefix-cache-${variant}-trial-${trial}-run-${COURSE_RUN_ID}.json"
    "${course_python}" -c \
      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/metrics', timeout=10).read().decode(), end='')" \
      >"${COURSE_RESULTS_DIR}/20_prefix-cache-${variant}-trial-${trial}-metrics-run-${COURSE_RUN_ID}.prom"
  fi
  control_profile stop
  stop_server
}

# Correctness is gated before any latency profile is collected.
compare_pair() {
  local trial=$1
  "${course_python}" -c \
    'import json,sys; a=json.load(open(sys.argv[1], encoding="utf-8")); b=json.load(open(sys.argv[2], encoding="utf-8")); raise SystemExit(0 if a["measurements"]["response_digests"] == b["measurements"]["response_digests"] else "prefix-cache policies changed greedy outputs")' \
    "${COURSE_RESULTS_DIR}/20-prefix-cache-disabled-trial-${trial}-digests-run-${COURSE_RUN_ID}.json" \
    "${COURSE_RESULTS_DIR}/20-prefix-cache-enabled-trial-${trial}-digests-run-${COURSE_RUN_ID}.json"
}

for trial in 1 2 3; do
  if ((trial % 2 == 1)); then
    run_trial disabled "${trial}" probe
    run_trial enabled "${trial}" probe
  else
    run_trial enabled "${trial}" probe
    run_trial disabled "${trial}" probe
  fi
  compare_pair "${trial}"
done
for trial in 1 2 3; do
  if ((trial % 2 == 1)); then
    run_trial disabled "${trial}" profile
    run_trial enabled "${trial}" profile
  else
    run_trial enabled "${trial}" profile
    run_trial disabled "${trial}" profile
  fi
done

# Successful exit alone does not prove that a profiler collected a report.
report_count=0
while IFS= read -r -d '' report; do
  [[ -s $report ]] || { printf 'ERROR: empty profiler report: %s\n' "$report" >&2; exit 2; }
  report_count=$((report_count + 1))
done < <(find "$COURSE_JOB_DIR" -type f -name '*.nsys-rep' -print0)
((report_count == 12)) || { printf 'ERROR: native profiler report count differs from the required producer count.\n' >&2; exit 2; }
printf 'Diagnostic reports: %s; use a separate normal job for timing.\n' "$COURSE_JOB_DIR"
