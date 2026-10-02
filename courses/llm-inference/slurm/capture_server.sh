#!/usr/bin/env bash
# Run with srun: the native profiler/server and client argv are supplied visibly.
set -euo pipefail
set -o noclobber
umask 077

usage() {
  printf 'Usage: srun [resources] bash %s LAB --client COMMAND... --server nsys profile OPTIONS... vllm serve MODEL...\n' "$0"
  printf 'Replaces @URL@, @PORT@ and @OUTPUT@ argv values with this private job endpoint/result.\n'
  printf 'Use --output results/LAB/profiles/nsys-%%q{COURSE_CAPTURE_ID} in the native command.\n'
}
if [[ ${1:-} == -h || ${1:-} == --help ]]; then usage; exit 0; fi
if [[ $# -lt 6 || ! ${SLURM_JOB_ID:-} =~ ^[0-9]+$ ]]; then usage >&2; exit 2; fi
lab=$1; shift
case "$lab" in
  11_serving_client|15_streaming_client|20_prefix_cache_client|33_speculative_engine_client|34_policy_equivalence_client) ;;
  *) printf 'ERROR: unsupported server lab.\n' >&2; exit 2 ;;
esac
[[ ${1:-} == --client ]] || { usage >&2; exit 2; }
shift
client=()
while [[ $# -gt 0 && $1 != --server ]]; do client+=("$1"); shift; done
[[ $# -gt 1 && ${#client[@]} -gt 0 ]] || { usage >&2; exit 2; }
shift
server=("$@")
[[ ${server[0]} == nsys && ${server[1]:-} == profile ]] || { printf 'ERROR: supply native nsys profile argv.\n' >&2; exit 2; }
: "${COURSE_CONTAINER_RUNNER:?select the qualified container runner}"
: "${VLLM_IMAGE_DIGEST:?select the qualified immutable vLLM image}"
[[ $VLLM_IMAGE_DIGEST == *@sha256:* && -x $COURSE_CONTAINER_RUNNER ]] || exit 2
for binary in setsid timeout curl; do command -v "$binary" >/dev/null || exit 2; done
[[ -d results/$lab/logs && -d results/$lab/profiles ]] || { printf 'ERROR: run course_setup.py prepare first.\n' >&2; exit 2; }
export COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys COURSE_RUNTIME_ID=$VLLM_IMAGE_DIGEST
export COURSE_CAPTURE_ID="${SLURM_JOB_ID}-${SLURM_STEP_ID:-0}-$$"
port=$((20000 + SLURM_JOB_ID % 20000))
url="http://127.0.0.1:$port"
output="$PWD/results/$lab/profiles/client-${COURSE_CAPTURE_ID}.json"
report="$PWD/results/$lab/profiles/nsys-${COURSE_CAPTURE_ID}.nsys-rep"
log="$PWD/results/$lab/logs/server-${COURSE_CAPTURE_ID}.log"
metrics="$PWD/results/$lab/profiles/metrics-${COURSE_CAPTURE_ID}.prom"
replace() {
  local i
  for i in "${!client[@]}"; do
    case ${client[i]} in @URL@) client[i]=$url ;; @PORT@) client[i]=$port ;; @OUTPUT@) client[i]=$output ;; esac
  done
  for i in "${!server[@]}"; do
    case ${server[i]} in @URL@) server[i]=$url ;; @PORT@) server[i]=$port ;; @OUTPUT@) server[i]=$output ;; esac
  done
}
replace
for path in "$output" "$report" "$log" "$metrics"; do
  [[ ! -e $path && ! -L $path ]] || { printf 'ERROR: diagnostic identity already exists.\n' >&2; exit 2; }
done
server_pid=''; capture_started=0
control() {
  local code
  code=$(curl --fail --silent --show-error --max-time 90 --output /dev/null \
    --write-out '%{http_code}' --request POST --data '' "$url/${1}_profile")
  [[ $code == 200 ]] || { printf 'ERROR: server did not acknowledge %s_profile.\n' "$1" >&2; return 2; }
}
stop_server() {
  local i status=0 forced=0
  [[ -n $server_pid ]] || return 0
  kill -INT -- "-$server_pid" 2>/dev/null || true
  for ((i=0; i<600; i++)); do
    if ! kill -0 -- "-$server_pid" 2>/dev/null; then break; fi
    sleep 0.1
  done
  if kill -0 -- "-$server_pid" 2>/dev/null; then
    forced=1
    kill -TERM -- "-$server_pid" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$server_pid" 2>/dev/null || true
  fi
  wait "$server_pid" || status=$?
  server_pid=''
  if ((forced)) || [[ $status != 0 && $status != 130 ]]; then
    printf 'ERROR: server/profiler failed or required forced shutdown (status %s).\n' "$status" >&2
    return 2
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((capture_started)); then control stop || true; fi
  stop_server || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# A distinct process group makes interruption and the 15-minute timeout bounded.
setsid timeout --signal=TERM --kill-after=15s 900s \
  "$COURSE_CONTAINER_RUNNER" "$VLLM_IMAGE_DIGEST" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 VLLM_WORKER_MULTIPROC_METHOD=spawn \
  "${server[@]}" >"$log" 2>&1 &
server_pid=$!
ready=0
for ((attempt=0; attempt<180; attempt++)); do
  kill -0 "$server_pid" 2>/dev/null || { printf 'ERROR: server exited; inspect %s.\n' "$log" >&2; exit 2; }
  if curl --fail --silent --show-error --max-time 2 "$url/health" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
((ready)) || { printf 'ERROR: server readiness timed out.\n' >&2; exit 2; }
capture_started=1
control start
# The actual supplied lab client runs unchanged; only its endpoint/output differ.
"${client[@]}"
control stop
capture_started=0
curl --fail --silent --show-error --max-time 10 "$url/metrics" \
  >"$metrics"
stop_server
[[ -s $report ]] || { printf 'ERROR: missing report %s.\n' "$report" >&2; exit 2; }
printf 'Diagnostic report: %s\nClient output: %s\nInspect report contents; rerun the baseline for performance measurements.\n' "$report" "$output"
