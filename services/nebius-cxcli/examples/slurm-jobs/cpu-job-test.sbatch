#!/usr/bin/env bash
set -euo pipefail

RUN_SECONDS="${RUN_SECONDS:-1800}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-30}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

on_signal() {
  local signal_name="$1"
  printf '[%s] received %s, exiting non-zero so interruption is visible\n' "$(timestamp)" "$signal_name" >&2
  exit 143
}

validate_positive_int() {
  local name="$1"
  local value="$2"

  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s must be a positive integer, got: %s\n' "$name" "$value" >&2
    exit 2
  fi
}

trap 'on_signal TERM' TERM
trap 'on_signal INT' INT

validate_positive_int RUN_SECONDS "$RUN_SECONDS"
validate_positive_int HEARTBEAT_SECONDS "$HEARTBEAT_SECONDS"

end_at=$((SECONDS + RUN_SECONDS))

printf '[%s] CPU smoke job started\n' "$(timestamp)"
printf 'job_id=%s array_task_id=%s partition=%s node_list=%s\n' \
  "${SLURM_JOB_ID:-unknown}" \
  "${SLURM_ARRAY_TASK_ID:-none}" \
  "${SLURM_JOB_PARTITION:-unknown}" \
  "${SLURM_JOB_NODELIST:-unknown}"
printf 'hostname=%s run_seconds=%s heartbeat_seconds=%s\n' "$(hostname)" "$RUN_SECONDS" "$HEARTBEAT_SECONDS"

while ((SECONDS < end_at)); do
  remaining=$((end_at - SECONDS))
  printf '[%s] CPU smoke heartbeat: elapsed=%ss remaining=%ss\n' "$(timestamp)" "$SECONDS" "$remaining"

  sleep_for="$HEARTBEAT_SECONDS"
  if ((remaining < sleep_for)); then
    sleep_for="$remaining"
  fi

  if ((sleep_for > 0)); then
    sleep "$sleep_for"
  fi
done

printf '[%s] CPU smoke job completed\n' "$(timestamp)"
