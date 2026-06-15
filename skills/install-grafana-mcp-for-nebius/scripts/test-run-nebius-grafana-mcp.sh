#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
wrapper="${script_dir}/run-nebius-grafana-mcp.sh"
ensure_config="${script_dir}/ensure-local-config.sh"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

cat >"${tmp_dir}/nebius" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "--profile" ]; then
  shift 2
fi

if [ "${1:-}" = "iam" ] && [ "${2:-}" = "get-access-token" ]; then
  printf 'fake-nebius-token\n'
  exit 0
fi

printf 'unexpected nebius args: %s\n' "$*" >&2
exit 2
SH

cat >"${tmp_dir}/mcp-grafana" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

: "${MCP_CAPTURE_FILE:?MCP_CAPTURE_FILE is required}"

{
  printf 'token=%s\n' "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}"
  printf 'token_file=%s\n' "${GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE:-}"
  printf 'args=%s\n' "$*"
  if [ "${MCP_CAPTURE_STDIN:-}" = "1" ]; then
    if IFS= read -r line; then
      printf 'stdin=%s\n' "$line"
    else
      printf 'stdin=<eof>\n'
    fi
  fi
} >"$MCP_CAPTURE_FILE"
SH

cat >"${tmp_dir}/strings" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ "${STRINGS_MODE:-}" = "token-file" ]; then
  printf 'GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE\n'
fi
SH

chmod +x "${tmp_dir}/nebius" "${tmp_dir}/mcp-grafana" "${tmp_dir}/strings"

capture_file="${tmp_dir}/capture"
token_file="${tmp_dir}/token"

PATH="${tmp_dir}:${PATH}" \
  STRINGS_MODE="inline" \
  GRAFANA_TOKEN_FILE="$token_file" \
  GRAFANA_SERVICE_ACCOUNT_TOKEN="stale-token" \
  MCP_CAPTURE_FILE="$capture_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=99999 \
  "$wrapper" --probe-arg

grep -Fxq 'token=fake-nebius-token' "$capture_file"
grep -Fxq 'token_file=' "$capture_file"
grep -Fxq 'args=--probe-arg' "$capture_file"
test "$(stat -f '%OLp' "$token_file" 2>/dev/null || stat -c '%a' "$token_file")" = "600"

cwd_dir="${tmp_dir}/cwd-mode"
mkdir "$cwd_dir"
chmod 755 "$cwd_dir"
cwd_mode_before="$(stat -f '%OLp' "$cwd_dir" 2>/dev/null || stat -c '%a' "$cwd_dir")"

(
  cd "$cwd_dir"
  PATH="${tmp_dir}:${PATH}" \
    GRAFANA_TOKEN_FILE="token" \
    "$wrapper" --refresh-token-only
)

cwd_mode_after="$(stat -f '%OLp' "$cwd_dir" 2>/dev/null || stat -c '%a' "$cwd_dir")"
test "$cwd_mode_after" = "$cwd_mode_before"
test "$(stat -f '%OLp' "${cwd_dir}/token" 2>/dev/null || stat -c '%a' "${cwd_dir}/token")" = "600"

capture_file="${tmp_dir}/capture-stdin"
token_file="${tmp_dir}/token-stdin"

PATH="${tmp_dir}:${PATH}" \
  STRINGS_MODE="inline" \
  GRAFANA_TOKEN_FILE="$token_file" \
  MCP_CAPTURE_FILE="$capture_file" \
  MCP_CAPTURE_STDIN=1 \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=99999 \
  "$wrapper" --probe-arg <<<"probe-stdin"

grep -Fxq 'stdin=probe-stdin' "$capture_file"

capture_file="${tmp_dir}/capture-token-file"
token_file="${tmp_dir}/token-file"

PATH="${tmp_dir}:${PATH}" \
  STRINGS_MODE="token-file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  GRAFANA_SERVICE_ACCOUNT_TOKEN="stale-token" \
  MCP_CAPTURE_FILE="$capture_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=99999 \
  "$wrapper" --probe-arg

grep -Fxq 'token=' "$capture_file"
grep -Fxq "token_file=$token_file" "$capture_file"
grep -Fxq 'args=--probe-arg' "$capture_file"
test "$(stat -f '%OLp' "$token_file" 2>/dev/null || stat -c '%a' "$token_file")" = "600"

shell_file="${tmp_dir}/zshrc-conflict"
cat >"$shell_file" <<'SH'
export GRAFANA_URL="https://custom.example/"
export GRAFANA_TOKEN_FILE="$HOME/custom-token"
SH

if "$ensure_config" --check --skip-codex --shell-file "$shell_file" >/dev/null 2>&1; then
  printf 'expected --check to fail on conflicting shell exports\n' >&2
  exit 1
fi

if "$ensure_config" --apply --skip-codex --shell-file "$shell_file" >/dev/null 2>&1; then
  printf 'expected --apply to fail on conflicting shell exports\n' >&2
  exit 1
fi

if grep -Fq '# >>> grafana-mcp-for-nebius >>>' "$shell_file"; then
  printf 'conflicting shell file was modified unexpectedly\n' >&2
  exit 1
fi
