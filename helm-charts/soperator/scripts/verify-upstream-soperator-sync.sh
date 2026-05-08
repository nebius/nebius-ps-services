#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_AMBER=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""
TMP_DIR=""

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

cleanup() {
  if [[ -n "${TMP_DIR:-}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--lock-file PATH${S_RESET}    Lock file to read"
  printf '%b\n' "  ${S_YELLOW}--check-latest${S_RESET}      Fail if GitHub latest release is newer than the lock"
  printf '%b\n' "  ${S_YELLOW}--sync${S_RESET}              Replace local upstream-owned scripts from the locked release"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}          Show help"
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --check-latest${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --sync${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

script_dir() {
  local source="${BASH_SOURCE[0]}"
  while [[ -L "${source}" ]]; do
    local dir
    dir="$(cd -P "$(dirname "${source}")" >/dev/null 2>&1 && pwd)"
    source="$(readlink "${source}")"
    [[ "${source}" != /* ]] && source="${dir}/${source}"
  done
  cd -P "$(dirname "${source}")" >/dev/null 2>&1 && pwd
}

yaml_value() {
  local file="$1"
  local key="$2"
  awk -F ':' -v key="${key}" '
    $1 == key {
      value = substr($0, index($0, ":") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "${file}"
}

first_yaml_value() {
  local file="$1"
  local key="$2"
  awk -F ':' -v key="${key}" '
    $1 ~ "^[[:space:]]*" key "$" {
      value = substr($0, index($0, ":") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "${file}"
}

local_owned_paths() {
  local file="$1"
  awk '
    /^[[:space:]]*local_owned_paths:[[:space:]]*$/ {
      in_list = 1
      next
    }
    in_list && /^[^[:space:]]/ {
      exit
    }
    in_list && /^[[:space:]]*-[[:space:]]*/ {
      value = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      print value
    }
  ' "${file}"
}

normalize_rel_path() {
  local path="$1"
  path="${path#./}"
  path="${path%/}"
  printf '%s\n' "${path}"
}

validate_sync_target() {
  local lock_file="$1"
  local local_path="$2"
  local target owned owned_normalized

  if [[ "${local_path}" == "." || "${local_path}" == /* || "${local_path}" == *".."* ]]; then
    log_error "Unsafe sync target in lock file: ${local_path}"
    exit 1
  fi

  target="$(normalize_rel_path "${local_path}")"
  while IFS= read -r owned; do
    [[ -z "${owned}" ]] && continue
    owned_normalized="$(normalize_rel_path "${owned}")"
    if [[ "${target}" == "${owned_normalized}" || "${target}" == "${owned_normalized}/"* ]]; then
      log_error "Lock file sync target '${local_path}' overlaps local-owned path '${owned}'."
      exit 1
    fi
  done < <(local_owned_paths "${lock_file}")
}

resolve_tag_commit() {
  local repo="$1"
  local tag="$2"
  local refs peeled direct

  refs="$(git ls-remote --tags "${repo}" "refs/tags/${tag}" "refs/tags/${tag}^{}")"
  peeled="$(printf '%s\n' "${refs}" | awk '$2 ~ /\^\{\}$/ { print $1; exit }')"
  direct="$(printf '%s\n' "${refs}" | awk '$2 !~ /\^\{\}$/ { print $1; exit }')"

  if [[ -n "${peeled}" ]]; then
    printf '%s\n' "${peeled}"
  elif [[ -n "${direct}" ]]; then
    printf '%s\n' "${direct}"
  else
    log_error "Could not resolve tag '${tag}' in ${repo}"
    exit 1
  fi
}

latest_release_tag() {
  local repo="$1"
  local api_repo
  api_repo="${repo#https://github.com/}"
  api_repo="${api_repo%.git}"
  curl -fsSL "https://api.github.com/repos/${api_repo}/releases/latest" \
    | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1
}

fetch_release() {
  local repo="$1"
  local tag="$2"
  local destination="$3"
  local archive="${destination}/source.tar.gz"
  local url="${repo%/}/archive/refs/tags/${tag}.tar.gz"

  mkdir -p "${destination}/source"
  curl -fsSL "${url}" -o "${archive}"
  tar -xzf "${archive}" -C "${destination}/source" --strip-components=1
}

validate_chart_versions() {
  local chart_file="$1"
  local release="$2"
  local expected_chart_version="$3"
  local chart_version app_version

  chart_version="$(yaml_value "${chart_file}" "version")"
  app_version="$(yaml_value "${chart_file}" "appVersion")"

  if [[ "${app_version}" != "${release}" ]]; then
    log_error "Chart.yaml appVersion '${app_version}' does not match upstream release '${release}'."
    exit 1
  fi

  if [[ "${chart_version}" != "${expected_chart_version}" ]]; then
    log_error "Chart.yaml version '${chart_version}' does not match lock chart_version '${expected_chart_version}'."
    exit 1
  fi

  if [[ "${chart_version}" != "${release}" && "${chart_version}" != "${release}-"* && "${chart_version}" != "${release}+"* ]]; then
    log_error "Chart.yaml version '${chart_version}' must use upstream release '${release}' as its base."
    exit 1
  fi
}

sync_scripts() {
  local source_dir="$1"
  local target_dir="$2"

  rm -rf "${target_dir}"
  mkdir -p "${target_dir}"
  cp -R "${source_dir}/." "${target_dir}/"
}

main() {
  init_output_style

  local check_latest=0
  local sync=0
  local chart_dir
  chart_dir="$(cd "$(script_dir)/.." >/dev/null 2>&1 && pwd)"
  local lock_file="${chart_dir}/upstream-soperator.lock.yaml"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lock-file)
        if [[ $# -lt 2 ]]; then
          log_error "--lock-file requires a path."
          exit 1
        fi
        lock_file="$2"
        shift 2
        ;;
      --check-latest)
        check_latest=1
        shift
        ;;
      --sync)
        sync=1
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
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
        log_error "Unexpected argument: $1"
        show_usage >&2
        exit 1
        ;;
    esac
  done

  require_cmd "awk"
  require_cmd "curl"
  require_cmd "diff"
  require_cmd "git"
  require_cmd "mktemp"
  require_cmd "tar"

  if [[ ! -f "${lock_file}" ]]; then
    log_error "Lock file not found: ${lock_file}"
    exit 1
  fi

  local repo release tag commit expected_chart_version upstream_path local_path mode
  repo="$(yaml_value "${lock_file}" "repository")"
  release="$(yaml_value "${lock_file}" "release")"
  tag="$(yaml_value "${lock_file}" "tag")"
  commit="$(yaml_value "${lock_file}" "commit")"
  expected_chart_version="$(yaml_value "${lock_file}" "chart_version")"
  upstream_path="$(first_yaml_value "${lock_file}" "upstream_path")"
  local_path="$(first_yaml_value "${lock_file}" "local_path")"
  mode="$(first_yaml_value "${lock_file}" "mode")"

  if [[ -z "${repo}" || -z "${release}" || -z "${tag}" || -z "${commit}" || -z "${upstream_path}" || -z "${local_path}" ]]; then
    log_error "Lock file is missing repository, release, tag, commit, upstream_path, or local_path."
    exit 1
  fi

  if [[ "${mode}" != "exact" ]]; then
    log_error "Unsupported sync mode '${mode}'. Only 'exact' is supported."
    exit 1
  fi

  validate_sync_target "${lock_file}" "${local_path}"
  validate_chart_versions "${chart_dir}/Chart.yaml" "${release}" "${expected_chart_version}"

  local resolved_commit
  resolved_commit="$(resolve_tag_commit "${repo}" "${tag}")"
  if [[ "${resolved_commit}" != "${commit}" ]]; then
    log_error "Tag '${tag}' resolves to '${resolved_commit}', but lock expects '${commit}'."
    exit 1
  fi

  if [[ "${check_latest}" -eq 1 ]]; then
    local latest
    latest="$(latest_release_tag "${repo}")"
    if [[ -z "${latest}" ]]; then
      log_error "Could not determine latest GitHub release for ${repo}."
      exit 1
    fi
    if [[ "${latest}" != "${release}" ]]; then
      log_error "Latest Soperator release is '${latest}', but lock is pinned to '${release}'. Run this script after updating the lock and syncing scripts."
      exit 1
    fi
    log_success "Pinned Soperator release '${release}' is the latest GitHub release."
  fi

  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  log_note "Fetching ${repo} release ${tag}..."
  fetch_release "${repo}" "${tag}" "${TMP_DIR}"

  local upstream_scripts="${TMP_DIR}/source/${upstream_path}"
  local local_scripts="${chart_dir}/${local_path}"
  if [[ ! -d "${upstream_scripts}" ]]; then
    log_error "Upstream path not found in release archive: ${upstream_path}"
    exit 1
  fi

  if [[ "${sync}" -eq 1 ]]; then
    log_warn "Syncing ${local_path} from upstream release ${tag}."
    sync_scripts "${upstream_scripts}" "${local_scripts}"
  fi

  if ! diff -ruN "${upstream_scripts}/" "${local_scripts}/"; then
    log_error "Local ${local_path} does not exactly match ${repo} ${tag}:${upstream_path}."
    log_error "Update the lock/release intentionally, or run with --sync after choosing the release."
    exit 1
  fi

  log_success "Local ${local_path} matches ${repo} release ${release} (${commit})."
}

main "$@"
