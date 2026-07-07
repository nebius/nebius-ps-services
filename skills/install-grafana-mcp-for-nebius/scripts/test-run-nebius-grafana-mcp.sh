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

cat >"${tmp_dir}/codex" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "mcp" ] && [ "${2:-}" = "get" ] && [ "${3:-}" = "grafana-nebius" ]; then
  : "${CODEX_EXISTING_WRAPPER:?CODEX_EXISTING_WRAPPER is required}"
  cat <<EOF
grafana-nebius
  enabled: true
  transport: stdio
  command: ${CODEX_EXISTING_WRAPPER}
  args: --disable-write
  cwd: -
  env: GRAFANA_TOKEN_FILE=*****, GRAFANA_URL=*****
  remove: codex mcp remove grafana-nebius
EOF
  exit 0
fi

printf 'unexpected codex args: %s\n' "$*" >&2
exit 2
SH

chmod +x "${tmp_dir}/nebius" "${tmp_dir}/mcp-grafana" "${tmp_dir}/strings" "${tmp_dir}/codex"

capture_file="${tmp_dir}/capture"
token_file="${tmp_dir}/token"

PATH="${tmp_dir}:${PATH}" \
  STRINGS_MODE="inline" \
  GRAFANA_TOKEN_FILE="$token_file" \
  GRAFANA_SERVICE_ACCOUNT_TOKEN="stale-token" \
  MCP_CAPTURE_FILE="$capture_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=36000 \
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
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=36000 \
  "$wrapper" --probe-arg <<<"probe-stdin"

grep -Fxq 'stdin=probe-stdin' "$capture_file"

capture_file="${tmp_dir}/capture-token-file"
token_file="${tmp_dir}/token-file"

PATH="${tmp_dir}:${PATH}" \
  STRINGS_MODE="token-file" \
  GRAFANA_TOKEN_FILE="$token_file" \
  GRAFANA_SERVICE_ACCOUNT_TOKEN="stale-token" \
  MCP_CAPTURE_FILE="$capture_file" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=36000 \
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

equivalent_wrapper="${tmp_dir}/equivalent-wrapper.sh"
cp "$wrapper" "$equivalent_wrapper"
chmod +x "$equivalent_wrapper"

PATH="${tmp_dir}:${PATH}" \
  CODEX_EXISTING_WRAPPER="$wrapper" \
  "$ensure_config" --check --skip-shell --wrapper-path "$wrapper" >/dev/null

if PATH="${tmp_dir}:${PATH}" \
  CODEX_EXISTING_WRAPPER="$equivalent_wrapper" \
  "$ensure_config" --check --skip-shell --wrapper-path "$wrapper" >/dev/null 2>&1; then
  printf 'expected byte-identical wrapper in untrusted path to fail\n' >&2
  exit 1
fi

trusted_home="${tmp_dir}/home"
mkdir -p "$trusted_home"
trusted_home="$(CDPATH='' cd -- "$trusted_home" && pwd -P)"
trusted_repo="${trusted_home}/src/nebius-ps-services"
trusted_wrapper="${trusted_repo}/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh"
mkdir -p "$(dirname -- "$trusted_wrapper")"
cp "$wrapper" "$trusted_wrapper"
chmod +x "$trusted_wrapper"
git -C "$trusted_repo" init -q

HOME="$trusted_home" \
  PATH="${tmp_dir}:${PATH}" \
  CODEX_EXISTING_WRAPPER="$trusted_wrapper" \
  "$ensure_config" --check --skip-shell --wrapper-path "$wrapper" >/dev/null

codex_token_capture="${tmp_dir}/codex-token-output"
PATH="${tmp_dir}:${PATH}" \
  CODEX_EXISTING_WRAPPER="/usr/bin/env GRAFANA_SERVICE_ACCOUNT_TOKEN=secret-value mcp-grafana" \
  "$ensure_config" --check --skip-shell --wrapper-path "$wrapper" >"$codex_token_capture" 2>&1 || true

if grep -Fq 'secret-value' "$codex_token_capture"; then
  printf 'codex config inspection leaked a token-like value\n' >&2
  exit 1
fi

PATH="${tmp_dir}:${PATH}" \
  CODEX_EXISTING_WRAPPER="$wrapper" \
  "$ensure_config" --check --skip-shell --wrapper-path "$wrapper" >/dev/null

if PATH="${tmp_dir}:${PATH}" \
  GRAFANA_TOKEN_FILE="${tmp_dir}/invalid-refresh-token" \
  NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS=0 \
  "$wrapper" --print-config >/dev/null 2>&1; then
  printf 'expected invalid refresh interval to fail\n' >&2
  exit 1
fi

shell_file="${tmp_dir}/zshrc-quoted"
token_with_metacharacters="${tmp_dir}/token \"quoted\" \$(not-run)"

"$ensure_config" \
  --apply \
  --skip-codex \
  --shell-file "$shell_file" \
  --token-file "$token_with_metacharacters" >/dev/null

grep -Fxq 'export GRAFANA_URL="https://grafana.nebius.dev/"' "$shell_file"
grep -Fxq "export GRAFANA_TOKEN_FILE=\"${tmp_dir}/token \\\"quoted\\\" \\\$(not-run)\"" "$shell_file"

if "$ensure_config" \
  --check \
  --skip-codex \
  --shell-file "${tmp_dir}/zshrc-external" \
  --grafana-url "https://external.example/" >/dev/null 2>&1; then
  printf 'expected external Grafana URL in Nebius helper to fail\n' >&2
  exit 1
fi

shell_file="${tmp_dir}/zshrc-secret"
cat >"$shell_file" <<'SH'
export GRAFANA_SERVICE_ACCOUNT_TOKEN="do-not-print"
SH

if "$ensure_config" --check --skip-codex --shell-file "$shell_file" >"${tmp_dir}/secret-check-output" 2>&1; then
  printf 'expected secret-like shell export to fail\n' >&2
  exit 1
fi

if grep -Fq 'do-not-print' "${tmp_dir}/secret-check-output"; then
  printf 'secret-like shell export value was printed\n' >&2
  exit 1
fi

if PATH="${tmp_dir}:${PATH}" \
  GRAFANA_URL="https://external.example/" \
  GRAFANA_TOKEN_FILE="${tmp_dir}/external-token" \
  "$wrapper" --refresh-token-only >/dev/null 2>&1; then
  printf 'expected external GRAFANA_URL to fail before token refresh\n' >&2
  exit 1
fi
