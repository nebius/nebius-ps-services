#!/usr/bin/env bash
set -euo pipefail

umask 077

active_tmp_file=""
refresh_lock_dir=""
refresh_lock_inode=""
refresh_lock_owner_pid=""
refresh_lock_process_started_at=""
mcp_pid=""
mcp_process_started_at=""
refresher_pid=""
refresher_process_started_at=""
worker_timer_pid=""
refresh_worker_stop_status=""
refresh_worker_stopped_mcp="false"
refresh_reason_file=""

usage() {
  cat <<'USAGE'
Run Grafana MCP for Nebius-managed Grafana with a pinned human Nebius profile.

The profile and identity binding must be created by ensure-local-config.sh.
Every token mint revalidates that the pinned profile still resolves to the
expected human user. Ambient agent credentials and competing Grafana
credentials are ignored.

Required environment:
  NEBIUS_GRAFANA_USER_PROFILE
      Pinned human Nebius CLI profile.
  NEBIUS_GRAFANA_IDENTITY_FILE
      Private mode-0600 profile/user binding created by the setup helper.
  GRAFANA_TOKEN_FILE
      Private profile-scoped token file in the same mode-0700 state directory.

Optional environment:
  GRAFANA_URL
      Defaults to https://grafana.nebius.dev/.
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS
      Token-file refresh interval. Defaults to 36000 seconds and must not
      exceed 36000.
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS
      Three retry delays. Defaults to "60 300 900".
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS
      Maximum background wait for another live refresh. Defaults to 90
      seconds.
  NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS
      Maximum foreground startup wait for another live refresh. Defaults to
      210 seconds and must remain within the Codex startup timeout.
Usage:
  run-nebius-grafana-mcp.sh [canonical mcp-grafana args]
  run-nebius-grafana-mcp.sh --refresh-token-only
  run-nebius-grafana-mcp.sh --print-config
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'error: required command not found on PATH: %s\n' "$1" >&2
    exit 127
  fi
}

cleanup_refresh_artifacts() {
  if [ -n "$active_tmp_file" ]; then
    rm -f -- "$active_tmp_file"
    active_tmp_file=""
  fi
  if [ -n "$refresh_lock_dir" ]; then
    if [ "$(file_inode "$refresh_lock_dir" 2>/dev/null || true)" = "$refresh_lock_inode" ] \
      && [ "$(sed -n 's/^pid=//p' "${refresh_lock_dir}/owner" 2>/dev/null | head -n 1 || true)" = "$refresh_lock_owner_pid" ] \
      && [ "$(sed -n 's/^process_started_at=//p' "${refresh_lock_dir}/owner" 2>/dev/null | head -n 1 || true)" = "$refresh_lock_process_started_at" ]; then
      rm -f -- "${refresh_lock_dir}/owner"
      rm -f -- "${refresh_lock_dir}/owner-pid.tmp"
      rmdir -- "$refresh_lock_dir" 2>/dev/null || true
    fi
    refresh_lock_dir=""
    refresh_lock_inode=""
    refresh_lock_owner_pid=""
    refresh_lock_process_started_at=""
  fi
}

write_refresh_exit_reason() {
  local reason="$1"

  case "$reason" in
    rotation-required | refresh-exhausted | refresh-worker-failed) ;;
    *) return 1 ;;
  esac
  [ -n "$refresh_reason_file" ] || return 1
  printf '%s\n' "$reason" >"$refresh_reason_file"
}

process_state() {
  ps -o stat= -p "$1" 2>/dev/null \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

process_is_live_instance() {
  local pid="$1"
  local expected_started_at="$2"
  local current_started_at=""
  local state=""

  [ -n "$pid" ] && [ -n "$expected_started_at" ] || return 1
  current_started_at="$(process_started_at "$pid" 2>/dev/null || true)"
  [ "$current_started_at" = "$expected_started_at" ] || return 1
  state="$(process_state "$pid" || true)"
  case "$state" in
    '' | Z*) return 1 ;;
  esac
}

