#!/usr/bin/env bash
set -euo pipefail

report_test_failure() {
  local status="$?"

  printf 'wrapper test failed at line %s: %s (status %s)\n' \
    "$1" "$2" "$status" >&2
  return "$status"
}
trap 'report_test_failure "$LINENO" "$BASH_COMMAND"' ERR

script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
wrapper="${script_dir}/run-nebius-grafana-mcp.sh"
ensure_config="${script_dir}/ensure-local-config.sh"

temp_root="${TMPDIR:-/tmp}"
temp_root="$(CDPATH='' cd -- "$temp_root" && pwd -P)"
tmp_dir="$(mktemp -d "${temp_root}/grafana-mcp-nebius-test.XXXXXX")"
case "$tmp_dir" in
  "${temp_root}/grafana-mcp-nebius-test."*) ;;
  *)
    printf 'unsafe temporary test path: %s\n' "$tmp_dir" >&2
    exit 1
    ;;
esac

cleanup() {
  if [ -d "$tmp_dir" ]; then
    find "$tmp_dir" -depth -delete
  fi
}
trap cleanup EXIT

test_home="${tmp_dir}/home"
fake_bin="${tmp_dir}/bin"
mkdir -p "$test_home" "$fake_bin"
chmod 700 "$test_home"

cat >"${fake_bin}/nebius" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_NEBIUS_LOG:?FAKE_NEBIUS_LOG is required}"
browser_auth_state="${FAKE_BROWSER_AUTH_STATE:-${FAKE_NEBIUS_LOG}.browser-auth}"

for variable_name in \
  CODEX_NEBIUS_PROJECT_ID \
  CODEX_NEBIUS_TOKEN_HELPER \
  NEBIUS_AUTH_CREDENTIALS_FILE \
  NEBIUS_IAM_TOKEN \
  NEBIUS_IMPERSONATE_SERVICE_ACCOUNT_ID \
  NEBIUS_PROFILE \
  NEBIUS_PROJECT_ID \
  TOKEN; do
  if printenv "$variable_name" >/dev/null 2>&1; then
    printf 'leaked:%s\n' "$variable_name" >>"$FAKE_NEBIUS_LOG"
    exit 90
  fi
done

printf 'args:%s\n' "$*" >>"$FAKE_NEBIUS_LOG"

case " $* " in
  *" profile active "*)
    printf '%s\n' "${FAKE_ACTIVE_PROFILE:-human-primary}"
    exit 0
    ;;
  *" iam whoami "*)
    if [ "${FAKE_AUTH_REQUIRES_BROWSER:-false}" = "true" ]; then
      case " $* " in
        *" --no-browser "*)
          [ -f "$browser_auth_state" ] || exit 7
          ;;
        *)
          printf 'browser login: https://auth.nebius.com/oauth2/authorize?state=fixture-only\n' >&2
          : >"$browser_auth_state"
          ;;
      esac
    fi
    case "${FAKE_IDENTITY_MODE:-user}" in
      user)
        printf '{"user_profile":{"id":"%s"}}\n' \
          "${FAKE_USER_ID:-tenantuseraccount-human-primary}"
        ;;
      service)
        printf '{"service_account_profile":{"info":{"metadata":{"id":"serviceaccount-agent"}}}}\n'
        ;;
      anonymous)
        printf '{"anonymous_profile":{}}\n'
        ;;
      mixed)
        printf '{"user_profile":{"id":"tenantuseraccount-human-primary"},"service_account_profile":{}}\n'
        ;;
      malformed)
        printf '{not-json\n'
        ;;
      *)
        exit 2
        ;;
    esac
    exit 0
    ;;
  *" iam get-access-token "*)
    if [ "${FAKE_AUTH_REQUIRES_BROWSER:-false}" = "true" ]; then
      case " $* " in
        *" --no-browser "*)
          [ -f "$browser_auth_state" ] || exit 7
          ;;
        *)
          printf 'browser login: https://auth.nebius.com/oauth2/authorize?state=fixture-only\n' >&2
          : >"$browser_auth_state"
          ;;
      esac
    fi
    : "${FAKE_TOKEN_COUNTER:?FAKE_TOKEN_COUNTER is required}"
    count=0
    if [ -f "$FAKE_TOKEN_COUNTER" ]; then
      count="$(cat "$FAKE_TOKEN_COUNTER")"
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$FAKE_TOKEN_COUNTER"
    if [ -n "${FAKE_TOKEN_DELAY_AFTER:-}" ] \
      && [ "$count" -gt "$FAKE_TOKEN_DELAY_AFTER" ]; then
      sleep "${FAKE_TOKEN_DELAY_SECONDS:-5}"
    fi
    if [ -n "${FAKE_TOKEN_FAIL_AFTER:-}" ] \
      && [ "$count" -gt "$FAKE_TOKEN_FAIL_AFTER" ]; then
      exit 1
    fi
    printf 'fake-nebius-token-%s\n' "$count"
    exit 0
    ;;
esac

printf 'unexpected nebius args: %s\n' "$*" >&2
exit 2
SH

cat >"${fake_bin}/mcp-grafana" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

: "${MCP_CAPTURE_FILE:?MCP_CAPTURE_FILE is required}"

record_state() {
  {
    printf 'token=%s\n' "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}"
    printf 'token_file=%s\n' "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}"
    printf 'args=%s\n' "$*"
    for variable_name in \
      GRAFANA_API_KEY \
      GRAFANA_BASIC_AUTH \
      GRAFANA_EXTRA_HEADERS \
      GRAFANA_FORWARD_HEADERS \
      GRAFANA_HTTP_HEADERS \
      GRAFANA_ORG_ID \
      GRAFANA_PASSWORD \
      GRAFANA_TOKEN \
      GRAFANA_USERNAME; do
      if printenv "$variable_name" >/dev/null 2>&1; then
        printf 'competing:%s\n' "$variable_name"
      fi
    done
    if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}" ] \
      && [ -f "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" ]; then
      printf 'token_file_initial=%s\n' "$(cat "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE")"
    fi
  } >"$MCP_CAPTURE_FILE"
}

record_state "$@"
if [ -n "${MCP_PID_CAPTURE_FILE:-}" ]; then
  printf '%s\n' "$$" >"$MCP_PID_CAPTURE_FILE"
fi
client_token_at_start=""
if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}" ] \
  && [ -f "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" ]; then
  client_token_at_start="$(cat "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE")"
fi

record_termination() {
  printf 'client_token_at_termination=%s\n' \
    "$client_token_at_start" >>"$MCP_CAPTURE_FILE"
  if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}" ] \
    && [ -f "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" ]; then
    printf 'token_file_at_termination=%s\n' \
      "$(cat "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE")" >>"$MCP_CAPTURE_FILE"
  fi
  printf 'terminated=true\n' >>"$MCP_CAPTURE_FILE"
  exit 0
}
trap record_termination TERM

