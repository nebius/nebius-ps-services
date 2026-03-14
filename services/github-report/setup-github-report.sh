#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

PROJECT_NAME="github-report"
PROJECT_DIST="github_report"
REPO_OWNER="nebius"
REPO_NAME="nebius-ps-services"
INSTALL_ROOT="${GITHUB_REPORT_INSTALL_ROOT:-${HOME}/.local/share/github-report}"
VENV_DIR="${INSTALL_ROOT}/venv"
BIN_DIR="${GITHUB_REPORT_BIN_DIR:-${HOME}/.local/bin}"
DOCS_URL="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens"
TOKEN_WIZARD_URL="https://github.com/settings/personal-access-tokens/new?name=github-report&description=Read+access+for+github-report&target_name=nebius&contents=read&metadata=read"
TMP_DIR=""
OS_FAMILY=""
DRY_RUN=0
PYTHON_CMD=""
GITHUB_API_TOKEN=""
TOKEN_MISSING=0
SOURCE_KIND=""
SOURCE_REF=""
SOURCE_URL=""
SOURCE_FILE=""
WHEEL_PATH=""

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_YELLOW=$'\033[33m'
    S_GREEN=$'\033[32m'
    S_CYAN=$'\033[36m'
  fi
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} $1${S_RESET}" >&2
}

log_warn() {
  printf '%b\n' "${S_YELLOW}$1${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}$1${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}$1${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./setup-github-report.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}          Show help"
  printf '%b\n' "  ${S_YELLOW}--dry-run${S_RESET}           Print actions without downloading or installing"
  printf '\n'
  printf '%b\n' "${S_BOLD}What it does:${S_RESET}"
  printf '%b\n' "  - Checks macOS/Linux prerequisites and installs missing ones when supported"
  printf '%b\n' "  - Verifies ${S_CYAN}GITHUB_TOKEN${S_RESET} or ${S_CYAN}GH_TOKEN${S_RESET}"
  printf '%b\n' "  - Downloads the latest published ${PROJECT_NAME} wheel from GitHub Releases"
  printf '%b\n' "  - Installs ${PROJECT_NAME} into ${S_CYAN}${VENV_DIR}${S_RESET}"
  printf '%b\n' "  - Creates ${S_CYAN}${BIN_DIR}/github-report${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./setup-github-report.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./setup-github-report.sh --dry-run${S_RESET}"
}

cleanup() {
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}

trap cleanup EXIT

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

format_cmd() {
  local rendered=""
  printf -v rendered '%q ' "$@"
  printf '%s' "${rendered% }"
}

run_cmd() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_cmd "$@")"
    return 0
  fi
  "$@"
}

run_privileged_cmd() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: sudo $(format_cmd "$@")"
    return 0
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
    return 0
  fi
  if ! have_cmd sudo; then
    log_error "sudo is required to install missing system packages."
    exit 1
  fi
  sudo "$@"
}

detect_os() {
  case "$(uname -s)" in
    Darwin)
      OS_FAMILY="macos"
      ;;
    Linux)
      OS_FAMILY="linux"
      ;;
    *)
      log_error "Unsupported operating system: $(uname -s)"
      exit 1
      ;;
  esac
}

