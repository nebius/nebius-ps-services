#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ensure-local-config.sh [--check] [--apply] [options]

Idempotently checks or applies the local Codex/Grafana MCP wiring for
Nebius-managed Grafana. The default is --check and does not modify files.

Options:
  --check                 Inspect only. This is the default.
  --apply                 Update the managed shell block and add a missing
                          Codex MCP server. Existing MCP servers are not
                          removed or replaced.
  --mcp-server-name NAME  Codex MCP server name. Default: grafana-nebius.
  --grafana-url URL       Grafana base URL. Default: https://grafana.nebius.dev/
  --token-file PATH       Token file path. Default:
                          $HOME/.config/grafana-mcp/token.
  --shell-file PATH       Shell startup file. Default: $HOME/.zshrc.
  --wrapper-path PATH     Wrapper script path. Default: this skill's wrapper.
  --skip-shell            Do not inspect or update the shell startup file.
  --skip-codex            Do not inspect or update Codex MCP config.
  -h, --help              Show this help.
EOF
}

mode="check"
skip_shell="false"
skip_codex="false"
mcp_server_name="${MCP_SERVER_NAME:-grafana-nebius}"
grafana_url="${GRAFANA_URL:-https://grafana.nebius.dev/}"
grafana_token_file="${GRAFANA_TOKEN_FILE:-${HOME}/.config/grafana-mcp/token}"
shell_file="${SHELL_FILE:-${HOME}/.zshrc}"
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
wrapper_path="${WRAPPER_PATH:-${script_dir}/run-nebius-grafana-mcp.sh}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      mode="check"
      ;;
    --apply)
      mode="apply"
      ;;
    --mcp-server-name)
      shift
      mcp_server_name="${1:?missing value for --mcp-server-name}"
      ;;
    --grafana-url)
      shift
      grafana_url="${1:?missing value for --grafana-url}"
      ;;
    --token-file)
      shift
      grafana_token_file="${1:?missing value for --token-file}"
      ;;
    --shell-file)
      shift
      shell_file="${1:?missing value for --shell-file}"
      ;;
    --wrapper-path)
      shift
      wrapper_path="${1:?missing value for --wrapper-path}"
      ;;
    --skip-shell)
      skip_shell="true"
      ;;
    --skip-codex)
      skip_codex="true"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

managed_start="# >>> grafana-mcp-for-nebius >>>"
managed_end="# <<< grafana-mcp-for-nebius <<<"
validate_nebius_grafana_url() {
  case "$grafana_url" in
    https://grafana.nebius.dev | https://grafana.nebius.dev/)
      return
      ;;
  esac

  printf 'error: this helper only supports GRAFANA_URL=https://grafana.nebius.dev/ with the Nebius token-refresh wrapper\n' >&2
  printf 'error: configure external Grafana with generic mcp-grafana service-account-token setup instead\n' >&2
  exit 2
}

validate_nebius_grafana_url

reject_shell_export_value() {
  local name="$1"
  local value="$2"

  case "$value" in
    *$'\n'* | *$'\r'*)
      printf 'error: %s must not contain newlines\n' "$name" >&2
      exit 2
      ;;
  esac
}