if [ -n "${MCP_STDIN_CAPTURE_FILE:-}" ]; then
  if ! IFS= read -r stdin_line; then
    printf 'mcp-grafana did not receive the wrapper stdin stream\n' >&2
    exit 91
  fi
  printf '%s\n' "$stdin_line" >"$MCP_STDIN_CAPTURE_FILE"
fi

if [ "${MCP_HOLD_SECONDS:-0}" -gt 0 ]; then
  sleep "$MCP_HOLD_SECONDS"
fi

if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}" ] \
  && [ -f "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" ]; then
  printf 'token_file_final=%s\n' \
    "$(cat "$GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE")" >>"$MCP_CAPTURE_FILE"
fi
printf 'natural_exit=true\n' >>"$MCP_CAPTURE_FILE"
exit "${MCP_EXIT_STATUS:-0}"
SH

cat >"${fake_bin}/strings" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

case "${STRINGS_MODE:-}" in
  token-file)
    printf 'GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE\n'
    ;;
  token-file-embedded)
    printf 'runtime:GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE,other-setting\n'
    ;;
esac
SH

cat >"${fake_bin}/ps" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

status=0
output="$(/bin/ps "$@")" || status="$?"
[ "$status" -eq 0 ] || exit "$status"

if [ "${FAKE_PS_HIDE_STOPPED:-false}" = "true" ] \
  && [ "${1:-}" = "-o" ] \
  && [ "${2:-}" = "stat=" ]; then
  state="$(
    printf '%s\n' "$output" \
      | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
  )"
  case "$state" in
    T*)
      if [ -n "${FAKE_PS_STOPPED_MARKER:-}" ]; then
        : >"$FAKE_PS_STOPPED_MARKER"
      fi
      exit 1
      ;;
  esac
fi

printf '%s\n' "$output"
SH

cat >"${fake_bin}/sleep" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ -n "${FAKE_REFRESHER_PID_CAPTURE_FILE:-}" ] \
  && [ "${1:-}" = "${FAKE_REFRESHER_SLEEP_SECONDS:-}" ]; then
  printf '%s\n' "$PPID" >"$FAKE_REFRESHER_PID_CAPTURE_FILE"
fi
exec /bin/sleep "$@"
SH

cat >"${fake_bin}/codex" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

: "${CODEX_CAPTURE_FILE:?CODEX_CAPTURE_FILE is required}"

