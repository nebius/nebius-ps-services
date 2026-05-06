#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_AMBER=""
S_GREEN=""
S_CYAN=""

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_AMBER=$'\033[38;5;214m'
    S_GREEN=$'\033[32m'
    S_CYAN=$'\033[36m'
  fi
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} ${1}${S_RESET}" >&2
}

log_warn() {
  printf '%b\n' "${S_AMBER}${S_BOLD}WARN:${S_RESET}${S_AMBER} ${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}validate-chart.sh${S_RESET} ${S_DIM}[options] <chart-dir>${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}--kube-version <version>${S_RESET}  Also render against a supported Kubernetes version"
  printf '%b\n' "  ${S_CYAN}-h, --help${S_RESET}                 Show help"
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}validate-chart.sh charts/my-app${S_RESET}"
  printf '%b\n' "  ${S_CYAN}validate-chart.sh --kube-version 1.30.0 charts/my-app${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

chart_has_dependencies() {
  local chart_yaml="$1"
  grep -Eq '^[[:space:]]*dependencies:[[:space:]]*$' "${chart_yaml}"
}

resolve_dependencies() {
  local chart="$1"

  if ! chart_has_dependencies "${chart}/Chart.yaml"; then
    log_note "No chart dependencies declared."
    return
  fi

  if [[ -f "${chart}/Chart.lock" ]]; then
    log_note "Resolving dependencies from Chart.lock."
    helm dependency build "${chart}"
  else
    log_note "Resolving dependencies from Chart.yaml and writing Chart.lock."
    helm dependency update "${chart}"
  fi
}

main() {
  init_output_style

  local chart=""
  local kube_version=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        show_usage
        exit 0
        ;;
      --kube-version)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "Missing value for --kube-version."
          show_usage >&2
          exit 1
        fi
        kube_version="$2"
        shift 2
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
        if [[ -n "${chart}" ]]; then
          log_error "Unexpected extra argument: $1"
          show_usage >&2
          exit 1
        fi
        chart="${1%/}"
        shift
        ;;
    esac
  done

  if [[ -z "${chart}" && $# -gt 0 ]]; then
    chart="${1%/}"
    shift
  fi

  if [[ $# -gt 0 ]]; then
    log_error "Unexpected extra argument: $1"
    show_usage >&2
    exit 1
  fi

  if [[ -z "${chart}" ]]; then
    log_error "Missing chart directory."
    show_usage >&2
    exit 1
  fi

  if [[ ! -d "${chart}" ]]; then
    log_error "Chart directory does not exist: ${chart}"
    exit 1
  fi

  if [[ ! -f "${chart}/Chart.yaml" ]]; then
    log_error "Chart.yaml not found in: ${chart}"
    exit 1
  fi

  require_cmd helm
  require_cmd grep

  resolve_dependencies "${chart}"

  log_note "Running helm lint --strict."
  helm lint "${chart}" --strict

  log_note "Rendering default manifests with --debug."
  helm template smoke "${chart}" --debug >/dev/null

  if [[ -n "${kube_version}" ]]; then
    log_note "Rendering default manifests for Kubernetes ${kube_version}."
    helm template smoke "${chart}" --kube-version "${kube_version}" >/dev/null
  fi

  log_note "Rendering optional ServiceMonitor sample as non-fatal smoke coverage."
  if ! helm template smoke "${chart}" \
    --set monitoring.serviceMonitor.enabled=true \
    --api-versions monitoring.coreos.com/v1/ServiceMonitor \
    >/dev/null; then
    log_warn "ServiceMonitor smoke render failed; keep this non-fatal unless the chart declares that feature as supported."
  fi

  log_success "Helm chart validation completed."
}

main "$@"
