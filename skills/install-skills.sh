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
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--remove-skill <skill_name> [destination_dir]${S_RESET}"
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
  printf '%b\n' "  ${S_YELLOW}--remove-skill${S_RESET}  Remove one skill by its visible Codex skill name or folder name"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh /Users/example/test${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\" \"~/.agents/skills\"${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --remove-skill nebius${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  - If newly installed skills are not visible, restart extension host manually in VS Code ${S_DIM}(Developer: Restart Extension Host)${S_RESET}."
  printf '%b\n' "  - Existing unmanaged folders in destination are never overwritten."
  printf '%b\n' "  - If a skill exists but belongs to another source, it is skipped."
  printf '%b\n' "  - A valid skill folder must contain ${S_CYAN}SKILL.md${S_RESET}."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} accepts the exact skill name from ${S_CYAN}SKILL.md${S_RESET} ${S_DIM}(the name Codex shows in VS Code)${S_RESET} or the installed folder name."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} removes the destination skill folder and local manifest entries; after reloading the extension host, the skill is no longer discoverable from that directory."
  printf '%b\n' "  - Reinstalling from a source that still contains the skill will add it back."
}

is_github_source() {
  local value="$1"
  [[ "${value}" =~ ^https://github\.com/[^/]+/[^/]+(/tree/.+)?/?$ ]]
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

expand_home_path() {
  local value="$1"

  if [[ "${value}" == "~" ]]; then
    printf '%s\n' "${HOME}"
    return
  fi

  if [[ "${#value}" -ge 2 && "${value:0:1}" == "~" && "${value:1:1}" == "/" ]]; then
    printf '%s/%s\n' "${HOME}" "${value#~/}"
    return
  fi

  printf '%s\n' "${value}"
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

read_skill_declared_name() {
  local skill_dir="$1"
  local skill_file="${skill_dir}/SKILL.md"
  local name_line=""
  local name=""
  local first_char=""
  local last_char=""
  local single_quote="'"

  if [[ ! -f "${skill_file}" ]]; then
    printf ''
    return
  fi

  name_line="$(
    awk '
      NR == 1 && $0 == "---" { in_frontmatter = 1; next }
      in_frontmatter && $0 == "---" { exit }
      in_frontmatter && $0 ~ /^name:[[:space:]]*/ { print; exit }
    ' "${skill_file}"
  )"

  if [[ -z "${name_line}" ]]; then
    printf ''
    return
  fi

  name="${name_line#name:}"
  name="${name#"${name%%[![:space:]]*}"}"
  name="${name%"${name##*[![:space:]]}"}"

  if [[ "${#name}" -ge 2 ]]; then
    first_char="${name:0:1}"
    last_char="${name:${#name}-1:1}"
    if [[ "${first_char}" == '"' && "${last_char}" == '"' ]]; then
      name="${name:1:${#name}-2}"
    elif [[ "${first_char}" == "${single_quote}" && "${last_char}" == "${single_quote}" ]]; then
      name="${name:1:${#name}-2}"
    fi
  fi

  printf '%s' "${name}"
}

is_safe_skill_name() {
  local skill_name="$1"

  [[ -n "${skill_name}" && "${skill_name}" != "." && "${skill_name}" != ".." && "${skill_name}" != */* ]]
}

remove_skill_from_manifests() {
  local state_dir="$1"
  local skill_name="$2"
  local manifest=""
  local tmp_manifest=""

  [[ -d "${state_dir}" ]] || return 0

  shopt -s nullglob
  for manifest in "${state_dir}"/*.skills; do
    [[ -f "${manifest}" ]] || continue
    if ! grep -Fxq -- "${skill_name}" "${manifest}"; then
      continue
    fi

    tmp_manifest="$(mktemp)"
    grep -Fxv -- "${skill_name}" "${manifest}" > "${tmp_manifest}" || true
    mv "${tmp_manifest}" "${manifest}"
  done
  shopt -u nullglob
}

resolve_installed_skill_dir() {
  local requested_name="$1"
  local dest_dir="$2"
  local d=""
  local base_name=""
  local declared_name=""
  local matches=()

  [[ -d "${dest_dir}" ]] || return 1

  shopt -s nullglob
  for d in "${dest_dir}"/*; do
    [[ -d "${d}" ]] || continue

    base_name="$(basename "${d}")"
    declared_name="$(read_skill_declared_name "${d}")"
    if [[ "${base_name}" == "${requested_name}" || "${declared_name}" == "${requested_name}" ]]; then
      matches+=("${d}")
    fi
  done
  shopt -u nullglob

  if [[ "${#matches[@]}" -eq 1 ]]; then
    printf '%s\n' "${matches[0]}"
    return 0
  fi

  if [[ "${#matches[@]}" -gt 1 ]]; then
    log_error "multiple installed skills match '--remove-skill ${requested_name}'."
    for d in "${matches[@]}"; do
      base_name="$(basename "${d}")"
      declared_name="$(read_skill_declared_name "${d}")"
      if [[ -n "${declared_name}" && "${declared_name}" != "${base_name}" ]]; then
        log_note "Matched: ${declared_name} (${base_name})"
      else
        log_note "Matched: ${base_name}"
      fi
    done
    return 2
  fi

  return 1
}

remove_installed_skill() {
  local skill_name="$1"
  local dest_dir="$2"
  local state_dir="${dest_dir}/.install-skills-state"
  local target=""
  local target_dir_name=""
  local removed_target=0
  local resolve_status=0

  if ! is_safe_skill_name "${skill_name}"; then
    log_error "invalid skill name for --remove-skill: ${skill_name}"
    log_note "Use a single directory name without path separators."
    exit 1
  fi

  if target="$(resolve_installed_skill_dir "${skill_name}" "${dest_dir}")"; then
    :
  else
    resolve_status=$?
    if [[ "${resolve_status}" -eq 2 ]]; then
      exit 1
    fi

    target="${dest_dir}/${skill_name}"
    if [[ ! -d "${target}" ]]; then
      remove_skill_from_manifests "${state_dir}" "${skill_name}"
      log_note "Skill not installed: ${skill_name}"
      return
    fi
  fi

  target_dir_name="$(basename "${target}")"
  if [[ -e "${target}" && ! -d "${target}" ]]; then
    log_error "destination entry exists but is not a directory: ${target}"
    exit 1
  fi

  if [[ -d "${target}" ]]; then
    if [[ ! -f "${target}/SKILL.md" && ! -f "${target}/.install-source-id" ]]; then
      log_error "refusing to remove unmanaged directory: ${target}"
      log_note "The target does not look like a skill directory."
      exit 1
    fi

    rm -rf "${target}"
    removed_target=1
  fi

  remove_skill_from_manifests "${state_dir}" "${skill_name}"
  if [[ "${target_dir_name}" != "${skill_name}" ]]; then
    remove_skill_from_manifests "${state_dir}" "${target_dir_name}"
  fi

  if [[ "${removed_target}" -eq 1 ]]; then
    log_success "Removed skill: ${skill_name} -> ${target}"
  else
    log_note "Skill not installed: ${skill_name}"
  fi
}

RESOLVED_GITHUB_REF=""
RESOLVED_GITHUB_SUBPATH=""
resolve_github_tree_spec() {
  local repo_url="$1"
  local tree_spec="$2"
  local remote_ref=""
  local ref_name=""
  local best_ref=""

  RESOLVED_GITHUB_REF=""
  RESOLVED_GITHUB_SUBPATH=""

  while IFS=$'\t' read -r _ remote_ref; do
    [[ -n "${remote_ref}" ]] || continue
    case "${remote_ref}" in
      refs/heads/*)
        ref_name="${remote_ref#refs/heads/}"
        ;;
      refs/tags/*)
        ref_name="${remote_ref#refs/tags/}"
        if [[ "${ref_name}" == *'^{}' ]]; then
          continue
        fi
        ;;
      *)
        continue
        ;;
    esac

    if [[ "${tree_spec}" == "${ref_name}" || "${tree_spec}" == "${ref_name}/"* ]]; then
      if [[ -z "${best_ref}" || "${#ref_name}" -gt "${#best_ref}" ]]; then
        best_ref="${ref_name}"
      fi
    fi
  done < <(git ls-remote --heads --tags "${repo_url}")

  if [[ -z "${best_ref}" ]]; then
    return 1
  fi

  RESOLVED_GITHUB_REF="${best_ref}"
  if [[ "${tree_spec}" == "${best_ref}" ]]; then
    RESOLVED_GITHUB_SUBPATH="."
  else
    RESOLVED_GITHUB_SUBPATH="${tree_spec#"${best_ref}"/}"
  fi

  return 0
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

REMOVE_SKILL=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_usage
      exit 0
      ;;
    --remove-skill)
      if [[ $# -lt 2 ]]; then
        log_error "--remove-skill requires a skill name."
        show_usage >&2
        exit 1
      fi
      if [[ -n "${REMOVE_SKILL}" ]]; then
        log_error "--remove-skill may only be specified once."
        show_usage >&2
        exit 1
      fi
      REMOVE_SKILL="$2"
      shift 2
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

if [[ -n "${REMOVE_SKILL}" ]]; then
  if [[ "${#POSITIONAL[@]}" -gt 1 ]]; then
    log_error "too many positional arguments for --remove-skill."
    show_usage >&2
    exit 1
  fi
else
  if [[ "${#POSITIONAL[@]}" -gt 2 ]]; then
    log_error "too many positional arguments."
    show_usage >&2
    exit 1
  fi
fi

if [[ -n "${REMOVE_SKILL}" ]]; then
  DEST_DIR="${POSITIONAL[0]:-${HOME}/.agents/skills}"
  DEST_DIR="$(expand_home_path "${DEST_DIR}")"
  if [[ -d "${DEST_DIR}" ]]; then
    DEST_DIR="$(cd "${DEST_DIR}" && pwd -P)"
  fi
  remove_installed_skill "${REMOVE_SKILL}" "${DEST_DIR}"
  exit 0
fi

SOURCE_SPEC="${POSITIONAL[0]:-${DEFAULT_SRC_DIR}}"
DEST_DIR="${POSITIONAL[1]:-${HOME}/.agents/skills}"
SOURCE_SPEC="$(expand_home_path "${SOURCE_SPEC}")"
DEST_DIR="$(expand_home_path "${DEST_DIR}")"

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
  if [[ "${source_no_trailing}" =~ ^https://github\.com/([^/]+)/([^/]+)/tree/(.+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    tree_spec="${BASH_REMATCH[3]}"
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
  if [[ -n "${tree_spec:-}" ]]; then
    if ! resolve_github_tree_spec "${repo_url}" "${tree_spec}"; then
      log_error "could not resolve GitHub ref/subpath from URL: ${SOURCE_SPEC}"
      log_note "Use a valid branch or tag name, followed by an optional subpath."
      exit 1
    fi
    ref="${RESOLVED_GITHUB_REF}"
    subpath="${RESOLVED_GITHUB_SUBPATH}"
  fi

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
