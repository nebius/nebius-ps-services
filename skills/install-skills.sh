#!/usr/bin/env bash
set -euo pipefail

# Script helper to install Codex skills into ~/.agents/skills.
#
# Usage: ./install-skills.sh [options] [source] [destination_dir]
# Source: local directory (default: script directory) or GitHub URL:
#   - https://github.com/<owner>/<repo>
#   - https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
#
# Requirements: bash, rsync, and git (GitHub sources only).
# How behavior is enforced:
#   - Skill detection: only directories containing SKILL.md are treated as skills.
#   - Idempotency: rsync keeps destination in sync (--delete, --omit-dir-times) and
#     non-meaningful timestamp noise is ignored; reruns converge to "Unchanged".
#   - Source safety: each installed skill writes .install-source-id; existing folders
#     owned by another source (or unmanaged) are skipped, never overwritten.
#   - Scoped cleanup: per-source manifests remove stale skills only when the recorded
#     owner matches the current SOURCE_ID.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SRC_DIR="${SCRIPT_DIR}"

# Color/style output (auto-disabled for non-interactive terminals or NO_COLOR).
S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

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

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

log_warn() {
  printf '%b\n' "${S_YELLOW}${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} ${1}${S_RESET}" >&2
}

require_command() {
  local cmd="$1"
  local reason="$2"

  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "'${cmd}' is required ${reason}."
    log_note "Install '${cmd}' and run again."
    exit 1
  fi
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}[options] [source] [destination_dir]${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Source:${S_RESET}"
  printf '%b\n' "  - Local directory path ${S_DIM}(default: script directory; supports multi-skill or single-skill folder)${S_RESET}"
  printf '%b\n' "  - GitHub URL:"
  printf '%b\n' "      ${S_CYAN}https://github.com/<owner>/<repo>${S_RESET}"
  printf '%b\n' "      ${S_CYAN}https://github.com/<owner>/<repo>/tree/<ref>/<subpath>${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Destination (default):${S_RESET}"
  printf '%b\n' "  ${S_CYAN}~/.agents/skills${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}      Show this help"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh /Users/example/test${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\" \"~/.agents/skills\"${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  - If newly installed skills are not visible, restart extension host manually in VS Code ${S_DIM}(Developer: Restart Extension Host)${S_RESET}."
  printf '%b\n' "  - Existing unmanaged folders in destination are never overwritten."
  printf '%b\n' "  - If a skill exists but belongs to another source, it is skipped."
  printf '%b\n' "  - A valid skill folder must contain ${S_CYAN}SKILL.md${S_RESET}."
}

is_github_source() {
  local value="$1"
  [[ "${value}" =~ ^https://github\.com/[^/]+/[^/]+(/tree/[^/]+/.+)?/?$ ]]
}

hash_string() {
  local value="$1"

  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "${value}" | shasum -a 256 | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${value}" | sha256sum | awk '{print $1}'
    return
  fi

  # Fallback key if hashing tools are unavailable.
  printf '%s' "${value}" | tr '/ :@' '____'
}

read_target_owner() {
  local target_dir="$1"
  local marker="${target_dir}/.install-source-id"

  if [[ -f "${marker}" ]]; then
    cat "${marker}"
    return
  fi

  printf ''
}

extract_meaningful_rsync_changes() {
  local output="$1"

  # Ignore timestamp-only rsync noise from freshly cloned GitHub sources.
  printf '%s\n' "${output}" | awk '
    NF == 0 { next }
    {
      code = $1
      if (code ~ /^\.[^[:space:]]\.\.[tT]\.+$/) {
        next
      }
      print
    }
  '
}

init_output_style

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -*)
      log_error "unknown option: $1"
      show_usage >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "${#POSITIONAL[@]}" -gt 2 ]]; then
  log_error "too many positional arguments."
  show_usage >&2
  exit 1
fi

SOURCE_SPEC="${POSITIONAL[0]:-${DEFAULT_SRC_DIR}}"
DEST_DIR="${POSITIONAL[1]:-${HOME}/.agents/skills}"

require_command "rsync" "for skill synchronization"
if is_github_source "${SOURCE_SPEC}"; then
  require_command "git" "for GitHub sources"
fi

TMP_MANIFEST=""
TMP_CLONE_ROOT=""
RSYNC_COMPARE_FLAGS=()
cleanup() {
  rm -f "${TMP_MANIFEST:-}" >/dev/null 2>&1 || true
  if [[ -n "${TMP_CLONE_ROOT}" && -d "${TMP_CLONE_ROOT}" ]]; then
    rm -rf "${TMP_CLONE_ROOT}"
  fi
}
trap cleanup EXIT