if [ "${1:-}" = "mcp" ] && [ "${2:-}" = "get" ]; then
  mkdir -p -- "${CODEX_HOME:?CODEX_HOME is required}"
  mode_file="${CODEX_HOME}/fake-mode"
  added_file="${CODEX_HOME}/fake-added"
  json_count_file="${CODEX_HOME}/fake-json-count"
  current_mode="${CODEX_MODE:-missing}"
  previous_mode=""
  if [ -f "$mode_file" ]; then
    previous_mode="$(cat "$mode_file")"
  fi
  if [ "$current_mode" != "$previous_mode" ]; then
    rm -f -- "$added_file" "$json_count_file"
    printf '%s\n' "$current_mode" >"$mode_file"
  fi
  if [ -f "$added_file" ]; then
    startup_timeout="null"
    if grep -Eq '^startup_timeout_sec = 300([.]0)?$' \
      "${CODEX_HOME}/config.toml"; then
      startup_timeout="300.0"
    fi
    if [ "${4:-}" = "--json" ]; then
      cat <<EOF
{"name":"grafana-nebius","enabled":${CODEX_JSON_ENABLED},"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_ADDED_WRAPPER:-${CODEX_EXISTING_WRAPPER}}","args":["--disable-write","--max-loki-log-limit","20"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}","NEBIUS_GRAFANA_IDENTITY_FILE":"${CODEX_JSON_IDENTITY_FILE}","NEBIUS_GRAFANA_USER_PROFILE":"${CODEX_JSON_PROFILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":${startup_timeout},"tool_timeout_sec":null}
EOF
    else
      printf 'grafana-nebius\n'
    fi
    exit 0
  fi
  case "${CODEX_MODE:-missing}" in
    missing | post-write-command-drift)
      exit 1
      ;;
    canonical | canonical-timeout-concurrent)
      startup_timeout="null"
      if [ -f "${CODEX_HOME}/config.toml" ] \
        && grep -Eq '^startup_timeout_sec = 300([.]0)?$' \
          "${CODEX_HOME}/config.toml"; then
        startup_timeout="300.0"
      fi
      if [ "${4:-}" = "--json" ]; then
        cat <<EOF
{"name":"grafana-nebius","enabled":${CODEX_JSON_ENABLED},"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write","--max-loki-log-limit","20"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}","NEBIUS_GRAFANA_IDENTITY_FILE":"${CODEX_JSON_IDENTITY_FILE}","NEBIUS_GRAFANA_USER_PROFILE":"${CODEX_JSON_PROFILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":${startup_timeout},"tool_timeout_sec":null}
EOF
        if [ "${CODEX_MODE:-}" = "canonical-timeout-concurrent" ]; then
          json_count=0
          if [ -f "$json_count_file" ]; then
            json_count="$(cat "$json_count_file")"
          fi
          json_count=$((json_count + 1))
          printf '%s\n' "$json_count" >"$json_count_file"
          if [ "$json_count" -gt 1 ]; then
            printf '# concurrent-timeout-change\n' \
              >>"${CODEX_HOME}/config.toml"
          fi
        fi
      else
        cat <<EOF
grafana-nebius
  enabled: true
  transport: stdio
  command: ${CODEX_EXISTING_WRAPPER}
  args: --disable-write --max-loki-log-limit 20
  cwd: -
  env: GRAFANA_TOKEN_FILE=*****, GRAFANA_URL=*****, NEBIUS_GRAFANA_IDENTITY_FILE=*****, NEBIUS_GRAFANA_USER_PROFILE=*****
EOF
      fi
      exit 0
      ;;
    legacy | unrestorable | unrestorable-secret | unrestorable-secret-value | unrestorable-args-type | add-fails-once | concurrent-change)
      if [ "${4:-}" = "--json" ]; then
        if [ "${CODEX_MODE:-}" = "unrestorable" ]; then
          cat <<EOF
{"name":"grafana-nebius","enabled":true,"disabled_reason":null,"future_setting":true,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
          exit 0
        fi
        if [ "${CODEX_MODE:-}" = "unrestorable-secret" ]; then
          cat <<EOF
{"name":"grafana-nebius","enabled":true,"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_SERVICE_ACCOUNT_TOKEN":"fixture-only"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
          exit 0
        fi
        if [ "${CODEX_MODE:-}" = "unrestorable-secret-value" ]; then
          cat <<EOF
{"name":"grafana-nebius","enabled":true,"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"sensitive-placeholder"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
          exit 0
        fi
        if [ "${CODEX_MODE:-}" = "unrestorable-args-type" ]; then
          cat <<EOF
{"name":"grafana-nebius","enabled":true,"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":"--disable-write","env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
          exit 0
        fi
        if [ "${CODEX_MODE:-}" = "concurrent-change" ]; then
          json_count=0
          if [ -f "$json_count_file" ]; then
            json_count="$(cat "$json_count_file")"
          fi
          json_count=$((json_count + 1))
          printf '%s\n' "$json_count" >"$json_count_file"
          if [ "$json_count" -gt 1 ]; then
            cat <<EOF
{"name":"grafana-nebius","enabled":${CODEX_JSON_ENABLED},"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write","--max-loki-log-limit","20"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}","NEBIUS_GRAFANA_IDENTITY_FILE":"${CODEX_JSON_IDENTITY_FILE}","NEBIUS_GRAFANA_USER_PROFILE":"${CODEX_JSON_PROFILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
            exit 0
          fi
        fi
        cat <<EOF
{"name":"grafana-nebius","enabled":true,"disabled_reason":null,"transport":{"type":"stdio","command":"${CODEX_EXISTING_WRAPPER}","args":["--disable-write"],"env":{"GRAFANA_URL":"${CODEX_JSON_URL}","GRAFANA_TOKEN_FILE":"${CODEX_JSON_TOKEN_FILE}"},"env_vars":[],"cwd":null},"enabled_tools":null,"disabled_tools":null,"startup_timeout_sec":null,"tool_timeout_sec":null}
EOF
      else
        cat <<EOF
grafana-nebius
  enabled: true
  transport: stdio
  command: ${CODEX_EXISTING_WRAPPER}
  args: --disable-write
  cwd: -
  env: GRAFANA_TOKEN_FILE=*****, GRAFANA_URL=*****
EOF
      fi
      exit 0
      ;;
  esac
fi

if [ "${1:-}" = "mcp" ] && { [ "${2:-}" = "add" ] || [ "${2:-}" = "remove" ]; }; then
  printf '%s\n' "$*" >>"$CODEX_CAPTURE_FILE"
  if [ "${CODEX_MODE:-}" = "add-fails-once" ] \
    && [ "${2:-}" = "add" ] \
    && ! grep -Fqx 'add-failed-once' "$CODEX_CAPTURE_FILE"; then
    printf 'add-failed-once\n' >>"$CODEX_CAPTURE_FILE"
    exit 1
  fi
  if [ "${2:-}" = "add" ]; then
    mkdir -p -- "${CODEX_HOME:?CODEX_HOME is required}"
    {
      printf '[mcp_servers.%s]\n' "${3:?MCP server name is required}"
      printf 'command = "/fake/wrapper"\n'
    } >"${CODEX_HOME}/config.toml"
    chmod 600 "${CODEX_HOME}/config.toml"
    : >"${CODEX_HOME}/fake-added"
  else
    rm -f -- \
      "${CODEX_HOME:?CODEX_HOME is required}/config.toml" \
      "${CODEX_HOME}/fake-added"
  fi
  exit 0
fi

printf 'unexpected codex args: %s\n' "$*" >&2
exit 2
SH

chmod +x \
  "${fake_bin}/nebius" \
  "${fake_bin}/mcp-grafana" \
  "${fake_bin}/ps" \
  "${fake_bin}/sleep" \
  "${fake_bin}/strings" \
  "${fake_bin}/codex"

configured_wrapper="${test_home}/.agents/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh"
configured_script_dir="$(dirname -- "$configured_wrapper")"
configured_ensure="${configured_script_dir}/ensure-local-config.sh"
mkdir -p "$configured_script_dir"
cp "$wrapper" "$configured_wrapper"
cp "$ensure_config" "$configured_ensure"
chmod 755 "$configured_wrapper"

nebius_log="${tmp_dir}/nebius.log"
browser_auth_state="${tmp_dir}/browser-auth-state"
token_counter="${tmp_dir}/token-counter"
codex_capture="${tmp_dir}/codex.log"
: >"$nebius_log"
: >"$codex_capture"

run_ensure() {
  HOME="$test_home" \
    CODEX_HOME="${test_home}/.codex" \
    PATH="${fake_bin}:${PATH}" \
    FAKE_NEBIUS_LOG="$nebius_log" \
    FAKE_BROWSER_AUTH_STATE="$browser_auth_state" \
    FAKE_TOKEN_COUNTER="$token_counter" \
    STRINGS_MODE="${STRINGS_MODE:-token-file}" \
    CODEX_CAPTURE_FILE="$codex_capture" \
    CODEX_EXISTING_WRAPPER="${CODEX_EXISTING_WRAPPER:-$configured_wrapper}" \
    CODEX_JSON_URL="${CODEX_JSON_URL:-https://grafana.nebius.dev/}" \
    CODEX_JSON_TOKEN_FILE="${CODEX_JSON_TOKEN_FILE:-${token_file:-/missing/token}}" \
    CODEX_JSON_IDENTITY_FILE="${CODEX_JSON_IDENTITY_FILE:-${identity_file:-/missing/identity}}" \
    CODEX_JSON_PROFILE="${CODEX_JSON_PROFILE:-human-primary}" \
    CODEX_JSON_ENABLED="${CODEX_JSON_ENABLED:-true}" \
    CODEX_ADDED_WRAPPER="${CODEX_ADDED_WRAPPER:-}" \
    WRAPPER_PATH="$configured_wrapper" \
    /bin/bash "$configured_ensure" "$@"
}

run_wrapper() {
  HOME="$test_home" \
    PATH="${fake_bin}:${PATH}" \
    FAKE_NEBIUS_LOG="$nebius_log" \
    FAKE_BROWSER_AUTH_STATE="$browser_auth_state" \
    FAKE_TOKEN_COUNTER="$token_counter" \
    NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
    NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
    GRAFANA_TOKEN_FILE="$token_file" \
    /bin/bash "$wrapper" "$@"
}

snapshot_paths() {
  python3 - "$@" <<'PY'
from pathlib import Path
import hashlib
import json
import stat
import sys

snapshot = {}
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    info = path.lstat()
    record = {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }
    if path.is_file():
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot[str(path)] = record
print(json.dumps(snapshot, sort_keys=True))
PY
}

run_ensure --apply --skip-codex --user-profile human-primary >/dev/null

state_dir="${test_home}/.config/grafana-mcp/nebius/grafana-nebius/human-primary"
identity_file="${state_dir}/identity"
token_file="${state_dir}/token"
test -f "$identity_file"
test -s "$token_file"
test "$(stat -f '%OLp' "$state_dir" 2>/dev/null || stat -c '%a' "$state_dir")" = "700"
test "$(stat -f '%OLp' "$identity_file" 2>/dev/null || stat -c '%a' "$identity_file")" = "600"

STRINGS_MODE="token-file-embedded" \
  run_ensure --check --skip-codex --user-profile human-primary >/dev/null
embedded_config="$(
  STRINGS_MODE="token-file-embedded" \
    run_wrapper --print-config
)"
printf '%s\n' "$embedded_config" \
  | grep -Fxq 'MCP_GRAFANA_TOKEN_MODE=token-file'
printf '%s\n' "$embedded_config" \
  | grep -Fxq 'NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=90'
printf '%s\n' "$embedded_config" \
  | grep -Fxq 'NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=210'

capture_file="${tmp_dir}/startup-capture"
: >"$token_counter"
: >"$nebius_log"
rm -f -- "$token_file" "$browser_auth_state"

STRINGS_MODE="token-file" \
  FAKE_AUTH_REQUIRES_BROWSER="true" \
  MCP_CAPTURE_FILE="${tmp_dir}/missing-token-capture" \
  run_wrapper >"${tmp_dir}/missing-token-output" 2>&1
test -s "$token_file"
test "$(cat "$token_counter")" = "1"
grep -Fq 'renewing it before MCP startup' "${tmp_dir}/missing-token-output"
grep -Fq '<browser-auth-url-redacted>' "${tmp_dir}/missing-token-output"
if grep -Fq 'state=fixture-only' "${tmp_dir}/missing-token-output"; then
  printf 'foreground startup output exposed the browser authentication URL\n' >&2
  exit 1
fi
grep -Fq 'args:iam whoami' "$nebius_log"
grep -Fq 'args:iam get-access-token' "$nebius_log"
if sed -n '1p' "$nebius_log" | grep -Fq -- '--no-browser'; then
  printf 'foreground startup identity validation unexpectedly disabled browser authentication\n' >&2
  exit 1
fi
grep -F 'args:iam get-access-token' "$nebius_log" \
  | grep -Fq -- '--no-browser'
test "$(grep -Fc -- '--no-browser' "$nebius_log")" = "2"
grep -Fxq "token_file=$token_file" "${tmp_dir}/missing-token-capture"

CODEX_NEBIUS_PROJECT_ID="project-agent" \
  CODEX_NEBIUS_TOKEN_HELPER="/tmp/agent-helper" \
  NEBIUS_AUTH_CREDENTIALS_FILE="/tmp/agent-credentials.json" \
  NEBIUS_IAM_TOKEN="stale-agent-token" \
  NEBIUS_PROFILE="codex-agent-project" \
  NEBIUS_PROJECT_ID="project-agent" \
  TOKEN="stale-token" \
  run_wrapper --refresh-token-only >"${tmp_dir}/startup-refresh-output" 2>&1

CODEX_NEBIUS_PROJECT_ID="project-agent" \
  CODEX_NEBIUS_TOKEN_HELPER="/tmp/agent-helper" \
  NEBIUS_AUTH_CREDENTIALS_FILE="/tmp/agent-credentials.json" \
  NEBIUS_IAM_TOKEN="stale-agent-token" \
  NEBIUS_PROFILE="codex-agent-project" \
  NEBIUS_PROJECT_ID="project-agent" \
  TOKEN="stale-token" \
  GRAFANA_API_KEY="stale-key" \
  GRAFANA_ORG_ID="999" \
  GRAFANA_PASSWORD="stale-password" \
  GRAFANA_SERVICE_ACCOUNT_TOKEN="stale-grafana-token" \
  GRAFANA_USERNAME="stale-user" \
  STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  run_wrapper >"${tmp_dir}/startup-output" 2>&1

grep -Fxq 'token=' "$capture_file"
grep -Fxq "token_file=$token_file" "$capture_file"
grep -Fxq 'args=--disable-write --max-loki-log-limit 20' "$capture_file"
if grep -Fq 'competing:' "$capture_file"; then
  printf 'competing Grafana authentication reached mcp-grafana\n' >&2
  exit 1
fi
if grep -Fq 'leaked:' "$nebius_log"; then
  printf 'ambient Nebius authentication reached a pinned-profile CLI call\n' >&2
  exit 1
fi
if grep -Fq 'fake-nebius-token-' "${tmp_dir}/startup-output"; then
  printf 'wrapper output leaked a token\n' >&2
  exit 1
fi
if grep -Fq 'fake-nebius-token-' "${tmp_dir}/startup-refresh-output"; then
  printf 'refresh-only wrapper output leaked a token\n' >&2
  exit 1
fi
test "$(stat -f '%OLp' "$token_file" 2>/dev/null || stat -c '%a' "$token_file")" = "600"

capture_file="${tmp_dir}/token-file-capture"
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  run_wrapper >/dev/null 2>&1

grep -Fxq 'token=' "$capture_file"
grep -Fxq "token_file=$token_file" "$capture_file"
grep -Fxq 'args=--disable-write --max-loki-log-limit 20' "$capture_file"

stdio_capture="${tmp_dir}/stdio-capture"
printf 'mcp-stdio-probe\n' \
  | STRINGS_MODE="token-file" \
    MCP_CAPTURE_FILE="${tmp_dir}/stdio-state-capture" \
    MCP_STDIN_CAPTURE_FILE="$stdio_capture" \
    run_wrapper >/dev/null 2>&1
grep -Fxq 'mcp-stdio-probe' "$stdio_capture"

python3 - "$token_file" <<'PY'
import os
import sys
import time

stale = time.time() - 7200
os.utime(sys.argv[1], (stale, stale))
PY
: >"$nebius_log"
rm -f -- "$browser_auth_state"
STRINGS_MODE="token-file" \
  FAKE_AUTH_REQUIRES_BROWSER="true" \
  MCP_CAPTURE_FILE="${tmp_dir}/stale-startup-capture" \
  run_wrapper >"${tmp_dir}/stale-startup-output" 2>&1
grep -Fq 'renewing it before MCP startup' "${tmp_dir}/stale-startup-output"
grep -Fxq "token_file=$token_file" "${tmp_dir}/stale-startup-capture"
if sed -n '1p' "$nebius_log" | grep -Fq -- '--no-browser'; then
  printf 'stale-token startup identity validation unexpectedly disabled browser authentication\n' >&2
  exit 1
fi
grep -F 'args:iam get-access-token' "$nebius_log" \
  | grep -Fq -- '--no-browser'
test "$(grep -Fc -- '--no-browser' "$nebius_log")" = "2"
run_wrapper --refresh-token-only >/dev/null 2>&1

capture_file="${tmp_dir}/canonical-registration-capture"
  STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  run_wrapper \
    --disable-write \
    --max-loki-log-limit 20 >/dev/null 2>&1
grep -Fxq 'args=--disable-write --max-loki-log-limit 20' "$capture_file"

if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/weakened-capture" \
  run_wrapper --disable-write=false >/dev/null 2>&1; then
  printf 'expected a read-only policy override to be rejected\n' >&2
  exit 1
fi

if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/proxied-capture" \
  run_wrapper --disable-proxied >/dev/null 2>&1; then
  printf 'expected --disable-proxied to be rejected\n' >&2
  exit 1
fi

if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/enabled-tools-capture" \
  run_wrapper --enabled-tools prometheus >/dev/null 2>&1; then
  printf 'expected a tool allowlist override to be rejected\n' >&2
  exit 1
fi

if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/tls-capture" \
  run_wrapper --tls-skip-verify >/dev/null 2>&1; then
  printf 'expected a transport security override to be rejected\n' >&2
  exit 1
fi

if HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  FAKE_NEBIUS_LOG="$nebius_log" \
  FAKE_TOKEN_COUNTER="$token_counter" \
  FAKE_USER_ID="tenantuseraccount-different" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  /bin/bash "$wrapper" --refresh-token-only >/dev/null 2>&1; then
  printf 'expected profile identity mutation to fail closed\n' >&2
  exit 1
fi

if FAKE_IDENTITY_MODE="service" \
  run_ensure --check --skip-codex --user-profile human-service >/dev/null 2>&1; then
  printf 'expected a service-account profile to be rejected\n' >&2
  exit 1
fi

if FAKE_IDENTITY_MODE="anonymous" \
  run_ensure --check --skip-codex --user-profile human-anonymous >/dev/null 2>&1; then
  printf 'expected an anonymous profile to be rejected\n' >&2
  exit 1
fi

if FAKE_IDENTITY_MODE="mixed" \
  run_ensure --check --skip-codex --user-profile human-mixed >/dev/null 2>&1; then
  printf 'expected a mixed identity payload to be rejected\n' >&2
  exit 1
fi

if run_ensure --check --skip-codex --user-profile codex-agent-project >/dev/null 2>&1; then
  printf 'expected an agent profile name to be rejected\n' >&2
  exit 1
fi

FAKE_USER_ID="tenantuseraccount-human-secondary" \
  run_ensure --apply --skip-codex --user-profile human-secondary >/dev/null
secondary_token="${test_home}/.config/grafana-mcp/nebius/grafana-nebius/human-secondary/token"
test "$secondary_token" != "$token_file"

trusted_root="${test_home}/custom-wrapper-root"
trusted_wrapper="${trusted_root}/scripts/run-nebius-grafana-mcp.sh"
mkdir -p "$(dirname -- "$trusted_wrapper")"
cp "$wrapper" "$trusted_wrapper"
chmod 755 "$trusted_wrapper"
chmod 775 "$trusted_root"
if run_ensure \
  --check \
  --skip-codex \
  --wrapper-path "$trusted_wrapper" \
  --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a group-writable wrapper parent to be rejected\n' >&2
  exit 1
fi
chmod 755 "$trusted_root"
ln -s "$trusted_root" "${test_home}/linked-wrapper-root"
if run_ensure \
  --check \
  --skip-codex \
  --wrapper-path "${test_home}/linked-wrapper-root/scripts/run-nebius-grafana-mcp.sh" \
  --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a symlinked wrapper parent to be rejected\n' >&2
  exit 1
fi

unsafe_target="${test_home}/unsafe-target"
mkdir -p "$unsafe_target"
ln -s "$unsafe_target" "${test_home}/unsafe-state"
if run_ensure \
  --apply \
  --skip-codex \
  --state-root "${test_home}/unsafe-state" \
  --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a symlinked state root to be rejected\n' >&2
  exit 1
fi

if run_ensure \
  --apply \
  --skip-codex \
  --state-root "${test_home}/normalized/../escaped-state" \
  --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a non-normalized state root to be rejected\n' >&2
  exit 1
fi
test ! -e "${test_home}/escaped-state"

if HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  FAKE_NEBIUS_LOG="$nebius_log" \
  FAKE_TOKEN_COUNTER="$token_counter" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="relative-token" \
  /bin/bash "$wrapper" --refresh-token-only >/dev/null 2>&1; then
  printf 'expected a relative token path to be rejected\n' >&2
  exit 1
fi

if HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  FAKE_NEBIUS_LOG="$nebius_log" \
  FAKE_TOKEN_COUNTER="$token_counter" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="${state_dir}/nested/../token" \
  /bin/bash "$wrapper" --refresh-token-only >/dev/null 2>&1; then
  printf 'expected a non-normalized token path to be rejected\n' >&2
  exit 1
fi

: >"$codex_capture"
CODEX_MODE="missing" \
  run_ensure --apply --user-profile human-primary >/dev/null
grep -Fq 'mcp add grafana-nebius' "$codex_capture"
grep -Fq "GRAFANA_TOKEN_FILE=${token_file}" "$codex_capture"
grep -Fq "NEBIUS_GRAFANA_IDENTITY_FILE=${identity_file}" "$codex_capture"
grep -Fq 'NEBIUS_GRAFANA_USER_PROFILE=human-primary' "$codex_capture"
grep -Fq -- '--disable-write --max-loki-log-limit 20' "$codex_capture"

if CODEX_MODE="post-write-command-drift" \
  CODEX_ADDED_WRAPPER="/unexpected/wrapper" \
  run_ensure \
    --apply \
    --user-profile human-primary >"${tmp_dir}/post-write-command-output" 2>&1; then
  printf 'expected a post-write command mismatch to fail verification\n' >&2
  exit 1
fi
grep -Fq \
  'added MCP registration did not have the exact canonical pre-timeout values' \
  "${tmp_dir}/post-write-command-output"
test -e "${test_home}/.codex/fake-added"
if grep -Fq 'mcp remove grafana-nebius' "$codex_capture"; then
  printf 'post-write drift must not delete potentially concurrent state\n' >&2
  exit 1
fi

CODEX_MODE="canonical" \
  run_ensure --apply --user-profile human-primary >/dev/null
CODEX_MODE="canonical" \
  run_ensure --check --user-profile human-primary >/dev/null

canonical_state_before="$(
  snapshot_paths \
    "${test_home}/.codex/config.toml" \
    "${test_home}/.config/grafana-mcp/nebius" \
    "${test_home}/.config/grafana-mcp/nebius/grafana-nebius" \
    "$state_dir" \
    "$identity_file" \
    "$token_file"
)"
token_counter_before="$(cat "$token_counter")"
: >"$codex_capture"
for _ in 1 2 3; do
  CODEX_MODE="canonical" \
    run_ensure --check --user-profile human-primary >/dev/null
  CODEX_MODE="canonical" \
    run_ensure --apply --user-profile human-primary >/dev/null
done
canonical_state_after="$(
  snapshot_paths \
    "${test_home}/.codex/config.toml" \
    "${test_home}/.config/grafana-mcp/nebius" \
    "${test_home}/.config/grafana-mcp/nebius/grafana-nebius" \
    "$state_dir" \
    "$identity_file" \
    "$token_file"
)"
if [ "$canonical_state_before" != "$canonical_state_after" ]; then
  printf 'repeated canonical check/apply changed protected setup state\n' >&2
  exit 1
fi
test "$(cat "$token_counter")" = "$token_counter_before"
if grep -Eq 'mcp (add|remove) grafana-nebius' "$codex_capture"; then
  printf 'repeated canonical check/apply rewrote the MCP registration\n' >&2
  exit 1
fi

protected_state_before="$canonical_state_after"
: >"$codex_capture"
if CODEX_MODE="canonical" \
  CODEX_JSON_PROFILE="human-secondary" \
  run_ensure \
    --apply \
    --user-profile human-primary >"${tmp_dir}/mismatch-without-replace-output" 2>&1; then
  printf 'expected mismatched MCP state to require explicit replacement\n' >&2
  exit 1
fi
grep -Fq \
  'existing MCP server requires replacement' \
  "${tmp_dir}/mismatch-without-replace-output"
protected_state_after="$(
  snapshot_paths \
    "${test_home}/.codex/config.toml" \
    "${test_home}/.config/grafana-mcp/nebius" \
    "${test_home}/.config/grafana-mcp/nebius/grafana-nebius" \
    "$state_dir" \
    "$identity_file" \
    "$token_file"
)"
if [ "$protected_state_before" != "$protected_state_after" ]; then
  printf 'failed closed mismatch handling changed protected setup state\n' >&2
  exit 1
fi
test "$(cat "$token_counter")" = "$token_counter_before"
if grep -Eq 'mcp (add|remove) grafana-nebius' "$codex_capture"; then
  printf 'mismatch without replacement approval rewrote the MCP registration\n' >&2
  exit 1
fi

python3 - "${test_home}/.codex/config.toml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
path.write_text(
    "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.startswith("startup_timeout_sec = ")
    )
)
PY
if CODEX_MODE="canonical" \
  run_ensure --check --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a missing MCP startup timeout to report drift\n' >&2
  exit 1
