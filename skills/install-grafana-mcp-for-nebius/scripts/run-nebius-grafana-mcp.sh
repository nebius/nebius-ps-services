#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Run Grafana MCP for Nebius-managed Grafana with a refreshed Nebius IAM token.

This wrapper writes a fresh Nebius access token to GRAFANA_TOKEN_FILE, exports
that token to mcp-grafana, refreshes the file periodically while the server is
running when the installed mcp-grafana supports token-file auth, and starts
mcp-grafana in read-only mode by default.

Environment:
  GRAFANA_URL
      Grafana base URL. Defaults to https://grafana.nebius.dev/.
  GRAFANA_TOKEN_FILE
      Local token file. Defaults to $HOME/.config/grafana-mcp/token.
  NEBIUS_PROFILE
      Optional Nebius CLI profile name passed to nebius --profile.
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS
      Refresh interval. Defaults to 36000 seconds, below the Nebius 12h token
      lifetime.

Usage:
  run-nebius-grafana-mcp.sh [mcp-grafana args...]
  run-nebius-grafana-mcp.sh --refresh-token-only
  run-nebius-grafana-mcp.sh --print-config
USAGE
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'error: required command not found on PATH: %s\n' "$1" >&2
    exit 127
  fi
}

validate_nebius_grafana_url() {
  case "$GRAFANA_URL" in
    https://grafana.nebius.dev | https://grafana.nebius.dev/)
      return
      ;;
  esac

  printf 'error: this Nebius-managed wrapper only supports GRAFANA_URL=https://grafana.nebius.dev/\n' >&2
  printf 'error: use a generic mcp-grafana service-account-token setup for external Grafana instances\n' >&2
  exit 2
}

refresh_token() {
  local token_dir
  local tmp_file

  token_dir="$(dirname "$GRAFANA_TOKEN_FILE")"
  if [ "$token_dir" != "." ] && [ ! -d "$token_dir" ]; then
    mkdir -p "$token_dir"
    chmod 700 "$token_dir" 2>/dev/null || true
  fi

  tmp_file="$(mktemp "${GRAFANA_TOKEN_FILE}.tmp.XXXXXX")"
  chmod 600 "$tmp_file" 2>/dev/null || true

  if [ -n "${NEBIUS_PROFILE:-}" ]; then
    if ! nebius --profile "$NEBIUS_PROFILE" iam get-access-token >"$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  else
    if ! nebius iam get-access-token >"$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  fi

  if [ ! -s "$tmp_file" ]; then
    rm -f "$tmp_file"
    printf 'error: Nebius CLI returned an empty access token\n' >&2
    return 1
  fi

  mv "$tmp_file" "$GRAFANA_TOKEN_FILE"
  chmod 600 "$GRAFANA_TOKEN_FILE" 2>/dev/null || true
}

mcp_supports_token_file() {
  local mcp_path

  mcp_path="$(command -v mcp-grafana)"
  if ! command -v strings >/dev/null 2>&1; then
    return 1
  fi

  strings "$mcp_path" 2>/dev/null \
    | grep -Fqx "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE"
}

validate_refresh_interval() {
  case "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS" in
    '' | *[!0-9]*)
      printf 'error: NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS must be a positive integer below 43200\n' >&2
      exit 2
      ;;
  esac

  if [ "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS" -le 0 ] \
    || [ "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS" -ge 43200 ]; then
    printf 'error: NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS must be below the 12-hour Nebius token lifetime\n' >&2
    exit 2
  fi
}

configure_token_environment() {
  local refreshed_token

  if mcp_supports_token_file; then
    if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}" ]; then
      printf 'warning: ignoring inline GRAFANA_SERVICE_ACCOUNT_TOKEN; using token file refresh\n' >&2
      unset GRAFANA_SERVICE_ACCOUNT_TOKEN
    fi
    export GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE="$GRAFANA_TOKEN_FILE"
    MCP_GRAFANA_TOKEN_MODE="token-file"
    return
  fi

  if [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}" ]; then
    printf 'warning: replacing inline GRAFANA_SERVICE_ACCOUNT_TOKEN with refreshed Nebius token\n' >&2
  else
    printf 'warning: mcp-grafana does not advertise GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE support; using refreshed token inline\n' >&2
  fi

  refreshed_token="$(head -n 1 "$GRAFANA_TOKEN_FILE")"
  if [ -z "$refreshed_token" ]; then
    printf 'error: refreshed token file is empty: %s\n' "$GRAFANA_TOKEN_FILE" >&2
    exit 1
  fi

  unset GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE
  export GRAFANA_SERVICE_ACCOUNT_TOKEN="$refreshed_token"
  MCP_GRAFANA_TOKEN_MODE="inline-env"
}

: "${GRAFANA_URL:=https://grafana.nebius.dev/}"
: "${GRAFANA_TOKEN_FILE:=${HOME}/.config/grafana-mcp/token}"
: "${NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS:=36000}"

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

validate_refresh_interval

validate_nebius_grafana_url

case "${1:-}" in
  --print-config)
    printf 'GRAFANA_URL=%s\n' "$GRAFANA_URL"
    printf 'GRAFANA_TOKEN_FILE=%s\n' "$GRAFANA_TOKEN_FILE"
    if command -v mcp-grafana >/dev/null 2>&1 && mcp_supports_token_file; then
      printf 'MCP_GRAFANA_TOKEN_MODE=token-file\n'
      printf 'GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE=%s\n' "$GRAFANA_TOKEN_FILE"
    else
      printf 'MCP_GRAFANA_TOKEN_MODE=inline-env\n'
      printf 'GRAFANA_SERVICE_ACCOUNT_TOKEN=<refreshed from GRAFANA_TOKEN_FILE at startup>\n'
    fi
    printf 'NEBIUS_PROFILE=%s\n' "${NEBIUS_PROFILE:-<default>}"
    printf 'NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=%s\n' "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS"
    exit 0
    ;;
esac

require_command nebius

refresh_token

if [ "${1:-}" = "--refresh-token-only" ]; then
  exit 0
fi

require_command mcp-grafana

export GRAFANA_URL
MCP_GRAFANA_TOKEN_MODE=""
configure_token_environment

if [ "$#" -eq 0 ]; then
  set -- --disable-write
fi

refresher_pid=""
cleanup() {
  if [ -n "$refresher_pid" ]; then
    kill "$refresher_pid" >/dev/null 2>&1 || true
    wait "$refresher_pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [ "$MCP_GRAFANA_TOKEN_MODE" = "token-file" ]; then
  (
    while sleep "$NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS"; do
      if ! refresh_token; then
        printf 'warning: failed to refresh Nebius Grafana token file\n' >&2
      fi
    done
  ) &
  refresher_pid="$!"
else
  printf 'warning: inline token mode cannot refresh credentials inside a running mcp-grafana process; restart Codex before the Nebius token expires\n' >&2
fi

mcp-grafana "$@"
