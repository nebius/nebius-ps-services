#!/usr/bin/env bash
set -euo pipefail

# Script helper to install Codex skills into ~/.agents/skills.
#
# Usage: ./install-skills.sh [source] [destination_dir]
#        ./install-skills.sh --remove-skill <skill_name> [destination_dir]
#        ./install-skills.sh --install-hooks <source_hook_dir>
#        ./install-skills.sh --install-all-hooks
#        ./install-skills.sh --help
# No-argument install: source is this script's directory and destination is
# ~/.agents/skills. --remove-skill without a destination removes from the same
# default Codex skills directory.
# Source: local directory (default: script directory) or GitHub URL:
#   - https://github.com/<owner>/<repo>
#   - https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
#
# Requirements: bash, rsync, and git (GitHub sources only). Hook installation
# also uses install, find, cmp, chmod, awk, cut, sort, and mktemp.
# How behavior is enforced:
#   - Skill detection: only directories containing SKILL.md are treated as skills.
#   - Idempotency: rsync keeps destination in sync (--delete, --omit-dir-times) and
#     non-meaningful timestamp noise is ignored; reruns converge to "Unchanged".
#   - Source safety: each installed skill writes .install-source-id; existing folders
#     owned by another source (or unmanaged) are skipped, never overwritten.
#   - Scoped cleanup: per-source manifests remove stale skills only when the recorded
#     owner matches the current SOURCE_ID.
#   - Drift visibility: destination skills missing from the selected source are
#     listed at the end with a --remove-skill hint instead of being removed unless
#     they are still marked as owned by the same source.

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
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}[source] [destination_dir]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--remove-skill <skill_name> [destination_dir]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--install-hooks <source_hook_dir>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--install-all-hooks${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--help${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Defaults:${S_RESET}"
  printf '%b\n' "  - ${S_CYAN}./install-skills.sh${S_RESET} installs from this script's directory into ${S_CYAN}~/.agents/skills${S_RESET}."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} without ${S_CYAN}[destination_dir]${S_RESET} removes from ${S_CYAN}~/.agents/skills${S_RESET}."
  printf '\n'

  printf '%b\n' "${S_BOLD}Source:${S_RESET}"
  printf '%b\n' "  - Local directory path ${S_DIM}(multi-skill folder or one skill folder containing SKILL.md)${S_RESET}"
  printf '%b\n' "  - GitHub repository or tree URL:"
  printf '%b\n' "      ${S_CYAN}https://github.com/<owner>/<repo>${S_RESET}"
  printf '%b\n' "      ${S_CYAN}https://github.com/<owner>/<repo>/tree/<ref>/<subpath>${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Destination (default):${S_RESET}"
  printf '%b\n' "  ${S_CYAN}~/.agents/skills${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}       Show this help"
  printf '%b\n' "  ${S_YELLOW}--remove-skill${S_RESET}   Remove one skill by its visible Codex skill name or folder name"
  printf '%b\n' "  ${S_YELLOW}--install-hooks${S_RESET}  Copy hook files from a source hook directory into"
  printf '%b\n' "                    ${S_CYAN}\${CODEX_HOME:-~/.codex}/hooks${S_RESET}, stripping .template suffixes"
  printf '%b\n' "                    without modifying hooks.json"
  printf '%b\n' "  ${S_YELLOW}--install-all-hooks${S_RESET}"
  printf '%b\n' "                    Copy hook files from every ${S_CYAN}*/assets/hooks${S_RESET} directory"
  printf '%b\n' "                    under this source skills folder"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh /Users/example/test${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\"${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\" \"~/custom-skills\"${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --remove-skill nebius${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --remove-skill nebius \"~/custom-skills\"${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-hooks sdlc-start/assets/hooks${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-hooks config-codex/assets/hooks${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-all-hooks${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  - A valid skill is a directory containing ${S_CYAN}SKILL.md${S_RESET}."
  printf '%b\n' "  - Existing unmanaged or other-source-owned destination folders are skipped, never overwritten."
  printf '%b\n' "  - Source-owned skills missing from the selected source are removed, so source-owned renames converge on reinstall."
  printf '%b\n' "  - Other extra destination skills are listed at the end with a ${S_CYAN}--remove-skill${S_RESET} hint."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} accepts the exact skill name from ${S_CYAN}SKILL.md${S_RESET} ${S_DIM}(the name Codex shows in VS Code)${S_RESET} or the installed folder name."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} removes the skill folder and local manifest entries from the selected destination."
  printf '%b\n' "  - Reinstalling from a source that still contains the skill will add it back."
  printf '%b\n' "  - ${S_CYAN}--install-hooks${S_RESET} is opt-in because hooks are runtime guardrails, not skills."
  printf '%b\n' "  - ${S_CYAN}--install-all-hooks${S_RESET} discovers only hook-only ${S_CYAN}*/assets/hooks${S_RESET} directories under this source."
  printf '%b\n' "  - After installing hooks, restart Codex and review/trust the hook entries in ${S_CYAN}/hooks${S_RESET}."
  printf '%b\n' "  - If newly installed skills are not visible, restart the VS Code extension host ${S_DIM}(Developer: Restart Extension Host)${S_RESET}."
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

print_extra_destination_skills() {
  local dest_dir="$1"
  local source_skills_file="$2"
  local extra_count=0
  local d=""
  local name=""
  local declared_name=""
  local target_owner=""
  local ownership_note=""
  local name_note=""
  local default_dest_dir=""

  [[ -d "${dest_dir}" ]] || return 0

  shopt -s nullglob
  for d in "${dest_dir}"/*; do
    [[ -d "${d}" ]] || continue
    [[ -f "${d}/SKILL.md" ]] || continue

    name="$(basename "${d}")"
    if grep -Fxq -- "${name}" "${source_skills_file}"; then
      continue
    fi

    if [[ "${extra_count}" -eq 0 ]]; then
      printf '\n%b\n' "${S_YELLOW}${S_BOLD}Extra destination skills not present in source:${S_RESET}"
    fi

    declared_name="$(read_skill_declared_name "${d}")"
    target_owner="$(read_target_owner "${d}")"
    name_note=""
    ownership_note="unmanaged"

    if [[ -n "${declared_name}" && "${declared_name}" != "${name}" ]]; then
      name_note=" (name: ${declared_name})"
    fi
    if [[ -n "${target_owner}" ]]; then
      if [[ "${target_owner}" == "${SOURCE_ID}" ]]; then
        ownership_note="managed by this source"
      else
        ownership_note="managed by another source"
      fi
    fi

    printf '%b\n' "  - ${name}${name_note} ${S_DIM}[${ownership_note}]${S_RESET}"
    extra_count=$((extra_count + 1))
  done
  shopt -u nullglob

  if [[ "${extra_count}" -gt 0 ]]; then
    default_dest_dir="$(expand_home_path "${HOME}/.agents/skills")"
    if [[ -d "${default_dest_dir}" ]]; then
      default_dest_dir="$(cd "${default_dest_dir}" && pwd -P)"
    fi

    if [[ "${dest_dir}" == "${default_dest_dir}" ]]; then
      printf '%b\n' "Remove one if it is obsolete: ${S_CYAN}./install-skills.sh --remove-skill <skill_name>${S_RESET}"
    else
      printf '%b\n' "Remove one if it is obsolete: ${S_CYAN}./install-skills.sh --remove-skill <skill_name> \"${dest_dir}\"${S_RESET}"
    fi
  fi
}

resolve_hook_source_dir() {
  local value="$1"
  local expanded=""
  local from_script=""

  expanded="$(expand_home_path "${value}")"
  if [[ -d "${expanded}" ]]; then
    (cd "${expanded}" && pwd -P)
    return 0
  fi

  from_script="${DEFAULT_SRC_DIR}/${value}"
  if [[ -d "${from_script}" ]]; then
    (cd "${from_script}" && pwd -P)
    return 0
  fi

  return 1
}

is_installable_hook_rel() {
  local rel="$1"

  case "${rel}" in
    README.md|*/README.md|hooks.json|*/hooks.json|hooks.json.template|*/hooks.json.template|*__pycache__*|*.pyc|*.pyo|*.DS_Store)
      return 1
      ;;
    *.py|*.json|*.py.template|*.json.template)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