fi
CODEX_MODE="canonical" \
  run_ensure --apply --user-profile human-primary >/dev/null
grep -Fxq 'startup_timeout_sec = 300.0' \
  "${test_home}/.codex/config.toml"

python3 - "${test_home}/.codex/config.toml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
path.write_text(
    "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.startswith("startup_timeout_sec = ")
    )
)
PY
if CODEX_MODE="canonical-timeout-concurrent" \
  run_ensure \
    --apply \
    --user-profile human-primary >"${tmp_dir}/timeout-concurrent-output" 2>&1; then
  printf 'expected a concurrent Codex config byte change to block timeout repair\n' >&2
  exit 1
fi
grep -Fq \
  'Codex config changed after structured inspection' \
  "${tmp_dir}/timeout-concurrent-output"
grep -Fxq '# concurrent-timeout-change' \
  "${test_home}/.codex/config.toml"
if grep -Fq 'startup_timeout_sec' "${test_home}/.codex/config.toml"; then
  printf 'timeout repair must not overwrite a concurrently changed config\n' >&2
  exit 1
fi
CODEX_MODE="canonical" \
  run_ensure --apply --user-profile human-primary >/dev/null
grep -Fxq 'startup_timeout_sec = 300.0' \
  "${test_home}/.codex/config.toml"