process_is_stopped_instance() {
  local pid="$1"
  local expected_started_at="$2"
  local current_started_at=""
  local state=""

  [ -n "$pid" ] && [ -n "$expected_started_at" ] || return 1
  current_started_at="$(process_started_at "$pid" 2>/dev/null || true)"
  [ "$current_started_at" = "$expected_started_at" ] || return 1
  state="$(process_state "$pid" || true)"
  case "$state" in
    T*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_owned_mcp_from_parent() {
  local waited=0

  process_is_live_instance "$mcp_pid" "$mcp_process_started_at" || return 1

  # A successful TERM is ambiguous when the child exits naturally just before
  # delivery: an unreaped zombie can still accept kill(2) without receiving the
  # signal. STOP cannot be handled or ignored, so first observe this exact child
  # in the stopped state. Once acknowledged, it cannot exit naturally before
  # the parent sends KILL. Because this is an unreaped direct child, its PID
  # cannot be reused before wait; after STOP succeeds, every path can safely
  # issue KILL without another fallible process-table lookup.
  kill -STOP "$mcp_pid" >/dev/null 2>&1 || return 1
  while [ "$waited" -lt 50 ]; do
    if process_is_stopped_instance "$mcp_pid" "$mcp_process_started_at"; then
      kill -KILL "$mcp_pid" >/dev/null 2>&1 || {
        kill -CONT "$mcp_pid" >/dev/null 2>&1 || true
        return 1
      }
      return 0
    fi

    if ! process_is_live_instance "$mcp_pid" "$mcp_process_started_at"; then
      kill -KILL "$mcp_pid" >/dev/null 2>&1 || true
      return 1
    fi
    sleep 0.1
    waited=$((waited + 1))
  done

  # A hidden or unsupported stopped-state report must not strand the child.
  # KILL is safe here because the direct child remains unreaped and therefore
  # retains the PID whose identity was checked before STOP.
  kill -KILL "$mcp_pid" >/dev/null 2>&1 || {
    kill -CONT "$mcp_pid" >/dev/null 2>&1 || true
  }
  return 1
}

# shellcheck disable=SC2329  # Invoked by worker EXIT/INT/TERM traps.
cleanup_worker_timer() {
  if [ -n "$worker_timer_pid" ]; then
    kill "$worker_timer_pid" >/dev/null 2>&1 || true
    wait "$worker_timer_pid" >/dev/null 2>&1 || true
    worker_timer_pid=""
  fi
}

# shellcheck disable=SC2329  # Invoked by refresh worker INT/TERM traps.
request_refresh_worker_stop() {
  refresh_worker_stop_status="$1"
  cleanup_worker_timer
}

exit_if_refresh_worker_stop_requested() {
  if [ -n "$refresh_worker_stop_status" ]; then
    exit "$refresh_worker_stop_status"
  fi
}

# shellcheck disable=SC2329  # Invoked by the refresh worker EXIT trap.
cleanup_refresh_worker() {
  local status="$?"

  trap - EXIT INT TERM
  cleanup_worker_timer
  cleanup_refresh_artifacts
  if [ "$status" -ne 0 ] \
    && [ "$status" -ne 130 ] \
    && [ "$status" -ne 143 ] \
    && [ "$refresh_worker_stopped_mcp" != "true" ]; then
    printf 'error: token refresh worker failed unexpectedly; stopping MCP before credential expiry\n' >&2
    write_refresh_exit_reason "refresh-worker-failed" || true
  fi
  return "$status"
}

wait_for_worker_timer() {
  local seconds="$1"
  local status=0

  sleep "$seconds" &
  worker_timer_pid="$!"
  wait "$worker_timer_pid" || status="$?"
  worker_timer_pid=""
  return "$status"
}

# shellcheck disable=SC2329  # Invoked by EXIT/INT/TERM traps.
cleanup() {
  trap - EXIT INT TERM
  # Signal every owned child before waiting for either one. In particular, the
  # MCP child may already be stopped and must be killed before a delayed or
  # stopped refresher can block its own reap path.
  if [ -n "$mcp_pid" ]; then
    kill -KILL "$mcp_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$refresher_pid" ]; then
    # A stopped worker cannot handle TERM until continued. Its timer trap and
    # every foreground Nebius CLI call are already bounded, so cooperative
    # shutdown reaches the EXIT trap that owns exact lock/temp cleanup.
    kill -CONT "$refresher_pid" >/dev/null 2>&1 || true
    kill "$refresher_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$mcp_pid" ]; then
    wait "$mcp_pid" >/dev/null 2>&1 || true
    mcp_pid=""
  fi
  if [ -n "$refresher_pid" ]; then
    wait "$refresher_pid" >/dev/null 2>&1 || true
    refresher_pid=""
  fi
  cleanup_refresh_artifacts
  if [ -n "$refresh_reason_file" ]; then
    rm -f -- "$refresh_reason_file"
    refresh_reason_file=""
  fi
}

trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

file_mode() {
  stat -f '%OLp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

file_uid() {
  stat -f '%u' "$1" 2>/dev/null || stat -c '%u' "$1"
}

file_inode() {
  stat -f '%i' "$1" 2>/dev/null || stat -c '%i' "$1"
}

file_mtime_epoch() {
  stat -f '%m' "$1" 2>/dev/null || stat -c '%Y' "$1"
}

process_started_at() {
  ps -o lstart= -p "$1" 2>/dev/null \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

validate_profile_name() {
  local profile="$1"

  case "$profile" in
    codex-agent-*)
      die "NEBIUS_GRAFANA_USER_PROFILE must be a human profile, not an agent profile"
      ;;
  esac
  if [[ ! "$profile" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]]; then
    die "NEBIUS_GRAFANA_USER_PROFILE contains unsupported characters"
  fi
}

validate_positive_integer() {
  local name="$1"
  local value="$2"
  local maximum="$3"

  case "$value" in
    '' | *[!0-9]*)
      die "$name must be a positive integer no greater than $maximum"
      ;;
  esac
  if [ "$value" -le 0 ] || [ "$value" -gt "$maximum" ]; then
    die "$name must be a positive integer no greater than $maximum"
  fi
}

