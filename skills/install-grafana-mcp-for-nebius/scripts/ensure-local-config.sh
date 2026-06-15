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
shell_token_file="$grafana_token_file"

if [[ "$shell_token_file" == "${HOME}/"* ]]; then
  shell_token_file="\$HOME/${shell_token_file#"${HOME}/"}"
fi

desired_url_line="export GRAFANA_URL=\"${grafana_url}\""
desired_token_line="export GRAFANA_TOKEN_FILE=\"${shell_token_file}\""

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

ensure_shell_block() {
  local tmp_file
  local shell_dir

  shell_dir="$(dirname -- "$shell_file")"
  check_conflicting_shell_exports

  if [ "$mode" = "check" ]; then
    if [ -f "$shell_file" ]; then
      local managed_count
      managed_count="$(grep -Fxc "$managed_start" "$shell_file" || true)"
      if [ "$managed_count" -gt 0 ]; then
        info "shell: managed Grafana MCP block present in $shell_file"
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

ensure_codex_mcp() {
  local existing_config
  local mismatch

  if ! command -v codex >/dev/null 2>&1; then
    warn "codex: codex CLI not found on PATH; cannot inspect MCP config"
    return
  fi

  if existing_config="$(codex mcp get "$mcp_server_name" 2>/dev/null)"; then
    info "codex: MCP server '$mcp_server_name' already exists; inspecting it"
    printf '%s\n' "$existing_config"

    mismatch="false"
    if ! printf '%s\n' "$existing_config" | grep -Fxq "  command: $wrapper_path"; then
      warn "codex: existing MCP server '$mcp_server_name' does not use wrapper: $wrapper_path"
      mismatch="true"
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