if CODEX_MODE="canonical" \
  CODEX_JSON_PROFILE="human-secondary" \
  run_ensure --check --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a masked but wrong profile value to require migration\n' >&2
  exit 1
fi

if CODEX_MODE="canonical" \
  CODEX_JSON_ENABLED="false" \
  run_ensure --check --user-profile human-primary >/dev/null 2>&1; then
  printf 'expected a disabled canonical-looking server to require migration\n' >&2
  exit 1
fi

if CODEX_MODE="legacy" \
  run_ensure --check --user-profile human-primary >"${tmp_dir}/legacy-check" 2>&1; then
  printf 'expected legacy MCP registration check to report drift\n' >&2
  exit 1
fi
grep -Fq -- '--apply --replace-existing' "${tmp_dir}/legacy-check"

: >"$codex_capture"
if CODEX_MODE="unrestorable" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/unrestorable-output" 2>&1; then
  printf 'expected unknown MCP registration fields to block replacement\n' >&2
  exit 1
fi
grep -Fq \
  'non-default or malformed structured settings' \
  "${tmp_dir}/unrestorable-output"
if grep -Fq 'mcp remove grafana-nebius' "$codex_capture"; then
  printf 'unroundtrippable registration must be rejected before removal\n' >&2
  exit 1
fi