install_hooks() {
  local hook_source_arg="$1"
  local codex_home="$2"
  local hook_src=""
  local hook_dest="${codex_home}/hooks"
  local src=""
  local rel=""
  local dest_rel=""
  local installed=0
  local unchanged=0
  local total=0

  require_command "install" "for hook installation"
  require_command "cmp" "for hook idempotency checks"
  require_command "chmod" "for hook permission repair"
  require_command "find" "for hook source discovery"

  if ! hook_src="$(resolve_hook_source_dir "${hook_source_arg}")"; then
    log_error "hook source directory not found: ${hook_source_arg}"
    log_note "Pass a hook-only source directory, for example sdlc-start/assets/hooks or config-codex/assets/hooks."
    exit 1
  fi

  mkdir -p "${hook_dest}"

  while IFS= read -r -d '' src; do
    rel="${src#"${hook_src}/"}"
    is_installable_hook_rel "${rel}" || continue

    dest_rel="${rel%.template}"
    total=$((total + 1))
    if [[ -f "${hook_dest}/${dest_rel}" ]] && cmp -s "${src}" "${hook_dest}/${dest_rel}"; then
      chmod 0644 "${hook_dest}/${dest_rel}"
      unchanged=$((unchanged + 1))
      continue
    fi

    mkdir -p "$(dirname "${hook_dest}/${dest_rel}")"
    install -m 0644 "${src}" "${hook_dest}/${dest_rel}"
    installed=$((installed + 1))
  done < <(find "${hook_src}" -type f -print0)

  if [[ "${total}" -eq 0 ]]; then
    log_error "no hook files found in source directory: ${hook_src}"
    log_note "Expected files ending in .py, .json, .py.template, or .json.template."
    exit 1
  fi

  log_success "Done. Hook files installed/updated: ${installed}, unchanged: ${unchanged} from ${hook_src} -> ${hook_dest}"
  log_note "Template suffixes were stripped for installed hook files."
  log_note "This did not modify hooks.json or trust hooks."
  log_note "Restart Codex, open /hooks, and review the hook entries before relying on them."
}