python_meets_minimum() {
  local candidate="$1"
  "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

find_python_cmd() {
  local candidate
  for candidate in python3 python3.12 python3.11; do
    if have_cmd "${candidate}" && python_meets_minimum "${candidate}"; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  return 1
}

ensure_homebrew() {
  if have_cmd brew; then
    return 0
  fi
  if ! have_cmd curl; then
    log_error "curl is required to bootstrap Homebrew on macOS."
    exit 1
  fi
  log_note "Installing Homebrew because it is required to install Python 3.12."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: install Homebrew from the official script."
    return 0
  fi
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
}

install_macos_dependencies() {
  local packages=()
  ensure_homebrew
  if ! find_python_cmd >/dev/null 2>&1; then
    packages+=("python@3.12")
  fi
  if ! have_cmd curl; then
    packages+=("curl")
  fi
  if [[ "${#packages[@]}" -eq 0 ]]; then
    return 0
  fi
  log_note "Installing required macOS packages with Homebrew: ${packages[*]}"
  run_cmd brew install "${packages[@]}"
}

install_linux_dependencies() {
  if have_cmd apt-get; then
    log_note "Installing required Linux packages with apt-get."
    run_privileged_cmd apt-get update
    run_privileged_cmd apt-get install -y curl tar python3 python3-venv || true
    if ! find_python_cmd >/dev/null 2>&1; then
      run_privileged_cmd apt-get install -y python3.12 python3.12-venv || \
        run_privileged_cmd apt-get install -y python3.11 python3.11-venv
    fi
    return 0
  fi
  if have_cmd dnf; then
    log_note "Installing required Linux packages with dnf."
    run_privileged_cmd dnf install -y curl tar python3 python3-pip
    if ! find_python_cmd >/dev/null 2>&1; then
      run_privileged_cmd dnf install -y python3.12 || \
        run_privileged_cmd dnf install -y python3.11
    fi
    return 0
  fi
  if have_cmd yum; then
    log_note "Installing required Linux packages with yum."
    run_privileged_cmd yum install -y curl tar python3 python3-pip
    if ! find_python_cmd >/dev/null 2>&1; then
      run_privileged_cmd yum install -y python3.11 || true
    fi
    return 0
  fi
  log_error "Unsupported Linux package manager. Install curl, tar, and Python 3.11+ manually."
  exit 1
}

ensure_prerequisites() {
  detect_os
  case "${OS_FAMILY}" in
    macos)
      install_macos_dependencies
      ;;
    linux)
      install_linux_dependencies
      ;;
  esac

  if ! have_cmd curl; then
    log_error "curl is still missing after the dependency step."
    exit 1
  fi
  if ! have_cmd tar; then
    log_error "tar is still missing after the dependency step."
    exit 1
  fi

  if ! PYTHON_CMD="$(find_python_cmd)"; then
    log_error "Python 3.11 or newer is required, but no supported interpreter was found."
    exit 1
  fi

  if ! "${PYTHON_CMD}" -m venv --help >/dev/null 2>&1; then
    log_error "The detected Python does not provide venv support."
    exit 1
  fi

  log_success "Using Python interpreter: ${PYTHON_CMD}"
}

detect_shell_rc() {
  case "${SHELL##*/}" in
    zsh)
      printf '%s' "${HOME}/.zshrc"
      ;;
    bash)
      printf '%s' "${HOME}/.bashrc"
      ;;
    *)
      return 1
      ;;
  esac
}

show_token_guidance() {
  local shell_rc=""
  shell_rc="$(detect_shell_rc || true)"
  log_error "GITHUB_TOKEN or GH_TOKEN is not set."
  printf '\n'
  log_note "Create a GitHub personal access token here:"
  printf '  %s\n' "${DOCS_URL}"
  printf '  %s\n' "${TOKEN_WIZARD_URL}"
  printf '\n'
  log_note "Recommended minimum access:"
  printf '  - Resource owner: %s\n' "${REPO_OWNER}"
  printf '  - Repository access: %s/%s\n' "${REPO_OWNER}" "${REPO_NAME}"
  printf '  - Contents: Read-only\n'
  printf '  - Metadata: Read-only\n'
  printf '\n'
  if [[ -n "${shell_rc}" ]]; then
    log_note "Set the token for future terminal sessions:"
    printf "  printf 'export GITHUB_TOKEN=YOUR_TOKEN_HERE\\n' >> %s\n" "${shell_rc}"
    printf "  source %s\n" "${shell_rc}"
  else
    log_note "Export it in your current shell before re-running the script:"
    printf '  export GITHUB_TOKEN=YOUR_TOKEN_HERE\n'
  fi
}

ensure_github_token() {
  GITHUB_API_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
  if [[ -n "${GITHUB_API_TOKEN}" ]]; then
    return 0
  fi
  TOKEN_MISSING=1
  show_token_guidance
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return 0
  fi
  exit 1
}