: >"$codex_capture"
CODEX_MODE="unrestorable-secret" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/unrestorable-secret-output" 2>&1
grep -Fxq 'mcp remove grafana-nebius' "$codex_capture"
grep -Fq 'mcp add grafana-nebius' "$codex_capture"
if grep -Fq 'fixture-only' \
  "$codex_capture" "${tmp_dir}/unrestorable-secret-output"; then
  printf 'discarded inline secret must not be replayed or printed\n' >&2
  exit 1
fi

: >"$codex_capture"
CODEX_MODE="unrestorable-secret-value" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/unrestorable-secret-value-output" 2>&1
grep -Fxq 'mcp remove grafana-nebius' "$codex_capture"
grep -Fq 'mcp add grafana-nebius' "$codex_capture"
if grep -Fq 'sensitive-placeholder' \
  "$codex_capture" "${tmp_dir}/unrestorable-secret-value-output"; then
  printf 'discarded secret-bearing value must not be replayed or printed\n' >&2
  exit 1
fi

: >"$codex_capture"
if CODEX_MODE="unrestorable-args-type" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/unrestorable-args-type-output" 2>&1; then
  printf 'expected string-valued MCP arguments to block replacement\n' >&2
  exit 1
fi
grep -Fq \
  'non-default or malformed structured settings' \
  "${tmp_dir}/unrestorable-args-type-output"
if grep -Fq 'mcp remove grafana-nebius' "$codex_capture"; then
  printf 'string-valued MCP arguments must be rejected before removal\n' >&2
  exit 1
fi

: >"$codex_capture"
if CODEX_MODE="concurrent-change" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/concurrent-change-output" 2>&1; then
  printf 'expected concurrent MCP registration drift to block replacement\n' >&2
  exit 1
fi
grep -Fq \
  'registration changed concurrently after inspection' \
  "${tmp_dir}/concurrent-change-output"
if grep -Fq 'mcp remove grafana-nebius' "$codex_capture"; then
  printf 'concurrent registration drift must be detected before removal\n' >&2
  exit 1
fi

: >"$codex_capture"
CODEX_MODE="legacy" \
  run_ensure --apply --replace-existing --user-profile human-primary >/dev/null
grep -Fxq 'mcp remove grafana-nebius' "$codex_capture"
grep -Fq 'mcp add grafana-nebius' "$codex_capture"

: >"$codex_capture"
if CODEX_MODE="add-fails-once" \
  run_ensure \
    --apply \
    --replace-existing \
    --user-profile human-primary >"${tmp_dir}/replacement-failure-output" 2>&1; then
  printf 'expected a failed canonical replacement to return failure\n' >&2
  exit 1
fi
grep -Fq \
  'no prior values were replayed' \
  "${tmp_dir}/replacement-failure-output"
test "$(grep -Fc 'mcp add grafana-nebius' "$codex_capture")" = "1"
test ! -e "${test_home}/.codex/config.toml"

: >"$token_counter"
unsafe_lock_target="${tmp_dir}/unsafe-lock-target"
mkdir "$unsafe_lock_target"
printf 'do-not-remove\n' >"${unsafe_lock_target}/owner"
ln -s "$unsafe_lock_target" "${token_file}.lock"
if NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  run_wrapper --refresh-token-only >/dev/null 2>&1; then
  printf 'expected a symlinked refresh lock to be rejected\n' >&2
  exit 1