discover_hook_source_dirs() {
  local source_root="$1"
  local d=""
  local hook_dir=""

  if [[ -f "${source_root}/SKILL.md" ]]; then
    hook_dir="${source_root}/assets/hooks"
    if [[ -d "${hook_dir}" ]]; then
      (cd "${hook_dir}" && pwd -P)
    fi
    return 0
  fi

  shopt -s nullglob
  for d in "${source_root}"/*; do
    [[ -d "${d}" ]] || continue
    [[ -f "${d}/SKILL.md" ]] || continue
    hook_dir="${d}/assets/hooks"
    if [[ -d "${hook_dir}" ]]; then
      (cd "${hook_dir}" && pwd -P)
    fi
  done
  shopt -u nullglob
}

validate_hook_dest_collisions() {
  local manifest=""
  local hook_src=""
  local src=""
  local rel=""
  local dest_rel=""
  local key=""
  local first=""
  local duplicate=""

  require_command "cmp" "for hook collision checks"
  require_command "find" "for hook source discovery"

  manifest="$(mktemp)"
  for hook_src in "$@"; do
    while IFS= read -r -d '' src; do
      rel="${src#"${hook_src}/"}"
      is_installable_hook_rel "${rel}" || continue
      dest_rel="${rel%.template}"
      printf '%s\t%s\n' "${dest_rel}" "${src}" >> "${manifest}"
    done < <(find "${hook_src}" -type f -print0)
  done

  while IFS= read -r key; do
    [[ -n "${key}" ]] || continue
    first=""
    duplicate=0
    while IFS=$'\t' read -r _ src; do
      if [[ -z "${first}" ]]; then
        first="${src}"
        continue
      fi
      duplicate=1
      if ! cmp -s "${first}" "${src}"; then
        log_error "--install-all-hooks found conflicting hook destination: ${key}"
        log_note "First source: ${first}"
        log_note "Conflicting source: ${src}"
        rm -f "${manifest}"
        exit 1
      fi
    done < <(awk -F '\t' -v k="${key}" '$1 == k { print $0 }' "${manifest}")
    if [[ "${duplicate}" -eq 1 ]]; then
      log_note "Duplicate hook destination has identical content: ${key}"
    fi
  done < <(cut -f 1 "${manifest}" | sort -u)

  rm -f "${manifest}"
}

install_all_hooks() {
  local source_root_arg="$1"
  local codex_home="$2"
  local source_root=""
  local hook_src=""
  local hook_dirs=()
  local displayed=""

  if [[ ! -d "${source_root_arg}" ]]; then
    log_error "source skills folder not found: ${source_root_arg}"
    exit 1
  fi
  source_root="$(cd "${source_root_arg}" && pwd -P)"

  while IFS= read -r hook_src; do
    [[ -n "${hook_src}" ]] || continue
    hook_dirs+=("${hook_src}")
  done < <(discover_hook_source_dirs "${source_root}" | sort)

  if [[ "${#hook_dirs[@]}" -eq 0 ]]; then
    log_error "no hook source directories found under ${source_root}"
    log_note "Expected skill-owned hook bundles at */assets/hooks."
    exit 1
  fi

  validate_hook_dest_collisions "${hook_dirs[@]}"

  log_note "Discovered hook source directories:"
  for hook_src in "${hook_dirs[@]}"; do
    displayed="${hook_src#"${source_root}/"}"
    log_note "  - ${displayed}"
  done

  for hook_src in "${hook_dirs[@]}"; do
    install_hooks "${hook_src}" "${codex_home}"
  done

  log_success "Done. Processed all hooks from ${#hook_dirs[@]} source directorie(s)."
}