resolve_source() {
  local metadata
  metadata="$(
    REPO_OWNER="${REPO_OWNER}" \
    REPO_NAME="${REPO_NAME}" \
    PROJECT_DIST="${PROJECT_DIST}" \
    GITHUB_API_TOKEN="${GITHUB_API_TOKEN}" \
    "${PYTHON_CMD}" - <<'PY'
import json
import os
import urllib.error
import urllib.request

repo_owner = os.environ["REPO_OWNER"]
repo_name = os.environ["REPO_NAME"]
project_dist = os.environ["PROJECT_DIST"]
token = os.environ["GITHUB_API_TOKEN"]

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

def get_json(url: str):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response:
        return json.load(response)

releases_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases?per_page=20"
try:
    releases = get_json(releases_url)
except urllib.error.HTTPError as exc:
    print(
        "ERROR="
        f"GitHub API returned HTTP {exc.code} for {repo_owner}/{repo_name} releases. "
        "Verify that your token can read repository contents and metadata."
    )
    raise SystemExit(0)
except urllib.error.URLError as exc:
    print(f"ERROR=Failed to reach GitHub while checking releases: {exc.reason}")
    raise SystemExit(0)

for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue
    for asset in release.get("assets") or []:
        name = asset.get("name", "")
        if name.startswith(f"{project_dist}-") and name.endswith(".whl"):
            print("SOURCE_KIND=release_asset")
            print(f"SOURCE_REF={release.get('tag_name') or ''}")
            print(f"SOURCE_URL={asset['url']}")
            print(f"SOURCE_FILE={name}")
            raise SystemExit(0)

print(
    "ERROR="
    f"No published {project_dist}-*.whl release asset was found for {repo_owner}/{repo_name}. "
    "Publish a GitHub Release before using this installer."
)
PY
  )"

  local resolve_error=""
  while IFS='=' read -r key value; do
    case "${key}" in
      ERROR) resolve_error="${value}" ;;
      SOURCE_KIND) SOURCE_KIND="${value}" ;;
      SOURCE_REF) SOURCE_REF="${value}" ;;
      SOURCE_URL) SOURCE_URL="${value}" ;;
      SOURCE_FILE) SOURCE_FILE="${value}" ;;
    esac
  done <<< "${metadata}"

  if [[ -n "${resolve_error}" ]]; then
    log_error "${resolve_error}"
    exit 1
  fi

  if [[ -z "${SOURCE_KIND}" || -z "${SOURCE_REF}" ]]; then
    log_error "Failed to determine which ${PROJECT_NAME} source to install."
    exit 1
  fi

  log_success "Using latest published wheel asset from release ${SOURCE_REF}."
}

prepare_install_dirs() {
  run_cmd mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}"
  if [[ ! -d "${VENV_DIR}" ]]; then
    log_note "Creating virtual environment at ${VENV_DIR}"
    run_cmd "${PYTHON_CMD}" -m venv "${VENV_DIR}"
  fi
}

venv_python() {
  printf '%s' "${VENV_DIR}/bin/python"
}

download_wheel() {
  TMP_DIR="$(mktemp -d -t github-report-setup.XXXXXX)"
  WHEEL_PATH="${TMP_DIR}/${SOURCE_FILE}"
  log_note "Downloading release wheel ${SOURCE_FILE}"
  run_cmd curl -fsSL \
    -H "Accept: application/octet-stream" \
    -H "Authorization: Bearer ${GITHUB_API_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -o "${WHEEL_PATH}" \
    "${SOURCE_URL}"
}

install_wheel() {
  local venv_py
  local launcher_target
  venv_py="$(venv_python)"
  launcher_target="${VENV_DIR}/bin/github-report"

  run_cmd "${venv_py}" -m pip install --upgrade pip
  run_cmd "${venv_py}" -m pip install --force-reinstall "${WHEEL_PATH}"
  run_cmd ln -sfn "${launcher_target}" "${BIN_DIR}/github-report"

  log_success "Installed ${PROJECT_NAME} into ${VENV_DIR}"
  log_success "Launcher created at ${BIN_DIR}/github-report"

  if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
    log_warn "${BIN_DIR} is not on your PATH."
    if [[ -n "$(detect_shell_rc || true)" ]]; then
      printf "Add this to your shell profile: export PATH=\"%s:\$PATH\"\n" "${BIN_DIR}"
    fi
  fi

  printf '\n'
  log_note "Next steps:"
  printf "  1. Run %s\n" "$(format_cmd "${BIN_DIR}/github-report" --help)"
  printf "  2. Run %s\n" "$(format_cmd "${BIN_DIR}/github-report" top-users --top 10)"
}

main() {
  init_output_style

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        show_usage
        exit 0
        ;;
      --dry-run)
        DRY_RUN=1
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
        log_error "Unexpected argument: $1"
        show_usage >&2
        exit 1
        ;;
    esac
  done

  ensure_prerequisites
  ensure_github_token
  if [[ "${DRY_RUN}" -eq 1 && "${TOKEN_MISSING}" -eq 1 ]]; then
    log_note "Dry-run complete. Release lookup was skipped because no GitHub token is configured."
    exit 0
  fi
  resolve_source
  prepare_install_dirs
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run complete. No files were downloaded or installed."
    exit 0
  fi
  download_wheel
  install_wheel
}

main "$@"