fi
grep -Fxq 'do-not-remove' "${unsafe_lock_target}/owner"
rm "${token_file}.lock"

: >"$token_counter"
mkdir "${token_file}.lock"
chmod 700 "${token_file}.lock"
cat >"${token_file}.lock/owner" <<'EOF'
version=1
pid=999999
started_at_epoch=1
process_started_at=stale-process
EOF
chmod 600 "${token_file}.lock/owner"
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/stale-lock-capture" \
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  run_wrapper --refresh-token-only >"${tmp_dir}/stale-lock-output" 2>&1
grep -Fq 'recovered a stale profile-scoped token refresh lock' \
  "${tmp_dir}/stale-lock-output"
test ! -e "${token_file}.lock"

: >"$token_counter"
mkdir "${token_file}.lock"
chmod 700 "${token_file}.lock"
cat >"${token_file}.lock/owner" <<EOF
version=1
pid=$$
started_at_epoch=1
process_started_at=definitely-not-this-process
EOF
chmod 600 "${token_file}.lock/owner"
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/reused-pid-lock-capture" \
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  run_wrapper --refresh-token-only >"${tmp_dir}/reused-pid-lock-output" 2>&1
grep -Fq 'recovered a stale profile-scoped token refresh lock' \
  "${tmp_dir}/reused-pid-lock-output"
test ! -e "${token_file}.lock"

: >"$token_counter"
mkdir "${token_file}.lock"
chmod 700 "${token_file}.lock"
python3 - "${token_file}.lock" <<'PY'
import os
import sys

os.utime(sys.argv[1], (1, 1))
PY
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="${tmp_dir}/incomplete-lock-capture" \
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=1 \
  run_wrapper --refresh-token-only >"${tmp_dir}/incomplete-lock-output" 2>&1
grep -Fq 'recovered an incomplete profile-scoped token refresh lock' \
  "${tmp_dir}/incomplete-lock-output"
test ! -e "${token_file}.lock"

for refresh_iteration in 1 2 3; do
  : >"$token_counter"
  FAKE_TOKEN_DELAY_AFTER=0 \
    FAKE_TOKEN_DELAY_SECONDS=1 \
    NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
    NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=20 \
    run_wrapper --refresh-token-only \
      >"${tmp_dir}/first-refresh-output-${refresh_iteration}" 2>&1 &
  first_refresh_pid="$!"
  sleep 0.1
  if ! FAKE_TOKEN_DELAY_AFTER=0 \
    FAKE_TOKEN_DELAY_SECONDS=1 \
    NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
    NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=20 \
    run_wrapper --refresh-token-only \
      >"${tmp_dir}/second-refresh-output-${refresh_iteration}" 2>&1; then
    printf 'expected the second concurrent refresh to wait and succeed\n' >&2
    sed -n '1,20p' "${tmp_dir}/first-refresh-output-${refresh_iteration}" >&2
    sed -n '1,20p' "${tmp_dir}/second-refresh-output-${refresh_iteration}" >&2
    exit 1
  fi
  if ! wait "$first_refresh_pid"; then
    printf 'expected the first concurrent refresh to succeed\n' >&2
    exit 1
  fi
  test "$(cat "$token_counter")" = "1"
  test ! -e "${token_file}.lock"
done

: >"$token_counter"
FAKE_TOKEN_DELAY_AFTER=0 \
  FAKE_TOKEN_DELAY_SECONDS=3 \
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=5 \
  run_wrapper --refresh-token-only \
    >"${tmp_dir}/slow-refresh-owner-output" 2>&1 &
slow_refresh_owner_pid="$!"
sleep 0.1
if ! FAKE_TOKEN_DELAY_AFTER=0 \
  FAKE_TOKEN_DELAY_SECONDS=3 \
  NEBIUS_GRAFANA_LOCK_WAIT_SECONDS=1 \
  NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS=5 \
  run_wrapper --refresh-token-only \
    >"${tmp_dir}/slow-refresh-waiter-output" 2>&1; then
  printf 'expected foreground waiter to outlive the shorter background lock budget\n' >&2
  exit 1
fi
wait "$slow_refresh_owner_pid"
test "$(cat "$token_counter")" = "1"
grep -Fq \
  'reused the fresh profile-scoped token produced by the prior refresh owner' \
  "${tmp_dir}/slow-refresh-waiter-output"
test ! -e "${token_file}.lock"

printf '1\n' >"$token_counter"
capture_file="${tmp_dir}/refresh-capture"
if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_HOLD_SECONDS=3 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
  run_wrapper >"${tmp_dir}/refresh-output" 2>&1; then
  printf 'expected scheduled token rotation to require MCP restart\n' >&2
  exit 1
else
  refresh_status="$?"
fi
test "$refresh_status" = "75"
grep -Fq 'token_file_initial=fake-nebius-token-1' "$capture_file"
grep -Fxq 'fake-nebius-token-2' "$token_file"
if grep -Fxq 'natural_exit=true' "$capture_file"; then
  printf 'scheduled rotation must stop MCP before natural exit\n' >&2
  exit 1
fi
grep -Fq 'start a new chat or restart Codex' "${tmp_dir}/refresh-output"

capture_file="${tmp_dir}/post-refresh-process-capture"
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  run_wrapper >/dev/null 2>&1
grep -Fq 'token_file_initial=fake-nebius-token-2' "$capture_file"

: >"$token_counter"
capture_file="${tmp_dir}/refresh-failure-capture"
token_before_failure="$(cat "$token_file")"
if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_HOLD_SECONDS=5 \
  FAKE_TOKEN_FAIL_AFTER=0 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
  run_wrapper >/dev/null 2>&1; then
  printf 'expected exhausted token refresh to fail the wrapper\n' >&2
  exit 1
else
  refresh_failure_status="$?"
fi
test "$refresh_failure_status" = "1"
test "$(cat "$token_file")" = "$token_before_failure"
if grep -Fxq 'natural_exit=true' "$capture_file"; then
  printf 'refresh exhaustion must stop MCP before natural exit\n' >&2
  exit 1
fi

: >"$token_counter"
capture_file="${tmp_dir}/refresh-worker-failure-capture"
cat >"${fake_bin}/chmod" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ "${2##*/}" = "owner" ]; then
  exit 1
fi
PATH="${PATH#*:}" exec chmod "$@"
SH
chmod +x "${fake_bin}/chmod"
if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_HOLD_SECONDS=5 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
  run_wrapper >"${tmp_dir}/refresh-worker-failure-output" 2>&1; then
  printf 'expected an unexpected refresh-worker exit to fail the wrapper\n' >&2
  exit 1
else
  refresh_worker_failure_status="$?"
fi
test "$refresh_worker_failure_status" = "1"
if grep -Fxq 'natural_exit=true' "$capture_file"; then
  printf 'refresh-worker failure must stop MCP before natural exit\n' >&2
  exit 1