validate_retry_delays() {
  local count=0
  local delay
  local total=0

  for delay in $NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS; do
    case "$delay" in
      '' | *[!0-9]*)
        die "NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS must contain three non-negative integers"
        ;;
    esac
    if [ "$delay" -gt 3600 ]; then
      die "each token refresh retry delay must be no greater than 3600 seconds"
    fi
    total=$((total + 10#$delay))
    count=$((count + 1))
  done
  if [ "$count" -ne 3 ]; then
    die "NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS must contain exactly three delays"
  fi
  if [ "$total" -gt 3600 ]; then
    die "token refresh retry delays must total no more than 3600 seconds"
  fi
}

validate_nebius_grafana_url() {
  case "$GRAFANA_URL" in
    https://grafana.nebius.dev | https://grafana.nebius.dev/)
      return
      ;;
  esac

  die "this wrapper only supports GRAFANA_URL=https://grafana.nebius.dev/; use a generic Grafana service-account-token setup for external Grafana"
}

validate_normalized_absolute_path() {
  local label="$1"
  local path="$2"
  local normalized

  case "$path" in
    /*) ;;
    *) die "$label must be an absolute path" ;;
  esac
  if [[ "$path" =~ [[:cntrl:]] ]]; then
    die "$label contains control characters"
  fi
  normalized="$(
    python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$path"
  )"
  [ "$path" = "$normalized" ] \
    || die "$label must be lexically normalized without dot segments"
}

validate_private_state_dir() {
  local state_dir="$1"

  python3 - "$state_dir" "${HOME:?HOME is required}" <<'PY'
import os
import stat
import sys

state_dir = os.path.abspath(sys.argv[1])
home = os.path.abspath(sys.argv[2])
try:
    if os.path.commonpath((state_dir, home)) != home or state_dir == home:
        raise ValueError
except ValueError:
    raise SystemExit(2)

relative = os.path.relpath(state_dir, home)
current = home
for part in relative.split(os.sep):
    current = os.path.join(current, part)
    try:
        info = os.lstat(current)
    except OSError:
        raise SystemExit(6)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit(3)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise SystemExit(4)

try:
    leaf_mode = stat.S_IMODE(os.lstat(state_dir).st_mode)
except OSError:
    raise SystemExit(6)
if leaf_mode != 0o700:
    raise SystemExit(5)
PY
}

validate_private_file() {
  local path="$1"

  [ -L "$path" ] && die "private state file must not be a symlink: $path"
  [ -f "$path" ] || die "private state file is missing or not regular: $path"
  [ "$(file_uid "$path")" = "$(id -u)" ] \
    || die "private state file is not owned by the current user: $path"
  [ "$(file_mode "$path")" = "600" ] \
    || die "private state file must have mode 0600: $path"
}

validate_state_paths() {
  local identity_dir
  local token_dir

  validate_normalized_absolute_path \
    "NEBIUS_GRAFANA_IDENTITY_FILE" \
    "$NEBIUS_GRAFANA_IDENTITY_FILE"
  validate_normalized_absolute_path "GRAFANA_TOKEN_FILE" "$GRAFANA_TOKEN_FILE"

  identity_dir="$(dirname -- "$NEBIUS_GRAFANA_IDENTITY_FILE")"
  token_dir="$(dirname -- "$GRAFANA_TOKEN_FILE")"
  [ "$identity_dir" = "$token_dir" ] \
    || die "identity and token files must share one profile-scoped state directory"
  [ "$(basename -- "$NEBIUS_GRAFANA_IDENTITY_FILE")" = "identity" ] \
    || die "NEBIUS_GRAFANA_IDENTITY_FILE must use the canonical identity filename"
  [ "$(basename -- "$GRAFANA_TOKEN_FILE")" = "token" ] \
    || die "GRAFANA_TOKEN_FILE must use the canonical token filename"
  [ "$(basename -- "$token_dir")" = "$NEBIUS_GRAFANA_USER_PROFILE" ] \
    || die "profile-scoped state directory must end with the pinned profile name"

  validate_private_state_dir "$token_dir" \
    || die "profile-scoped state directory is unsafe; require an owned non-symlink mode-0700 path below HOME"
  validate_private_file "$NEBIUS_GRAFANA_IDENTITY_FILE"
  if [ -e "$GRAFANA_TOKEN_FILE" ] || [ -L "$GRAFANA_TOKEN_FILE" ]; then
    validate_private_file "$GRAFANA_TOKEN_FILE"
  fi
}

read_identity_binding() {
  local line_count

  line_count="$(awk 'END { print NR }' "$NEBIUS_GRAFANA_IDENTITY_FILE")"
  [ "$line_count" = "3" ] || die "identity binding has an unsupported format"

  binding_version="$(sed -n 's/^version=//p' "$NEBIUS_GRAFANA_IDENTITY_FILE")"
  binding_profile="$(sed -n 's/^profile=//p' "$NEBIUS_GRAFANA_IDENTITY_FILE")"
  binding_user_id="$(sed -n 's/^user_id=//p' "$NEBIUS_GRAFANA_IDENTITY_FILE")"

  [ "$binding_version" = "1" ] || die "identity binding version is unsupported"
  [ "$binding_profile" = "$NEBIUS_GRAFANA_USER_PROFILE" ] \
    || die "identity binding profile does not match NEBIUS_GRAFANA_USER_PROFILE"
  if [[ ! "$binding_user_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$ ]]; then
    die "identity binding contains an invalid user ID"
  fi
}

run_nebius_clean() {
  env \
    -u CODEX_NEBIUS_PROJECT_ID \
    -u CODEX_NEBIUS_TOKEN_HELPER \
    -u NEBIUS_AUTH_CREDENTIALS_FILE \
    -u NEBIUS_IAM_TOKEN \
    -u NEBIUS_IMPERSONATE_SERVICE_ACCOUNT_ID \
    -u NEBIUS_PROFILE \
    -u NEBIUS_PROJECT_ID \
    -u TOKEN \
    nebius "$@"
}

sanitize_browser_auth_stderr() {
  sed -E \
    's#https://auth[.]nebius[.]com/[^[:space:]]+#<browser-auth-url-redacted>#g'
}

run_nebius_browser_auth() {
  run_nebius_clean "$@" \
    2> >(sanitize_browser_auth_stderr >&2)
}

resolve_profile_user_id() {
  local auth_mode="${1:-noninteractive}"
  local -a auth_args=(
    --no-browser
    --auth-timeout 10s
    --timeout 20s
    --retries 1
  )
  local identity_json
  local user_id

  case "$auth_mode" in
    interactive)
      auth_args=(
        --auth-timeout 120s
        --timeout 20s
        --retries 1
      )
      ;;
    noninteractive) ;;
    *)
      die "unsupported Nebius authentication mode"
      ;;
  esac

  if [ "$auth_mode" = "interactive" ]; then
    if ! identity_json="$(
      run_nebius_browser_auth iam whoami \
        --profile "$NEBIUS_GRAFANA_USER_PROFILE" \
        --format json \
        "${auth_args[@]}" </dev/null
    )"; then
      printf 'error: pinned Nebius profile could not be validated after browser authentication\n' >&2
      return 1
    fi
  elif ! identity_json="$(
    run_nebius_clean iam whoami \
      --profile "$NEBIUS_GRAFANA_USER_PROFILE" \
      --format json \
      "${auth_args[@]}" </dev/null 2>/dev/null
  )"; then
    printf 'error: pinned Nebius profile could not be validated non-interactively\n' >&2
    return 1
  fi

  if ! user_id="$(
    printf '%s' "$identity_json" \
      | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(2)

user = payload.get("user_profile")
if not isinstance(user, dict):
    raise SystemExit(3)
if "service_account_profile" in payload or "anonymous_profile" in payload:
    raise SystemExit(4)
user_id = user.get("id")
if not isinstance(user_id, str) or not user_id:
    raise SystemExit(5)
print(user_id)
'
  )"; then
    printf 'error: pinned Nebius profile is not a validated human user profile\n' >&2
    return 1
  fi

  printf '%s\n' "$user_id"
}

validate_identity_binding() {
  local auth_mode="${1:-noninteractive}"
  local actual_user_id

  actual_user_id="$(resolve_profile_user_id "$auth_mode")" || return 1
  if [ "$actual_user_id" != "$binding_user_id" ]; then
    printf 'error: pinned Nebius profile identity changed; create a distinct human profile and rerun setup\n' >&2
    return 1
  fi
}

validate_token_contents() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

value = Path(sys.argv[1]).read_bytes()
lines = value.splitlines()
if len(lines) != 1 or not lines[0].strip() or b"\x00" in lines[0]:
    raise SystemExit(1)
PY
}

token_file_is_fresh() {
  [ -f "$GRAFANA_TOKEN_FILE" ] \
    && [ ! -L "$GRAFANA_TOKEN_FILE" ] \
    && [ "$(file_uid "$GRAFANA_TOKEN_FILE")" = "$(id -u)" ] \
    && [ "$(file_mode "$GRAFANA_TOKEN_FILE")" = "600" ] \
    || return 1
  validate_token_contents "$GRAFANA_TOKEN_FILE" || return 1
  python3 - "$GRAFANA_TOKEN_FILE" <<'PY'
from pathlib import Path
import sys
import time

age = time.time() - Path(sys.argv[1]).stat().st_mtime
if age < 0 or age > 3600:
    raise SystemExit(1)
PY
}

refresh_token() {
  local auth_mode="${1:-noninteractive}"
  local -a auth_args=(
    --no-browser
    --auth-timeout 10s
    --timeout 20s
    --retries 1
  )
  local lock_dir="${GRAFANA_TOKEN_FILE}.lock"
  local lock_owner_file="${lock_dir}/owner"
  local lock_inode=""
  local lock_uid=""
  local lock_mode=""
  local lock_mtime=""
  local lock_age=0
  local owner_pid_file=""
  local owner_file_inode=""
  local owner_file_uid=""
  local owner_file_mode=""
  local owner_version=""
  local owner_pid=""
  local owner_started_at=""
  local owner_process_started_at=""
  local current_process_started_at=""
  local metadata_valid="false"
  local now_epoch=""
  local token_inode_before=""
  local token_inode_after=""
  local waited=0
  local lock_wait_seconds="$NEBIUS_GRAFANA_LOCK_WAIT_SECONDS"

  case "$auth_mode" in
    interactive)
      lock_wait_seconds="$NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS"
      ;;
    noninteractive) ;;
    *)
      die "unsupported Nebius authentication mode"
      ;;
  esac

  exit_if_refresh_worker_stop_requested
  if [ -f "$GRAFANA_TOKEN_FILE" ] && [ ! -L "$GRAFANA_TOKEN_FILE" ]; then
    token_inode_before="$(file_inode "$GRAFANA_TOKEN_FILE")"
  fi

  while :; do
    exit_if_refresh_worker_stop_requested
    if mkdir -- "$lock_dir" 2>/dev/null; then
      lock_inode="$(file_inode "$lock_dir")"
      refresh_lock_dir="$lock_dir"
      refresh_lock_inode="$lock_inode"
      exit_if_refresh_worker_stop_requested
      break
    fi
    if [ -L "$lock_dir" ]; then
      [ -L "$lock_dir" ] || continue
      printf 'error: profile-scoped token refresh lock path is unsafe\n' >&2
      return 1
    fi
    lock_inode="$(file_inode "$lock_dir" 2>/dev/null || true)"
    [ -n "$lock_inode" ] || continue
    lock_uid="$(file_uid "$lock_dir" 2>/dev/null || true)"
    lock_mode="$(file_mode "$lock_dir" 2>/dev/null || true)"
    if [ ! -d "$lock_dir" ] \
      || [ "$lock_uid" != "$(id -u)" ] \
      || [ "$lock_mode" != "700" ]; then
      [ "$(file_inode "$lock_dir" 2>/dev/null || true)" = "$lock_inode" ] \
        || continue
      printf 'error: profile-scoped token refresh lock path is unsafe\n' >&2
      return 1
    fi
    [ "$(file_inode "$lock_dir" 2>/dev/null || true)" = "$lock_inode" ] \
      || continue

    if [ -e "$lock_owner_file" ] || [ -L "$lock_owner_file" ]; then
      if [ -L "$lock_owner_file" ]; then
        [ -L "$lock_owner_file" ] || continue
        printf 'error: profile-scoped token refresh lock metadata is unsafe\n' >&2
        return 1
      fi
      owner_file_inode="$(file_inode "$lock_owner_file" 2>/dev/null || true)"
      [ -n "$owner_file_inode" ] || continue
      owner_file_uid="$(file_uid "$lock_owner_file" 2>/dev/null || true)"
      owner_file_mode="$(file_mode "$lock_owner_file" 2>/dev/null || true)"
      if [ ! -f "$lock_owner_file" ] \
        || [ "$owner_file_uid" != "$(id -u)" ] \
        || [ "$owner_file_mode" != "600" ]; then
        [ "$(file_inode "$lock_owner_file" 2>/dev/null || true)" = "$owner_file_inode" ] \
          || continue
        printf 'error: profile-scoped token refresh lock metadata is unsafe\n' >&2
        return 1
      fi
      [ "$(file_inode "$lock_owner_file" 2>/dev/null || true)" = "$owner_file_inode" ] \
        || continue
    fi
    owner_file_inode=""
    owner_version=""
    owner_pid="$(
      sed -n 's/^pid=//p' "$lock_owner_file" 2>/dev/null \
        | head -n 1 \
        || true
    )"
    owner_started_at="$(
      sed -n 's/^started_at_epoch=//p' "$lock_owner_file" 2>/dev/null \
        | head -n 1 \
        || true
    )"
    owner_process_started_at="$(
      sed -n 's/^process_started_at=//p' "$lock_owner_file" 2>/dev/null \
        | head -n 1 \
        || true
    )"
    owner_version="$(
      sed -n 's/^version=//p' "$lock_owner_file" 2>/dev/null \
        | head -n 1 \
        || true
    )"
    if [ -f "$lock_owner_file" ]; then
      owner_file_inode="$(file_inode "$lock_owner_file" 2>/dev/null || true)"
    fi
    metadata_valid="false"
    if [ "$owner_version" = "1" ] \
      && [[ "$owner_pid" =~ ^[0-9]+$ ]] \
      && [[ "$owner_started_at" =~ ^[0-9]+$ ]] \
      && [ -n "$owner_process_started_at" ] \
      && [ "$(wc -l <"$lock_owner_file" | tr -d '[:space:]')" = "4" ] \
      && [ "$(grep -Ec '^(version|pid|started_at_epoch|process_started_at)=' "$lock_owner_file")" = "4" ]; then
      metadata_valid="true"
    fi

    if [ "$metadata_valid" = "true" ]; then
      current_process_started_at=""
      if kill -0 "$owner_pid" 2>/dev/null; then
        current_process_started_at="$(process_started_at "$owner_pid" || true)"
      fi
      if ! kill -0 "$owner_pid" 2>/dev/null \
        || { [ -n "$current_process_started_at" ] \
          && [ "$current_process_started_at" != "$owner_process_started_at" ]; }; then
        if [ "$(file_inode "$lock_dir" 2>/dev/null || true)" = "$lock_inode" ] \
          && [ "$(file_inode "$lock_owner_file" 2>/dev/null || true)" = "$owner_file_inode" ]; then
          rm -f -- "$lock_owner_file"
          if rmdir -- "$lock_dir" 2>/dev/null; then
            printf 'warning: recovered a stale profile-scoped token refresh lock\n' >&2
            continue
          fi
        fi
      fi
    else
      now_epoch="$(date +%s)"
      lock_mtime="$(file_mtime_epoch "$lock_dir" 2>/dev/null || true)"
      case "$lock_mtime" in
        '' | *[!0-9]*)
          lock_age=0
          ;;
        *)
          lock_age=$((now_epoch - lock_mtime))
          [ "$lock_age" -ge 0 ] || lock_age=0
          ;;
      esac
      if [ "$lock_age" -ge "$lock_wait_seconds" ] \
        && [ "$(file_inode "$lock_dir" 2>/dev/null || true)" = "$lock_inode" ]; then
        if [ -n "$owner_file_inode" ]; then
          if [ "$(file_inode "$lock_owner_file" 2>/dev/null || true)" = "$owner_file_inode" ]; then
            rm -f -- "$lock_owner_file"
          fi
        elif [ -e "$lock_owner_file" ] || [ -L "$lock_owner_file" ]; then
          sleep 1
          waited=$((waited + 1))
          continue
        fi
        if rmdir -- "$lock_dir" 2>/dev/null; then
          printf 'warning: recovered an incomplete profile-scoped token refresh lock\n' >&2
          continue
        fi
      fi
    fi
    if [ "$waited" -ge "$lock_wait_seconds" ]; then
      printf 'error: timed out waiting for the profile-scoped token refresh lock\n' >&2
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if [ "$waited" -gt 0 ] \
    && [ -f "$GRAFANA_TOKEN_FILE" ] \
    && [ ! -L "$GRAFANA_TOKEN_FILE" ]; then
    token_inode_after="$(file_inode "$GRAFANA_TOKEN_FILE")"
    if [ -n "$token_inode_after" ] \
      && [ "$token_inode_after" != "$token_inode_before" ]; then
      validate_private_file "$GRAFANA_TOKEN_FILE"
      validate_token_contents "$GRAFANA_TOKEN_FILE" \
        || die "concurrent refresh produced an invalid access token payload"
      if [ "$(file_inode "$lock_dir" 2>/dev/null || true)" = "$lock_inode" ]; then
        cleanup_refresh_artifacts
        if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
          printf 'warning: reused the fresh profile-scoped token produced by the prior refresh owner\n' >&2
          return 0
        fi
      fi
      die "token refresh lock changed while reusing a concurrent refresh"
    fi
  fi
  if [ "$waited" -gt 0 ] && [ "$auth_mode" = "interactive" ]; then
    cleanup_refresh_artifacts
    printf 'error: prior foreground token refresh ended without producing a fresh token\n' >&2
    return 1
  fi
  owner_pid_file="${lock_dir}/owner-pid.tmp"
  sh -c 'printf "%s\n" "$PPID"' >"$owner_pid_file" \
    || die "unable to identify token refresh lock owner process"
  chmod 600 "$owner_pid_file" \
    || die "unable to protect temporary token refresh lock metadata"
  IFS= read -r owner_pid <"$owner_pid_file" || [ -n "$owner_pid" ]
  rm -f -- "$owner_pid_file"
  [[ "$owner_pid" =~ ^[0-9]+$ ]] \
    || die "token refresh lock owner PID is invalid"
  owner_process_started_at="$(process_started_at "$owner_pid")"
  [ -n "$owner_process_started_at" ] \
    || die "unable to identify token refresh lock owner process"
  refresh_lock_owner_pid="$owner_pid"
  refresh_lock_process_started_at="$owner_process_started_at"
  {
    printf 'version=1\n'
    printf 'pid=%s\n' "$owner_pid"
    printf 'started_at_epoch=%s\n' "$(date +%s)"
    printf 'process_started_at=%s\n' "$owner_process_started_at"
  } >"$lock_owner_file"
  chmod 600 "$lock_owner_file" \
    || die "unable to protect token refresh lock metadata"
  exit_if_refresh_worker_stop_requested

  active_tmp_file="$(mktemp "${GRAFANA_TOKEN_FILE}.tmp.XXXXXX")"
  if ! chmod 600 "$active_tmp_file"; then
    printf 'error: unable to enforce mode 0600 on the temporary token file\n' >&2
    cleanup_refresh_artifacts
    return 1
  fi
  exit_if_refresh_worker_stop_requested

  if ! validate_identity_binding "$auth_mode"; then
    cleanup_refresh_artifacts
    return 1
  fi
  exit_if_refresh_worker_stop_requested
  if ! run_nebius_clean iam get-access-token \
    --profile "$NEBIUS_GRAFANA_USER_PROFILE" \
    "${auth_args[@]}" </dev/null >"$active_tmp_file"; then
    printf 'error: Nebius CLI could not mint a token for the pinned human profile\n' >&2
    cleanup_refresh_artifacts
    return 1
  fi
  exit_if_refresh_worker_stop_requested
  if ! validate_identity_binding noninteractive; then
    cleanup_refresh_artifacts
    return 1
  fi
  exit_if_refresh_worker_stop_requested
  if ! validate_token_contents "$active_tmp_file"; then
    printf 'error: Nebius CLI returned an invalid access token payload\n' >&2
    cleanup_refresh_artifacts
    return 1
  fi

  if ! mv -f -- "$active_tmp_file" "$GRAFANA_TOKEN_FILE"; then
    printf 'error: unable to atomically replace the profile-scoped token file\n' >&2
    cleanup_refresh_artifacts
    return 1
  fi
  active_tmp_file=""
  if ! chmod 600 "$GRAFANA_TOKEN_FILE"; then
    printf 'error: unable to enforce mode 0600 on the token file\n' >&2
    cleanup_refresh_artifacts
    return 1
  fi
  if ! validate_private_file "$GRAFANA_TOKEN_FILE"; then
    cleanup_refresh_artifacts
    return 1
  fi

  cleanup_refresh_artifacts
}

mcp_supports_token_file() {
  local mcp_path

  mcp_path="$(command -v mcp-grafana)"
  if ! command -v strings >/dev/null 2>&1; then
    return 1
  fi

  strings "$mcp_path" 2>/dev/null \
    | grep -F "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" >/dev/null
}

clear_competing_grafana_auth() {
  unset \
    GRAFANA_API_KEY \
    GRAFANA_BASIC_AUTH \
    GRAFANA_EXTRA_HEADERS \
    GRAFANA_FORWARD_HEADERS \
    GRAFANA_HTTP_HEADERS \
    GRAFANA_ORG_ID \
    GRAFANA_PASSWORD \
    GRAFANA_SERVICE_ACCOUNT_TOKEN \
    GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE \
    GRAFANA_TOKEN \
    GRAFANA_USERNAME || true
}

configure_token_environment() {
  clear_competing_grafana_auth
  export GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE="$GRAFANA_TOKEN_FILE"
}

build_mcp_args() {
  MCP_ARGS=(--disable-write --max-loki-log-limit 20)
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --disable-write)
        shift
        ;;
      --max-loki-log-limit)
        [ "$#" -ge 2 ] \
          || die "--max-loki-log-limit requires the canonical value 20"
        [ "$2" = "20" ] \
          || die "caller argument would weaken the canonical Loki result limit"
        shift 2
        ;;
      --max-loki-log-limit=20)
        shift
        ;;
      --disable-write=* | --max-loki-log-limit=* | --disable-proxied)
        die "caller argument would weaken the canonical read-only Grafana MCP policy: $1"
        ;;
      *)
        die "unsupported mcp-grafana argument; this wrapper accepts only the canonical read-only flags: $1"
        ;;
    esac
  done
}

refresh_with_retries() {
  local delay

  if refresh_token noninteractive; then
    return 0
  fi
  for delay in $NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS; do
    printf 'warning: Nebius Grafana token refresh failed; retrying after %s seconds\n' "$delay" >&2
    wait_for_worker_timer "$delay"
    exit_if_refresh_worker_stop_requested
    if refresh_token noninteractive; then
      return 0
    fi
  done
  return 1
}

run_refresh_loop() {
  trap cleanup_refresh_worker EXIT
  trap 'request_refresh_worker_stop 130' INT
  trap 'request_refresh_worker_stop 143' TERM

  wait_for_worker_timer "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS"
  exit_if_refresh_worker_stop_requested
  if ! refresh_with_retries; then
    printf 'error: Nebius Grafana token refresh exhausted; stopping MCP before credential expiry\n' >&2
    refresh_worker_stopped_mcp="true"
    write_refresh_exit_reason "refresh-exhausted" || return 1
    return 1
  fi

  # mcp-grafana stdio builds through v0.17.2 keep the startup Grafana client
  # for the life of the process and do not re-read a rotated token file.
  # Close the transport after rotation so the next process loads the fresh
  # credential instead of silently retaining the expiring in-memory token.
  printf 'warning: refreshed the Nebius Grafana token; requesting bounded stdio shutdown because a new chat or Codex restart is required to load it\n' >&2
  refresh_worker_stopped_mcp="true"
  write_refresh_exit_reason "rotation-required" || return 1
  return 75
}

: "${GRAFANA_URL:=https://grafana.nebius.dev/}"
: "${NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS:=36000}"
: "${NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS:=60 300 900}"
: "${NEBIUS_GRAFANA_LOCK_WAIT_SECONDS:=90}"
: "${NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS:=210}"

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

: "${NEBIUS_GRAFANA_USER_PROFILE:?NEBIUS_GRAFANA_USER_PROFILE is required}"
: "${NEBIUS_GRAFANA_IDENTITY_FILE:?NEBIUS_GRAFANA_IDENTITY_FILE is required}"
: "${GRAFANA_TOKEN_FILE:?GRAFANA_TOKEN_FILE is required}"

require_command python3
require_command ps
validate_profile_name "$NEBIUS_GRAFANA_USER_PROFILE"
validate_positive_integer \
  "NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS" \
  "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS" \
  36000
validate_positive_integer \
  "NEBIUS_GRAFANA_LOCK_WAIT_SECONDS" \
  "$NEBIUS_GRAFANA_LOCK_WAIT_SECONDS" \
  120
validate_positive_integer \
  "NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS" \
  "$NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS" \
  240
validate_retry_delays
validate_nebius_grafana_url
validate_state_paths
read_identity_binding

case "${1:-}" in
  --print-config)
    printf 'GRAFANA_URL=%s\n' "$GRAFANA_URL"
    printf 'GRAFANA_TOKEN_FILE=%s\n' "$GRAFANA_TOKEN_FILE"
    printf 'NEBIUS_GRAFANA_IDENTITY_FILE=%s\n' "$NEBIUS_GRAFANA_IDENTITY_FILE"
    printf 'NEBIUS_GRAFANA_USER_PROFILE=%s\n' "$NEBIUS_GRAFANA_USER_PROFILE"
    if command -v mcp-grafana >/dev/null 2>&1 \
      && command -v strings >/dev/null 2>&1 \
      && mcp_supports_token_file; then
      printf 'MCP_GRAFANA_TOKEN_MODE=token-file\n'
    else
      printf 'MCP_GRAFANA_TOKEN_MODE=unsupported\n'
    fi
    printf 'MCP_GRAFANA_READ_ONLY=true\n'
    printf 'NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=%s\n' "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS"
    printf 'NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=%s\n' "$NEBIUS_GRAFANA_LOCK_WAIT_SECONDS"
    printf 'NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=%s\n' "$NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS"
    exit 0
    ;;
esac

require_command nebius

if [ "${1:-}" = "--refresh-token-only" ]; then
  refresh_token interactive
  exit 0
fi

require_command mcp-grafana
require_command strings
mcp_supports_token_file \
  || die "installed mcp-grafana lacks required token-file authentication support; update it before using Nebius-managed Grafana"
if ! token_file_is_fresh; then
  printf 'warning: cached pinned-human token is missing or older than one hour; renewing it before MCP startup\n' >&2
  refresh_token interactive \
    || die "unable to renew the pinned-human token before MCP startup"
  token_file_is_fresh \
    || die "renewed pinned-human token failed the startup freshness check"
fi

export GRAFANA_URL
configure_token_environment
build_mcp_args "$@"
refresh_reason_file="$(
  mktemp "${GRAFANA_TOKEN_FILE}.restart.XXXXXX"
)" || die "unable to create private refresh-reason state"
chmod 600 "$refresh_reason_file" \
  || die "unable to protect private refresh-reason state"
validate_private_file "$refresh_reason_file"

# Bash redirects stdin for asynchronous commands to /dev/null when job control
# is unavailable unless the command has an explicit stdin redirection. Preserve
# the wrapper's MCP stdio stream on a private descriptor before backgrounding.
exec 3<&0
mcp-grafana "${MCP_ARGS[@]}" <&3 3<&- &
mcp_pid="$!"
mcp_process_started_at="$(process_started_at "$mcp_pid" 2>/dev/null || true)"
exec 3<&-
if [ -z "$mcp_process_started_at" ]; then
  status=0
  wait "$mcp_pid" || status="$?"
  mcp_pid=""
  exit "$status"
fi

run_refresh_loop &
refresher_pid="$!"
refresher_process_started_at="$(
  process_started_at "$refresher_pid" 2>/dev/null || true
)"

refresh_reason=""
confirmed_refresh_reason=""
while process_is_live_instance "$mcp_pid" "$mcp_process_started_at"; do
  refresh_reason=""
  IFS= read -r refresh_reason <"$refresh_reason_file" || true
  if [ -n "$refresh_reason" ]; then
    if stop_owned_mcp_from_parent; then
      confirmed_refresh_reason="$refresh_reason"
    fi
    break
  fi
  if ! process_is_live_instance \
    "$refresher_pid" "$refresher_process_started_at"; then
    IFS= read -r refresh_reason <"$refresh_reason_file" || true
    [ -n "$refresh_reason" ] || refresh_reason="refresh-worker-failed"
    if stop_owned_mcp_from_parent; then
      confirmed_refresh_reason="$refresh_reason"
    fi
    break
  fi
  sleep 1
done

status=0
wait "$mcp_pid" || status="$?"
mcp_pid=""
case "$confirmed_refresh_reason" in
  '') exit "$status" ;;
esac

refresh_reason="$confirmed_refresh_reason"
case "$refresh_reason" in
  rotation-required)
    refresh_status=0
    wait "$refresher_pid" || refresh_status="$?"
    refresher_pid=""
    if [ "$refresh_status" -ne 75 ]; then
      printf 'error: token rotation stopped MCP without the expected refresh-worker status\n' >&2
      exit 1
    fi
    printf 'error: Nebius Grafana credential rotated; start a new chat or restart Codex before using grafana-nebius again\n' >&2
    exit 75
    ;;
  refresh-exhausted | refresh-worker-failed)
    wait "$refresher_pid" >/dev/null 2>&1 || true
    refresher_pid=""
    exit 1
    ;;
  '')
    ;;
  *)
    printf 'error: invalid refresh exit reason; refusing to report MCP success\n' >&2
    exit 1
    ;;
esac
exit "$status"