init_output_style

REMOVE_SKILL=""
HOOK_INSTALL_SOURCE=""
INSTALL_ALL_HOOKS=0
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
    --install-hooks)
      if [[ $# -lt 2 ]]; then
        log_error "--install-hooks requires a source hook directory."
        show_usage >&2
        exit 1
      fi
      if [[ -n "${HOOK_INSTALL_SOURCE}" ]]; then
        log_error "--install-hooks may only be specified once."
        show_usage >&2
        exit 1
      fi
      HOOK_INSTALL_SOURCE="$2"
      shift 2
      ;;
    --install-all-hooks)
      if [[ "${INSTALL_ALL_HOOKS}" -eq 1 ]]; then
        log_error "--install-all-hooks may only be specified once."
        show_usage >&2
        exit 1
      fi
      INSTALL_ALL_HOOKS=1
      shift
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

if [[ -n "${HOOK_INSTALL_SOURCE}" && "${INSTALL_ALL_HOOKS}" -eq 1 ]]; then
  log_error "--install-hooks cannot be combined with --install-all-hooks."
  show_usage >&2
  exit 1
fi

if [[ ( -n "${HOOK_INSTALL_SOURCE}" || "${INSTALL_ALL_HOOKS}" -eq 1 ) && -n "${REMOVE_SKILL}" ]]; then
  log_error "hook installation cannot be combined with --remove-skill."
  show_usage >&2
  exit 1
fi

if [[ "${INSTALL_ALL_HOOKS}" -eq 1 ]]; then
  if [[ "${#POSITIONAL[@]}" -gt 0 ]]; then
    log_error "--install-all-hooks does not accept positional arguments."
    log_note "It installs from hook-only */assets/hooks directories under this script's source folder."
    log_note "Set CODEX_HOME to choose a non-default Codex home."
    show_usage >&2
    exit 1
  fi
  CODEX_HOME_DIR="$(expand_home_path "${CODEX_HOME:-${HOME}/.codex}")"
  install_all_hooks "${DEFAULT_SRC_DIR}" "${CODEX_HOME_DIR}"
  exit 0
fi

if [[ -n "${HOOK_INSTALL_SOURCE}" ]]; then
  if [[ "${#POSITIONAL[@]}" -gt 0 ]]; then
    log_error "--install-hooks does not accept positional arguments."
    log_note "Set CODEX_HOME to choose a non-default Codex home."
    show_usage >&2
    exit 1
  fi
  CODEX_HOME_DIR="$(expand_home_path "${CODEX_HOME:-${HOME}/.codex}")"
  install_hooks "${HOOK_INSTALL_SOURCE}" "${CODEX_HOME_DIR}"
  exit 0
fi

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
TMP_SOURCE_SKILLS=""
TMP_CLONE_ROOT=""
RSYNC_COMPARE_FLAGS=()
cleanup() {
  rm -f "${TMP_MANIFEST:-}" >/dev/null 2>&1 || true
  rm -f "${TMP_SOURCE_SKILLS:-}" >/dev/null 2>&1 || true
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
TMP_SOURCE_SKILLS="$(mktemp)"

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

  printf '%s\n' "${name}" >> "${TMP_SOURCE_SKILLS}"

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

sort -u "${TMP_SOURCE_SKILLS}" -o "${TMP_SOURCE_SKILLS}"
sort -u "${TMP_MANIFEST}" -o "${TMP_MANIFEST}"

# Remove previously installed skills from this source when they no longer exist.
if [[ -f "${SOURCE_MANIFEST}" ]]; then
  while IFS= read -r old_name; do
    [[ -n "${old_name}" ]] || continue
    if ! grep -Fxq -- "${old_name}" "${TMP_SOURCE_SKILLS}"; then
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

# Also clean up source-owned target folders that predate the manifest or were
# missed by it. This lets a renamed source skill converge on reinstall while
# still preserving unmanaged and other-source-owned destination skills.
shopt -s nullglob
for old_target in "${DEST_DIR}"/*; do
  [[ -d "${old_target}" ]] || continue
  [[ -f "${old_target}/SKILL.md" ]] || continue

  old_name="$(basename "${old_target}")"
  if grep -Fxq -- "${old_name}" "${TMP_SOURCE_SKILLS}"; then
    continue
  fi

  old_owner="$(read_target_owner "${old_target}")"
  if [[ "${old_owner}" == "${SOURCE_ID}" ]]; then
    rm -rf "${old_target}"
    log_success "Removed stale skill: ${old_name}"
    removed=$((removed + 1))
  fi
done
shopt -u nullglob

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

print_extra_destination_skills "${DEST_DIR}" "${TMP_SOURCE_SKILLS}"
