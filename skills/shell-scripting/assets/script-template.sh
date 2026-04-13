#!/usr/bin/env bash
set -euo pipefail

# Template Bash script with rich usage output and structured logging.

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_AMBER=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_AMBER=$'\033[38;5;214m'
    S_YELLOW=$'\033[33m'
    S_GREEN=$'\033[32m'
    S_CYAN=$'\033[36m'
  fi
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} ${1}${S_RESET}" >&2
}

log_warn() {
  printf '%b\n' "${S_AMBER}${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./script.sh${S_RESET} ${S_DIM}[options] <required_arg>${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}          Show help"
  printf '%b\n' "  ${S_YELLOW}--dry-run${S_RESET}           Print actions without applying changes"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./script.sh value${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./script.sh --dry-run value${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

main() {
  init_output_style

  local dry_run=0
  local required_arg=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        show_usage
        exit 0
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      --)
        shift
        break
        ;;
      -*)
        log_error "Unknown option: $1"
        show_usage >&2
        exit 1
        ;;
      *)
        required_arg="$1"
        shift
        break
        ;;
    esac
  done

  if [[ -z "${required_arg}" ]]; then
    log_error "Missing required argument."
    show_usage >&2
    exit 1
  fi

  # Example dependency check.
  require_cmd "bash"

  if [[ "${dry_run}" -eq 1 ]]; then
    log_note "Dry-run: would process '${required_arg}'"
  else
    # Replace with real logic.
    log_success "Processed '${required_arg}' successfully."
  fi
}

main "$@"