SOURCE_ID=""
if is_github_source "${SOURCE_SPEC}"; then
  source_no_trailing="${SOURCE_SPEC%/}"
  if [[ "${source_no_trailing}" =~ ^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    ref="${BASH_REMATCH[3]}"
    subpath="${BASH_REMATCH[4]}"
  elif [[ "${source_no_trailing}" =~ ^https://github\.com/([^/]+)/([^/]+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    ref="HEAD"
    subpath="."
  else
    log_error "unsupported GitHub source format: ${SOURCE_SPEC}"
    log_warn "Use https://github.com/<owner>/<repo> or /tree/<ref>/<subpath>"
    exit 1
  fi

  repo="${repo%.git}"
  repo_url="https://github.com/${owner}/${repo}.git"
  TMP_CLONE_ROOT="$(mktemp -d)"
  clone_dir="${TMP_CLONE_ROOT}/repo"

  if [[ "${ref}" == "HEAD" ]]; then
    git clone --depth 1 "${repo_url}" "${clone_dir}" >/dev/null
  else
    git clone --depth 1 --branch "${ref}" "${repo_url}" "${clone_dir}" >/dev/null
  fi

  SRC_DIR="${clone_dir}/${subpath}"
  if [[ ! -d "${SRC_DIR}" ]]; then
    log_error "source subpath not found in remote repo: ${subpath}"
    exit 1
  fi
  SRC_DIR="$(cd "${SRC_DIR}" && pwd -P)"
  SOURCE_ID="github:${owner}/${repo}@${ref}:${subpath}"
  # Fresh clones reset mtimes; checksum avoids false-positive updates.
  RSYNC_COMPARE_FLAGS+=(--checksum)
else
  if [[ ! -d "${SOURCE_SPEC}" ]]; then
    log_error "source directory does not exist: ${SOURCE_SPEC}"
    exit 1
  fi

  SRC_DIR="$(cd "${SOURCE_SPEC}" && pwd -P)"
  SOURCE_ID="local:${SRC_DIR}"
fi

mkdir -p "${DEST_DIR}"
DEST_DIR="$(cd "${DEST_DIR}" && pwd -P)"

if [[ "${SRC_DIR}" == "${DEST_DIR}" ]]; then
  log_error "source and destination must be different directories."
  exit 1
fi

SOURCE_KEY="$(hash_string "${SOURCE_ID}")"

STATE_DIR="${DEST_DIR}/.install-skills-state"
SOURCE_MANIFEST="${STATE_DIR}/${SOURCE_KEY}.skills"
TMP_MANIFEST="$(mktemp)"

installed=0
unchanged=0
skipped=0
removed=0

skill_dirs=()
if [[ -f "${SRC_DIR}/SKILL.md" ]]; then
  # Source is a single skill directory.
  skill_dirs+=("${SRC_DIR}")
else
  shopt -s nullglob
  for d in "${SRC_DIR}"/*; do
    [[ -d "${d}" ]] || continue
    skill_dirs+=("${d}")
  done
  shopt -u nullglob
fi

for d in "${skill_dirs[@]}"; do
  [[ -d "${d}" ]] || continue

  name="$(basename "${d}")"
  if [[ ! -f "${d}/SKILL.md" ]]; then
    log_note "Skip (no SKILL.md): ${name}"
    skipped=$((skipped + 1))
    continue
  fi

  target="${DEST_DIR}/${name}"
  marker="${target}/.install-source-id"
  if [[ -d "${target}" ]]; then
    target_owner="$(read_target_owner "${target}")"
    if [[ -n "${target_owner}" ]]; then
      if [[ "${target_owner}" != "${SOURCE_ID}" ]]; then
        log_note "Skip (owned by different source): ${name}"
        skipped=$((skipped + 1))
        continue
      fi
    else
      log_note "Skip (existing unmanaged directory): ${name}"
      skipped=$((skipped + 1))
      continue
    fi
  fi

  mkdir -p "${target}"

  # Sync skill folder and capture whether meaningful changes occurred.
  rsync_output="$(
    rsync -ai --delete --omit-dir-times "${RSYNC_COMPARE_FLAGS[@]}" --exclude ".DS_Store" --exclude ".install-source-id" "${d}/" "${target}/"
  )"
  meaningful_rsync_output="$(extract_meaningful_rsync_changes "${rsync_output}")"

  marker_updated=0
  marker_owner=""
  if [[ -f "${marker}" ]]; then
    marker_owner="$(cat "${marker}" 2>/dev/null || true)"
  fi
  if [[ "${marker_owner}" != "${SOURCE_ID}" ]]; then
    printf '%s\n' "${SOURCE_ID}" > "${marker}"
    marker_updated=1
  fi

  printf '%s\n' "${name}" >> "${TMP_MANIFEST}"

  if [[ -n "${meaningful_rsync_output}" || "${marker_updated}" -eq 1 ]]; then
    log_success "Installed/updated: ${name} -> ${target}"
    installed=$((installed + 1))
  else
    log_note "Unchanged: ${name}"
    unchanged=$((unchanged + 1))
  fi
done

sort -u "${TMP_MANIFEST}" -o "${TMP_MANIFEST}"

# Remove previously installed skills from this source when they no longer exist.
if [[ -f "${SOURCE_MANIFEST}" ]]; then
  while IFS= read -r old_name; do
    [[ -n "${old_name}" ]] || continue
    if ! grep -Fxq -- "${old_name}" "${TMP_MANIFEST}"; then
      old_target="${DEST_DIR}/${old_name}"
      if [[ -d "${old_target}" ]]; then
        old_owner="$(read_target_owner "${old_target}")"
        if [[ "${old_owner}" == "${SOURCE_ID}" ]]; then
          rm -rf "${old_target}"
          log_success "Removed stale skill: ${old_name}"
          removed=$((removed + 1))
        fi
      fi
    fi
  done < "${SOURCE_MANIFEST}"
fi

mkdir -p "${STATE_DIR}"
cp "${TMP_MANIFEST}" "${SOURCE_MANIFEST}"

log_success "Done. Installed/updated: ${installed}, unchanged: ${unchanged}, skipped: ${skipped}, removed: ${removed}"
if [[ $((installed + unchanged + removed)) -eq 0 ]]; then
  log_warn "Warning: no skills were installed from ${SRC_DIR}"
elif [[ $((installed + removed)) -gt 0 ]]; then
  log_note "If new skills are not showing up, run 'Developer: Restart Extension Host' in VS Code."
else
  log_note "No changes detected."
fi