fi
grep -Fq \
  'token refresh worker failed unexpectedly; stopping MCP' \
  "${tmp_dir}/refresh-worker-failure-output"
test ! -e "${token_file}.lock"
rm -f -- "${fake_bin}/chmod"

: >"$token_counter"
capture_file="${tmp_dir}/refresh-shutdown-capture"
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_HOLD_SECONDS=2 \
  FAKE_TOKEN_DELAY_AFTER=0 \
  FAKE_TOKEN_DELAY_SECONDS=5 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
  run_wrapper >/dev/null 2>&1
test ! -e "${token_file}.lock"
if find "$state_dir" -maxdepth 1 -name 'token.tmp.*' -print -quit | grep -q .; then
  printf 'expected refresh shutdown to remove the temporary token file\n' >&2
  exit 1
fi
if find "$state_dir" -maxdepth 1 -name 'token.restart.*' -print -quit | grep -q .; then
  printf 'expected wrapper shutdown to remove refresh-reason state\n' >&2
  exit 1
fi

capture_file="${tmp_dir}/natural-exit-capture"
if STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_EXIT_STATUS=42 \
  run_wrapper >/dev/null 2>&1; then
  printf 'expected the wrapper to preserve the natural MCP exit status\n' >&2
  exit 1
else
  natural_exit_status="$?"
fi
test "$natural_exit_status" = "42"

for race_iteration in 1 2 3; do
  : >"$token_counter"
  capture_file="${tmp_dir}/refresh-exit-race-${race_iteration}"
  race_status=0
  STRINGS_MODE="token-file" \
    MCP_CAPTURE_FILE="$capture_file" \
    MCP_HOLD_SECONDS=1 \
    MCP_EXIT_STATUS=42 \
    NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
    NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
    run_wrapper >/dev/null 2>&1 || race_status="$?"
  case "$race_status" in
    42)
      grep -Fxq 'natural_exit=true' "$capture_file"
      ;;
    75)
      if grep -Fxq 'natural_exit=true' "$capture_file"; then
        printf 'refresh termination must not be reported as a natural exit\n' >&2
        exit 1
      fi
      ;;
    *)
      printf 'unexpected refresh/natural-exit race status: %s\n' "$race_status" >&2
      exit 1
      ;;
  esac
  test ! -e "${token_file}.lock"
done

: >"$token_counter"
capture_file="${tmp_dir}/hidden-stop-capture"
stopped_marker="${tmp_dir}/hidden-stop-observed"
hidden_stop_status=0
STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_HOLD_SECONDS=10 \
  FAKE_PS_HIDE_STOPPED="true" \
  FAKE_PS_STOPPED_MARKER="$stopped_marker" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0 0 0" \
  run_wrapper >/dev/null 2>&1 || hidden_stop_status="$?"
test "$hidden_stop_status" = "137"
test -f "$stopped_marker"

: >"$token_counter"
capture_file="${tmp_dir}/interrupt-stopped-child-capture"
mcp_pid_file="${tmp_dir}/interrupt-stopped-child-pid"
refresher_pid_file="${tmp_dir}/interrupt-stopped-refresher-pid"
HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  FAKE_NEBIUS_LOG="$nebius_log" \
  FAKE_TOKEN_COUNTER="$token_counter" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  STRINGS_MODE="token-file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_PID_CAPTURE_FILE="$mcp_pid_file" \
  MCP_HOLD_SECONDS=30 \
  FAKE_REFRESHER_PID_CAPTURE_FILE="$refresher_pid_file" \
  FAKE_REFRESHER_SLEEP_SECONDS=30 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=30 \
  /bin/bash "$wrapper" >/dev/null 2>&1 &
wrapper_pid="$!"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  [ -s "$mcp_pid_file" ] && [ -s "$refresher_pid_file" ] && break
  sleep 0.1
done
test -s "$mcp_pid_file"
test -s "$refresher_pid_file"
mcp_child_pid="$(cat "$mcp_pid_file")"
refresher_child_pid="$(cat "$refresher_pid_file")"
case "$mcp_child_pid" in
  '' | *[!0-9]*)
    printf 'fake MCP did not report a numeric child PID\n' >&2
    exit 1
    ;;
esac
case "$refresher_child_pid" in
  '' | *[!0-9]*)
    printf 'refresh worker did not report a numeric child PID\n' >&2
    exit 1
    ;;
esac
kill -STOP "$mcp_child_pid"
kill -STOP "$refresher_child_pid"
kill -TERM "$wrapper_pid"
wrapper_exited="false"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 \
  21 22 23 24 25 26 27 28 29 30; do
  if ! kill -0 "$wrapper_pid" >/dev/null 2>&1; then
    wrapper_exited="true"
    break
  fi
  sleep 0.1
done
if [ "$wrapper_exited" != "true" ]; then
  kill -KILL "$wrapper_pid" >/dev/null 2>&1 || true
  kill -CONT "$mcp_child_pid" >/dev/null 2>&1 || true
  kill -KILL "$mcp_child_pid" >/dev/null 2>&1 || true
  kill -CONT "$refresher_child_pid" >/dev/null 2>&1 || true
  kill -KILL "$refresher_child_pid" >/dev/null 2>&1 || true
  wait "$wrapper_pid" >/dev/null 2>&1 || true
  printf 'wrapper cleanup hung while both exact children were stopped\n' >&2
  exit 1
fi
interrupt_status=0
wait "$wrapper_pid" || interrupt_status="$?"
test "$interrupt_status" = "143"
if kill -0 "$mcp_child_pid" >/dev/null 2>&1; then
  printf 'wrapper cleanup left its stopped MCP child alive\n' >&2
  exit 1
fi
if kill -0 "$refresher_child_pid" >/dev/null 2>&1; then
  printf 'wrapper cleanup left its stopped refresh worker alive\n' >&2
  exit 1
fi

: >"$token_counter"
capture_file="${tmp_dir}/unsupported-token-mode-capture"
if STRINGS_MODE="inline" \
  MCP_CAPTURE_FILE="$capture_file" \
  run_wrapper >/dev/null 2>&1; then
  printf 'expected a build without token-file authentication to be rejected\n' >&2
  exit 1
fi

if HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  GRAFANA_URL="https://external.example/" \
  /bin/bash "$wrapper" --print-config >/dev/null 2>&1; then
  printf 'expected an external Grafana URL to be rejected\n' >&2
  exit 1
fi

if HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="1800 1800 1" \
  /bin/bash "$wrapper" --print-config >/dev/null 2>&1; then
  printf 'expected retry delays beyond the pre-expiry budget to be rejected\n' >&2
  exit 1
fi

HOME="$test_home" \
  PATH="${fake_bin}:${PATH}" \
  NEBIUS_GRAFANA_USER_PROFILE="human-primary" \
  NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_RETRY_SECONDS="0000 0000 0000" \
  /bin/bash "$wrapper" --print-config >/dev/null

printf 'Grafana MCP human-profile wrapper tests passed.\n'