escape_double_quoted_value() {
  local value="$1"

  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\`/\\\`}"
  value="${value//\$/\\$}"
  printf '%s' "$value"
}

shell_export_value() {
  local home_literal
  local value="$1"
  local suffix

  reject_shell_export_value "shell export value" "$value"

  if [[ "$value" == "${HOME}/"* ]]; then
    home_literal="\$HOME"
    suffix="${value#"${HOME}/"}"
    printf '"%s/%s"' "$home_literal" "$(escape_double_quoted_value "$suffix")"
    return
  fi

  printf '"%s"' "$(escape_double_quoted_value "$value")"
}

desired_url_line="export GRAFANA_URL=$(shell_export_value "$grafana_url")"
desired_token_line="export GRAFANA_TOKEN_FILE=$(shell_export_value "$grafana_token_file")"

build_shell_block() {
  printf '%s\n' "$managed_start"
  printf '%s\n' "$desired_url_line"
  printf '%s\n' "$desired_token_line"
  printf '%s\n' "$managed_end"
}

info() {
  printf '%s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

redact_sensitive_text() {
  printf '%s\n' "$1" \
    | sed -E \
      -e 's/([A-Za-z_]*(TOKEN|Token|token|SECRET|Secret|secret|PASSWORD|Password|password|STATIC_KEY|static_key|KEY|Key|key)[A-Za-z_]*=)[^[:space:]]+/\1*****/g' \
      -e 's/([Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1*****/g' \
      -e 's/([Bb]earer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1*****/g'
}

check_conflicting_shell_exports() {
  local conflicts
  local line_number
  local variable_name

  if [ ! -f "$shell_file" ]; then
    return 0
  fi

  conflicts="$(
    awk \
      -v start="$managed_start" \
      -v end="$managed_end" \
      -v desired_url="$desired_url_line" \
      -v desired_token="$desired_token_line" '
        $0 == start {
          in_block = 1
          next
        }
        in_block {
          if ($0 == end) {
            in_block = 0
          }
          next
        }
        $0 ~ /^export GRAFANA_URL=/ && $0 != desired_url {
          print FNR ":GRAFANA_URL"
        }
        $0 ~ /^export GRAFANA_TOKEN_FILE=/ && $0 != desired_token {
          print FNR ":GRAFANA_TOKEN_FILE"
        }
      ' "$shell_file"
  )"

  if [ -z "$conflicts" ]; then
    return 0
  fi

  while IFS=: read -r line_number variable_name; do
    [ -n "$line_number" ] || continue
    warn "shell: conflicting standalone $variable_name export at $shell_file:$line_number"
  done <<EOF
$conflicts
EOF
  warn "shell: not changing $shell_file automatically; remove the conflicting export or confirm replacement"
  return 1
}

check_secret_shell_exports() {
  local findings
  local line_number
  local variable_name

  if [ ! -f "$shell_file" ]; then
    return 0
  fi

  findings="$(
    awk \
      -v start="$managed_start" \
      -v end="$managed_end" '
        $0 == start {
          in_block = 1
          next
        }
        in_block {
          if ($0 == end) {
            in_block = 0
          }
          next
        }
        $0 ~ /^export[[:space:]]+(GRAFANA_SERVICE_ACCOUNT_TOKEN|GRAFANA_TOKEN|NEBIUS_ACCESS_TOKEN|NEBIUS_IAM_TOKEN|NEBIUS_STATIC_KEY|NEBIUS_STATIC_TOKEN|NEBIUS_OBSERVABILITY_STATIC_TOKEN|AUTHORIZATION)[[:space:]]*=/ {
          line = $0
          sub(/^export[[:space:]]+/, "", line)
          sub(/[[:space:]]*=.*/, "", line)
          print FNR ":" line
        }
      ' "$shell_file"
  )"

  if [ -z "$findings" ]; then
    return 0
  fi

  while IFS=: read -r line_number variable_name; do
    [ -n "$line_number" ] || continue
    warn "shell: secret-like $variable_name export at $shell_file:$line_number; remove it before applying Grafana MCP defaults"
  done <<EOF
$findings
EOF
  warn "shell: not printing secret-like export values"
  return 1
}

managed_shell_block_matches() {
  local existing_block
  local expected_block

  if [ ! -f "$shell_file" ]; then
    return 1
  fi

  existing_block="$(
    awk \
      -v start="$managed_start" \
      -v end="$managed_end" '
        $0 == start {
          in_block = 1
        }
        in_block {
          print
          if ($0 == end) {
            exit
          }
        }
      ' "$shell_file"
  )"
  expected_block="$(build_shell_block)"

  [ "$existing_block" = "$expected_block" ]
}

ensure_shell_block() {
  local tmp_file
  local shell_dir

  shell_dir="$(dirname -- "$shell_file")"
  check_secret_shell_exports
  check_conflicting_shell_exports

  if [ "$mode" = "check" ]; then
    if [ -f "$shell_file" ]; then
      local managed_count
      managed_count="$(grep -Fxc "$managed_start" "$shell_file" || true)"
      if [ "$managed_count" -gt 1 ]; then
        info "shell: multiple managed Grafana MCP blocks found in $shell_file; --apply would consolidate them"
      elif [ "$managed_count" -gt 0 ] && managed_shell_block_matches; then
        info "shell: managed Grafana MCP block present in $shell_file"
      elif [ "$managed_count" -gt 0 ]; then
        info "shell: managed Grafana MCP block differs in $shell_file; --apply would refresh it"
      elif grep -Fxq "$desired_url_line" "$shell_file" \
        && grep -Fxq "$desired_token_line" "$shell_file"; then
        info "shell: desired Grafana exports exist; --apply would normalize them into the managed block"
      else
        info "shell: managed Grafana MCP block missing; --apply would add it to $shell_file"
      fi
    else
      info "shell: $shell_file missing; --apply would create it with the managed block"
    fi
    return
  fi

  mkdir -p "$shell_dir"
  tmp_file="$(mktemp "${shell_file}.tmp.XXXXXX")"

  if [ -f "$shell_file" ]; then
    awk \
      -v start="$managed_start" \
      -v end="$managed_end" \
      -v desired_url="$desired_url_line" \
      -v desired_token="$desired_token_line" '
        function print_block() {
          print start
          print desired_url
          print desired_token
          print end
        }
        $0 == start {
          if (!inserted) {
            print_block()
            inserted = 1
          }
          in_block = 1
          next
        }
        in_block {
          if ($0 == end) {
            in_block = 0
          }
          next
        }
        $0 == desired_url || $0 == desired_token {
          if (!inserted) {
            print_block()
            inserted = 1
          }
          next
        }
        { print }
        END {
          if (!inserted) {
            if (NR > 0) {
              print ""
            }
            print_block()
          }
        }
      ' "$shell_file" > "$tmp_file"
    cat "$tmp_file" > "$shell_file"
  else
    build_shell_block > "$shell_file"
  fi

  rm -f "$tmp_file"
  info "shell: ensured managed Grafana MCP block in $shell_file"
}

check_binary() {
  if command -v mcp-grafana >/dev/null 2>&1; then
    info "binary: mcp-grafana found at $(command -v mcp-grafana)"
  else
    warn "binary: mcp-grafana not found on PATH; install it before starting Codex MCP"
  fi
}

file_mode() {
  stat -f '%OLp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

path_not_group_or_world_writable() {
  local checked_path="$1"
  local group_digit
  local mode
  local numeric_mode
  local other_digit
  local steps

  steps=0
  while [ -n "$checked_path" ] && [ "$checked_path" != "/" ] && [ "$steps" -lt 4 ]; do
    [ -e "$checked_path" ] || return 1
    mode="$(file_mode "$checked_path")"
    numeric_mode=$((10#$mode))
    group_digit=$(((numeric_mode / 10) % 10))
    other_digit=$((numeric_mode % 10))

    if [ $((group_digit & 2)) -ne 0 ] || [ $((other_digit & 2)) -ne 0 ]; then
      return 1
    fi

    checked_path="$(dirname -- "$checked_path")"
    steps=$((steps + 1))
  done
}

trusted_equivalent_wrapper_path() {
  local path="$1"
  local repo_root
  local relative_path

  case "$path" in
    "$HOME/.agents/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh" | \
      "$HOME/.codex/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh")
      path_not_group_or_world_writable "$path"
      ;;
    *)
      case "$path" in
        "$HOME"/*/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh)
          if ! command -v git >/dev/null 2>&1; then
            return 1
          fi
          repo_root="$(git -C "$(dirname -- "$path")" rev-parse --show-toplevel 2>/dev/null)" || return 1
          case "$repo_root" in
            "$HOME"/*)
              relative_path="${path#"${repo_root}/"}"
              [ "$relative_path" = "skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh" ] \
                && path_not_group_or_world_writable "$path"
              ;;
            *)
              return 1
              ;;
          esac
          ;;
        *)
          return 1
          ;;
      esac
      ;;
  esac
}

ensure_codex_mcp() {
  local existing_args
  local existing_command
  local existing_config
  local existing_env
  local mismatch

  if ! command -v codex >/dev/null 2>&1; then
    warn "codex: codex CLI not found on PATH; cannot inspect MCP config"
    return
  fi

  if existing_config="$(codex mcp get "$mcp_server_name" 2>/dev/null)"; then
    mismatch="false"
    existing_command="$(
      printf '%s\n' "$existing_config" \
        | sed -n 's/^  command: //p' \
        | head -n 1
    )"
    existing_args="$(
      printf '%s\n' "$existing_config" \
        | sed -n 's/^  args: //p' \
        | head -n 1
    )"
    existing_env="$(
      printf '%s\n' "$existing_config" \
        | sed -n 's/^  env: //p' \
        | head -n 1
    )"

    info "codex: MCP server '$mcp_server_name' already exists; inspecting sanitized fields"
    info "codex: command: $(redact_sensitive_text "$existing_command")"
    info "codex: args: $(redact_sensitive_text "$existing_args")"
    info "codex: env: $(redact_sensitive_text "$existing_env")"

    if [ "$existing_command" != "$wrapper_path" ]; then
      if [ -n "$existing_command" ] \
        && [ -f "$existing_command" ] \
        && [ -f "$wrapper_path" ] \
        && cmp -s "$existing_command" "$wrapper_path" \
        && trusted_equivalent_wrapper_path "$existing_command"; then
        info "codex: MCP server '$mcp_server_name' uses a byte-identical wrapper at $existing_command"
      elif [ -n "$existing_command" ] \
        && [ -f "$existing_command" ] \
        && [ -f "$wrapper_path" ] \
        && cmp -s "$existing_command" "$wrapper_path"; then
        warn "codex: existing MCP server '$mcp_server_name' uses byte-identical wrapper content from an untrusted path: $existing_command"
        mismatch="true"
      else
        warn "codex: existing MCP server '$mcp_server_name' does not use wrapper: $wrapper_path"
        mismatch="true"
      fi
    fi
    if ! printf '%s\n' "$existing_config" | grep -Fxq "  args: --disable-write" \
      && ! printf '%s\n' "$existing_config" | grep -Fxq "  args: -"; then
      warn "codex: existing MCP server '$mcp_server_name' does not use --disable-write or wrapper defaults"
      mismatch="true"
    fi
    if ! printf '%s\n' "$existing_config" | grep -Fq "GRAFANA_URL=*****"; then
      warn "codex: existing MCP server '$mcp_server_name' is missing GRAFANA_URL"
      mismatch="true"
    fi
    if ! printf '%s\n' "$existing_config" | grep -Fq "GRAFANA_TOKEN_FILE=*****"; then
      warn "codex: existing MCP server '$mcp_server_name' is missing GRAFANA_TOKEN_FILE"
      mismatch="true"
    fi

    if [ "$mismatch" = "true" ]; then
      warn "codex: not replacing existing MCP server without explicit removal/replacement approval"
      return 1
    fi

    info "codex: MCP server '$mcp_server_name' matches the wrapper-based Nebius setup"
    return
  fi

  if [ "$mode" = "check" ]; then
    info "codex: MCP server '$mcp_server_name' missing; --apply would add the wrapper-based server"
    printf 'codex mcp add %q --env %q --env %q -- %q --disable-write\n' \
      "$mcp_server_name" \
      "GRAFANA_URL=${grafana_url}" \
      "GRAFANA_TOKEN_FILE=${grafana_token_file}" \
      "$wrapper_path"
    return
  fi

  codex mcp add "$mcp_server_name" \
    --env "GRAFANA_URL=${grafana_url}" \
    --env "GRAFANA_TOKEN_FILE=${grafana_token_file}" \
    -- "$wrapper_path" --disable-write
  info "codex: added MCP server '$mcp_server_name'"
}

info "mode: $mode"
info "mcp server: $mcp_server_name"
info "grafana url: $grafana_url"
info "token file: $grafana_token_file"
info "wrapper: $wrapper_path"

check_binary

if [ "$skip_shell" = "false" ]; then
  ensure_shell_block
else
  info "shell: skipped"
fi

if [ "$skip_codex" = "false" ]; then
  ensure_codex_mcp
else
  info "codex: skipped"
fi
