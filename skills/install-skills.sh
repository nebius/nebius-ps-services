#!/usr/bin/env bash
set -euo pipefail

# Script helper to install Codex skills into ~/.agents/skills.
#
# Usage: ./install-skills.sh [source] [destination_dir]
#        ./install-skills.sh --remove-skill <skill_name> [destination_dir]
#        ./install-skills.sh --install-hooks <source_hook_dir> [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]
#        ./install-skills.sh --install-all-hooks [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]
#        ./install-skills.sh --help
# No-argument install: source is this script's directory and destination is
# ~/.agents/skills. --remove-skill without a destination removes from the same
# default Codex skills directory.
# Source: local directory (default: script directory) or GitHub URL:
#   - https://github.com/<owner>/<repo>
#   - https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
#
# Requirements: bash, rsync, and git (GitHub sources only). Hook installation
# also uses install, find, cmp, chmod, awk, cut, sort, date, mktemp, and
# shasum or sha256sum. Hook registration also uses python3.
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
#   - Hook drift visibility: hook installation copies missing hook files,
#     records provenance hashes, and refreshes differing existing hook files
#     from the selected source after backing up the previous target. It lists
#     extra installed hook files and hooks.json registrations that are not
#     present in the selected source manifests, but it never deletes them
#     automatically unless --replace-hooks-json is explicitly set for
#     registrations.

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

log_action_required() {
  printf '%b\n' "${S_YELLOW}${S_BOLD}${1}${S_RESET}" >&2
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
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--install-hooks <source_hook_dir> [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET} ${S_DIM}--install-all-hooks [--register-hooks] [--refresh-hook-registrations|--replace-hooks-json]${S_RESET}"
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
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}              Show this help."
  printf '%b\n' "  ${S_YELLOW}--remove-skill <name>${S_RESET}   Remove one skill by visible Codex skill name or folder name."
  printf '%b\n' "  ${S_YELLOW}--install-hooks <dir>${S_RESET}   Sync hook payload files from one source hook directory into"
  printf '%b\n' "                           ${S_CYAN}\${CODEX_HOME:-~/.codex}/hooks${S_RESET}."
  printf '%b\n' "                           Installed names strip a trailing ${S_CYAN}.template${S_RESET} suffix."
  printf '%b\n' "                           Differing existing files are backed up, then refreshed."
  printf '%b\n' "                           Does not change hooks.json unless ${S_CYAN}--register-hooks${S_RESET} is set."
  printf '%b\n' "  ${S_YELLOW}--install-all-hooks${S_RESET}     Sync every reviewed ${S_CYAN}*/assets/hooks${S_RESET} bundle under this"
  printf '%b\n' "                           source skills folder."
  printf '%b\n' "  ${S_YELLOW}--register-hooks${S_RESET}        With a hook install mode, merge selected source manifest(s)"
  printf '%b\n' "                           into ${S_CYAN}\${CODEX_HOME:-~/.codex}/hooks.json${S_RESET}."
  printf '%b\n' "  ${S_YELLOW}--refresh-hook-registrations${S_RESET}"
  printf '%b\n' "                           With ${S_CYAN}--register-hooks${S_RESET}, replace only differing registrations"
  printf '%b\n' "                           for the same event/script and handlers; only statusMessage may differ."
  printf '%b\n' "  ${S_YELLOW}--replace-hooks-json${S_RESET}    With ${S_CYAN}--register-hooks${S_RESET}, replace hooks.json with a clean"
  printf '%b\n' "                           file built only from selected source manifest(s)."
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_DIM}# Install all skills from this source tree.${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}# Install skills from a local folder or GitHub tree.${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh /Users/example/test${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\"${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh \"https://github.com/openai/skills/tree/main/skills\" \"~/custom-skills\"${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}# Remove an installed skill from the default or a custom destination.${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --remove-skill nebius${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --remove-skill nebius \"~/custom-skills\"${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}# Sync reviewed hook payload files. Add --register-hooks to update hooks.json.${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-hooks sdlc-start/assets/hooks${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-hooks config-codex/assets/hooks --register-hooks${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-hooks config-codex/assets/hooks --register-hooks --refresh-hook-registrations${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-all-hooks --register-hooks${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}# Rebuild hooks.json only from the selected reviewed hook manifests.${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./install-skills.sh --install-all-hooks --register-hooks --replace-hooks-json${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  - A valid skill is a directory containing ${S_CYAN}SKILL.md${S_RESET}."
  printf '%b\n' "  - Existing unmanaged or other-source-owned destination folders are skipped,"
  printf '%b\n' "    never overwritten."
  printf '%b\n' "  - Source-owned skills missing from the selected source are removed, so"
  printf '%b\n' "    source-owned renames converge on reinstall."
  printf '%b\n' "  - Other extra destination skills are listed at the end with a"
  printf '%b\n' "    ${S_CYAN}--remove-skill${S_RESET} hint."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} accepts the exact skill name from ${S_CYAN}SKILL.md${S_RESET}"
  printf '%b\n' "    ${S_DIM}(the name Codex shows in VS Code)${S_RESET} or the installed folder name."
  printf '%b\n' "  - ${S_CYAN}--remove-skill${S_RESET} removes the skill folder and local manifest entries"
  printf '%b\n' "    from the selected destination."
  printf '%b\n' "  - Reinstalling from a source that still contains the skill will add it back."
  printf '%b\n' "  - ${S_CYAN}--install-hooks${S_RESET} is opt-in because hooks are runtime guardrails, not skills."
  printf '%b\n' "  - ${S_CYAN}--install-all-hooks${S_RESET} discovers only hook-only ${S_CYAN}*/assets/hooks${S_RESET}"
  printf '%b\n' "    directories under this source."
  printf '%b\n' "  - Hook file provenance hashes are recorded for drift visibility."
  printf '%b\n' "  - Differing existing hook files are backed up, then refreshed from the"
  printf '%b\n' "    selected source."
  printf '%b\n' "  - ${S_CYAN}--register-hooks${S_RESET} preflights ${S_CYAN}hooks.json${S_RESET}, preserves existing entries,"
  printf '%b\n' "    and appends missing source entries."
  printf '%b\n' "  - ${S_CYAN}--register-hooks${S_RESET} refuses duplicate Python hook files within the same hook event."
  printf '%b\n' "  - ${S_CYAN}--refresh-hook-registrations${S_RESET} explicitly replaces only a differing registration"
  printf '%b\n' "    with the same event/script and handlers when only statusMessage differs, preserving unrelated entries."
  printf '%b\n' "  - ${S_CYAN}--replace-hooks-json${S_RESET} explicitly backs up and replaces ${S_CYAN}hooks.json${S_RESET} with"
  printf '%b\n' "    selected source entries."
  printf '%b\n' "  - Hook installation reports extra installed hook files and ${S_CYAN}hooks.json${S_RESET}"
  printf '%b\n' "    entries not present in the selected source."
  printf '%b\n' "  - After installing new or changed hooks, restart Codex and review/trust"
  printf '%b\n' "    the hook entries in ${S_CYAN}/hooks${S_RESET}."
  printf '%b\n' "  - If newly installed skills are not visible, restart the VS Code extension"
  printf '%b\n' "    host ${S_DIM}(Developer: Restart Extension Host)${S_RESET}."
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

hash_file() {
  local file="$1"

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file}" | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file}" | awk '{print $1}'
    return
  fi

  log_error "either 'shasum' or 'sha256sum' is required for hook file provenance."
  exit 1
}

require_hook_hash_command() {
  if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
    log_error "either 'shasum' or 'sha256sum' is required for hook file provenance."
    log_note "Install one SHA-256 tool and run again."
    exit 1
  fi
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

find_hook_registration_manifest() {
  local hook_src="$1"
  local parent_dir=""
  local candidate=""
  local candidates=()

  parent_dir="$(dirname "${hook_src}")"
  candidates=(
    "${hook_src}/hooks.json"
    "${hook_src}/hooks.json.template"
    "${parent_dir}/hooks.json"
    "${parent_dir}/hooks.json.template"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

hook_source_label() {
  local hook_src="$1"

  if [[ "${hook_src}" == "${DEFAULT_SRC_DIR}/"* ]]; then
    printf '%s\n' "${hook_src#"${DEFAULT_SRC_DIR}/"}"
    return 0
  fi

  printf '%s\n' "${hook_src}"
}

hook_source_id() {
  local hook_src="$1"

  printf 'local:%s\n' "${hook_src}"
}

hook_state_dir() {
  local codex_home="$1"

  printf '%s/.install-hooks-state\n' "${codex_home}"
}

hook_state_file() {
  local codex_home="$1"

  printf '%s/hook-files.tsv\n' "$(hook_state_dir "${codex_home}")"
}

write_hook_file_provenance() {
  local codex_home="$1"
  local dest_rel="$2"
  local source_id="$3"
  local source_rel="$4"
  local source_sha="$5"
  local target_sha="$6"
  local state_dir=""
  local state_file=""
  local tmp_state=""

  state_dir="$(hook_state_dir "${codex_home}")"
  state_file="$(hook_state_file "${codex_home}")"
  mkdir -p "${state_dir}"
  tmp_state="$(mktemp)"

  if [[ -f "${state_file}" ]]; then
    awk -F '\t' -v dest_rel="${dest_rel}" '$1 != dest_rel { print }' "${state_file}" > "${tmp_state}"
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "${dest_rel}" \
    "${source_id}" \
    "${source_rel}" \
    "${source_sha}" \
    "${target_sha}" >> "${tmp_state}"
  sort -u "${tmp_state}" -o "${tmp_state}"
  mv "${tmp_state}" "${state_file}"
}

hook_overwrite_backup_dir() {
  local codex_home="$1"

  if [[ -z "${HOOK_OVERWRITE_BACKUP_DIR}" ]]; then
    HOOK_OVERWRITE_BACKUP_DIR="$(hook_state_dir "${codex_home}")/backups/$(date -u +%Y%m%d%H%M%S)-$$"
  fi
  printf '%s\n' "${HOOK_OVERWRITE_BACKUP_DIR}"
}

backup_hook_file_for_overwrite() {
  local codex_home="$1"
  local dest_rel="$2"
  local dest="$3"
  local backup_dest=""

  backup_dest="$(hook_overwrite_backup_dir "${codex_home}")/${dest_rel}"
  mkdir -p "$(dirname "${backup_dest}")"
  cp -p "${dest}" "${backup_dest}"
  log_note "Backed up overwritten hook file: ${backup_dest}"
}

HOOK_SYNC_INSTALLED=0
HOOK_SYNC_UNCHANGED=0
HOOK_SYNC_TOTAL=0
HOOK_FILE_STATUS_FILE=""
HOOK_OVERWRITE_BACKUP_DIR=""

sync_hook_files() {
  local hook_src="$1"
  local codex_home="$2"
  local hook_dest="${codex_home}/hooks"
  local source_label=""
  local source_id=""
  local src=""
  local rel=""
  local dest_rel=""
  local dest=""
  local source_sha=""
  local target_sha=""

  HOOK_SYNC_INSTALLED=0
  HOOK_SYNC_UNCHANGED=0
  HOOK_SYNC_TOTAL=0
  source_label="$(hook_source_label "${hook_src}")"
  source_id="$(hook_source_id "${hook_src}")"

  mkdir -p "${hook_dest}"

  while IFS= read -r -d '' src; do
    rel="${src#"${hook_src}/"}"
    is_installable_hook_rel "${rel}" || continue

    dest_rel="${rel%.template}"
    dest="${hook_dest}/${dest_rel}"
    source_sha="$(hash_file "${src}")"
    HOOK_SYNC_TOTAL=$((HOOK_SYNC_TOTAL + 1))
    if [[ -f "${dest}" ]] && cmp -s "${src}" "${dest}"; then
      chmod 0644 "${dest}"
      target_sha="$(hash_file "${dest}")"
      write_hook_file_provenance "${codex_home}" "${dest_rel}" "${source_id}" "${rel}" "${source_sha}" "${target_sha}"
      HOOK_SYNC_UNCHANGED=$((HOOK_SYNC_UNCHANGED + 1))
    else
      if [[ -f "${dest}" ]]; then
        backup_hook_file_for_overwrite "${codex_home}" "${dest_rel}" "${dest}"
      fi
      mkdir -p "$(dirname "${dest}")"
      install -m 0644 "${src}" "${dest}"
      target_sha="$(hash_file "${dest}")"
      write_hook_file_provenance "${codex_home}" "${dest_rel}" "${source_id}" "${rel}" "${source_sha}" "${target_sha}"
      HOOK_SYNC_INSTALLED=$((HOOK_SYNC_INSTALLED + 1))
    fi
  done < <(find "${hook_src}" -type f -print0)

  if [[ "${HOOK_SYNC_TOTAL}" -eq 0 ]]; then
    log_error "no hook files found in source directory: ${hook_src}"
    log_note "Expected files ending in .py, .json, .py.template, or .json.template."
    exit 1
  fi

  if [[ -n "${HOOK_FILE_STATUS_FILE}" ]]; then
    printf 'FILE\t%s\t%s\t%s\n' \
      "${source_label}" \
      "${HOOK_SYNC_INSTALLED}" \
      "${HOOK_SYNC_UNCHANGED}" >> "${HOOK_FILE_STATUS_FILE}"
  fi
}

write_source_hook_files_manifest() {
  local output_file="$1"
  local hook_src=""
  local src=""
  local rel=""
  local dest_rel=""

  shift
  : > "${output_file}"
  for hook_src in "$@"; do
    while IFS= read -r -d '' src; do
      rel="${src#"${hook_src}/"}"
      is_installable_hook_rel "${rel}" || continue
      dest_rel="${rel%.template}"
      printf '%s\n' "${dest_rel}" >> "${output_file}"
    done < <(find "${hook_src}" -type f -print0)
  done
  sort -u "${output_file}" -o "${output_file}"
}

print_extra_destination_hook_files() {
  local hook_dest="$1"
  local source_files_file="$2"
  local dest_files_file=""
  local dest_file=""
  local rel=""
  local extra_count=0

  [[ -d "${hook_dest}" ]] || return 0

  dest_files_file="$(mktemp)"
  while IFS= read -r -d '' dest_file; do
    rel="${dest_file#"${hook_dest}/"}"
    is_installable_hook_rel "${rel}" || continue
    printf '%s\n' "${rel}" >> "${dest_files_file}"
  done < <(find "${hook_dest}" -type f -print0)
  sort -u "${dest_files_file}" -o "${dest_files_file}"

  while IFS= read -r rel; do
    [[ -n "${rel}" ]] || continue
    if grep -Fxq -- "${rel}" "${source_files_file}"; then
      continue
    fi
    if [[ "${extra_count}" -eq 0 ]]; then
      printf '\n%b\n' "${S_YELLOW}${S_BOLD}Extra installed hook files not present in selected source:${S_RESET}"
    fi
    printf '%b\n' "  - ${rel}"
    extra_count=$((extra_count + 1))
  done < "${dest_files_file}"

  rm -f "${dest_files_file}"

  if [[ "${extra_count}" -gt 0 ]]; then
    printf '%b\n' "Review before deleting. Remove one manually if obsolete: ${S_CYAN}rm \"${hook_dest}/<relative_path>\"${S_RESET}"
  fi
}

print_extra_hook_registrations() {
  local codex_home="$1"
  local hooks_json="${codex_home}/hooks.json"
  local manifest_paths=()
  local hook_src=""
  local manifest_src=""
  local extra_output=""

  shift
  [[ -f "${hooks_json}" ]] || return 0
  command -v python3 >/dev/null 2>&1 || return 0

  for hook_src in "$@"; do
    if manifest_src="$(find_hook_registration_manifest "${hook_src}")"; then
      manifest_paths+=("${manifest_src}")
    fi
  done
  [[ "${#manifest_paths[@]}" -gt 0 ]] || return 0

  extra_output="$(
    python3 - "${codex_home}" "${hooks_json}" "${manifest_paths[@]}" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_manifest(path: Path, codex_home: Path) -> dict[str, list[dict[str, Any]]]:
    hooks_dir = codex_home / "hooks"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for needle, replacement in {
        "{{CODEX_HOME}}": str(codex_home),
        "__CODEX_HOME__": str(codex_home),
        "{{HOOKS_DIR}}": str(hooks_dir),
        "__HOOKS_DIR__": str(hooks_dir),
    }.items():
        text = text.replace(needle, replacement)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    hooks = value.get("hooks") if isinstance(value, dict) else None
    if not isinstance(hooks, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for event_name, entries in hooks.items():
        if isinstance(event_name, str) and isinstance(entries, list):
            result[event_name] = [entry for entry in entries if isinstance(entry, dict)]
    return result


def summarize(event_name: str, entry: dict[str, Any]) -> str:
    matcher = entry.get("matcher", "")
    commands: list[str] = []
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict):
            command = hook.get("command")
            if isinstance(command, str):
                commands.append(command)
    command_text = "; ".join(commands) if commands else "<no command>"
    timeout_values = [
        str(hook.get("timeout"))
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict) and hook.get("timeout") is not None
    ]
    timeout_text = f" timeout={','.join(timeout_values)}" if timeout_values else ""
    matcher_text = f" matcher={matcher!r}" if matcher else ""
    return f"  - {event_name}{matcher_text} command={command_text!r}{timeout_text}"


codex_home = Path(sys.argv[1]).expanduser()
hooks_json = Path(sys.argv[2]).expanduser()
manifest_paths = [Path(value).expanduser() for value in sys.argv[3:]]

target = load_json(hooks_json)
if target is None:
    raise SystemExit(0)
target_hooks = target.get("hooks")
if not isinstance(target_hooks, dict):
    raise SystemExit(0)

source: dict[str, list[dict[str, Any]]] = {}
for manifest_path in manifest_paths:
    for event_name, entries in load_manifest(manifest_path, codex_home).items():
        source.setdefault(event_name, []).extend(entries)

for event_name in sorted(target_hooks):
    entries = target_hooks[event_name]
    if not isinstance(entries, list):
        continue
    source_entries = source.get(event_name, [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry in source_entries:
            continue
        print(summarize(event_name, entry))
PY
  )"

  if [[ -n "${extra_output}" ]]; then
    printf '\n%b\n' "${S_YELLOW}${S_BOLD}Extra hook registrations not present in selected source manifests:${S_RESET}"
    printf '%s\n' "${extra_output}"
    printf '%b\n' "Review before editing. Remove obsolete entries manually from: ${S_CYAN}${hooks_json}${S_RESET}"
  fi
}

print_extra_destination_hooks() {
  local codex_home="$1"
  local hook_dest="${codex_home}/hooks"
  local source_files_file=""

  shift
  source_files_file="$(mktemp)"
  write_source_hook_files_manifest "${source_files_file}" "$@"
  print_extra_destination_hook_files "${hook_dest}" "${source_files_file}"
  rm -f "${source_files_file}"
  print_extra_hook_registrations "${codex_home}" "$@"
}

agent_nebius_auth_hook_source_selected() {
  local hook_src=""

  for hook_src in "$@"; do
    if [[ -f "${hook_src}/pre_tool_use_nebius_auth.py" ]]; then
      return 0
    fi
  done

  return 1
}

reject_inline_agent_nebius_auth_config_hook() {
  local codex_home="$1"
  local config="${codex_home}/config.toml"

  shift
  agent_nebius_auth_hook_source_selected "$@" || return 0
  [[ -f "${config}" ]] || return 0

  if grep -Eq 'agent-nebius-auth managed block begin|pre_tool_use_nebius_auth\.py' "${config}"; then
    log_error "agent-nebius-auth inline config.toml hook entry detected: ${config}"
    log_note "Remove the inline agent-nebius-auth hook entry before registering the canonical hooks.json entry."
    log_note "Then rerun: ./install-skills.sh --install-hooks agent-nebius-auth-setup/assets/hooks --register-hooks"
    exit 1
  fi
}

HOOK_REGISTRATION_STATUS_FILE=""
HOOK_REGISTRATION_PREFLIGHT=0

register_hooks_manifests() {
  local codex_home="$1"
  local replace_hooks_json="$2"
  local refresh_hook_registrations="$3"
  local hook_src=""
  local source_label=""
  local manifest_src=""
  local manifest_args=()
  local status_file="${HOOK_REGISTRATION_STATUS_FILE:-}"
  local preflight="${HOOK_REGISTRATION_PREFLIGHT:-0}"

  shift 3
  require_command "python3" "for hooks.json registration"

  for hook_src in "$@"; do
    if ! manifest_src="$(find_hook_registration_manifest "${hook_src}")"; then
      log_error "--register-hooks could not find hooks.json or hooks.json.template for: ${hook_src}"
      log_note "Place the registration manifest in the hook directory or its parent directory."
      exit 1
    fi
    source_label="$(hook_source_label "${hook_src}")"
    manifest_args+=("${source_label}" "${manifest_src}")
  done

  python3 - "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "${status_file}" "${preflight}" "${manifest_args[@]}" <<'PY'
import json
import os
import re
import shutil
import shlex
import stat
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_manifest(path: Path, codex_home: Path) -> dict[str, list[dict[str, object]]]:
    hooks_dir = codex_home / "hooks"
    manifest_text = path.read_text(encoding="utf-8")
    for needle, replacement in {
        "{{CODEX_HOME}}": str(codex_home),
        "__CODEX_HOME__": str(codex_home),
        "{{HOOKS_DIR}}": str(hooks_dir),
        "__HOOKS_DIR__": str(hooks_dir),
    }.items():
        manifest_text = manifest_text.replace(needle, replacement)

    try:
        source = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")

    if not isinstance(source, dict) or not isinstance(source.get("hooks"), dict):
        fail(f"{path} must contain a top-level hooks object")

    hooks: dict[str, list[dict[str, object]]] = {}
    for event_name, source_entries in sorted(source["hooks"].items()):
        if not isinstance(event_name, str):
            fail(f"{path} hooks keys must be strings")
        if not isinstance(source_entries, list):
            fail(f"{path} hooks.{event_name} must be an array")
        event_entries: list[dict[str, object]] = []
        for entry in source_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                fail(f"{path} hooks.{event_name} entries must contain a hooks array")
            event_entries.append(entry)
        hooks[event_name] = event_entries
    return hooks


def count_entries(value: object) -> int:
    if not isinstance(value, dict):
        return 0
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    return sum(len(entries) for entries in hooks.values() if isinstance(entries, list))


def command_python_script_names(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.replace('"', " ").replace("'", " ").split()

    names: list[str] = []
    for token in tokens:
        candidate = token.rstrip(";),")
        if candidate.endswith(".py"):
            names.append(Path(candidate).name)

    if names:
        return sorted(set(names))

    return sorted(
        {
            Path(match.group(1)).name
            for match in re.finditer(r"([A-Za-z0-9_$/{\}:.~+\-]+\.py)\b", command)
        }
    )


def entry_python_script_names(entry: dict[str, object]) -> list[str]:
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return []

    names: set[str] = set()
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if isinstance(command, str):
            names.update(command_python_script_names(command))
    return sorted(names)


def validate_source_script_uniqueness(
    source_hooks: dict[str, list[dict[str, object]]]
) -> None:
    seen: dict[tuple[str, str], dict[str, object]] = {}
    for event_name, entries in source_hooks.items():
        for entry in entries:
            for script_name in entry_python_script_names(entry):
                key = (event_name, script_name)
                previous = seen.get(key)
                if previous is not None and previous != entry:
                    fail(
                        "Selected source manifests contain multiple "
                        f"{event_name} registrations for Python hook script "
                        f"{script_name!r}. Keep one registration per hook event "
                        "and Python file."
                    )
                seen[key] = entry


def existing_script_index(
    hooks_json: Path, target_hooks: dict[str, object]
) -> dict[tuple[str, str], dict[str, object]]:
    seen: dict[tuple[str, str], dict[str, object]] = {}
    for event_name, entries in target_hooks.items():
        if not isinstance(event_name, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for script_name in entry_python_script_names(entry):
                key = (event_name, script_name)
                if key in seen:
                    fail(
                        f"{hooks_json} already contains duplicate {event_name} "
                        f"registrations for Python hook script {script_name!r}. "
                        "Remove the duplicate manually or rerun with "
                        "--replace-hooks-json when the selected source manifests "
                        "should be authoritative."
                    )
                seen[key] = entry
    return seen


codex_home = Path(sys.argv[1]).expanduser()
replace_hooks_json = sys.argv[2] == "1"
refresh_hook_registrations = sys.argv[3] == "1"
status_file = sys.argv[4]
preflight_only = sys.argv[5] == "1"
manifest_args = sys.argv[6:]
if len(manifest_args) % 2 != 0:
    fail("internal error: hook registration source arguments must be label/path pairs")
source_manifests = [
    (manifest_args[index], Path(manifest_args[index + 1]).expanduser())
    for index in range(0, len(manifest_args), 2)
]
hooks_json = codex_home / "hooks.json"
if not source_manifests:
    fail("--register-hooks requires at least one source manifest")

def entry_label(event_name: str, entry: dict[str, object]) -> str:
    names = entry_python_script_names(entry)
    if names:
        return f"{event_name} {' '.join(names)}"
    return event_name


def handler_lists_refresh_compatible(
    existing_entry: dict[str, object],
    source_entry: dict[str, object],
) -> bool:
    existing_handlers = existing_entry.get("hooks")
    source_handlers = source_entry.get("hooks")
    if not isinstance(existing_handlers, list) or not isinstance(source_handlers, list):
        return False

    def without_status_message(handler: object) -> object:
        if not isinstance(handler, dict):
            return handler
        normalized = dict(handler)
        normalized.pop("statusMessage", None)
        return normalized

    return [without_status_message(handler) for handler in existing_handlers] == [
        without_status_message(handler) for handler in source_handlers
    ]


source_hooks: dict[str, list[dict[str, object]]] = {}
loaded_sources: list[
    tuple[str, Path, dict[str, list[dict[str, object]]]]
] = []
for source_label, manifest_path in source_manifests:
    loaded_hooks = load_manifest(manifest_path, codex_home)
    loaded_sources.append((source_label, manifest_path, loaded_hooks))
    for event_name, entries in loaded_hooks.items():
        target_entries = source_hooks.setdefault(event_name, [])
        for entry in entries:
            if entry not in target_entries:
                target_entries.append(entry)

validate_source_script_uniqueness(source_hooks)

new_target = {"hooks": source_hooks}
source_count = count_entries(new_target)
existing_target: object = None
existing_valid = False
existing_count = 0
had_existing = hooks_json.exists()
added = 0
refreshed = 0
unchanged_entries = 0
superseded_removed = 0
registration_statuses: list[tuple[str, str, str]] = []

if had_existing:
    try:
        existing_target = json.loads(hooks_json.read_text(encoding="utf-8"))
        existing_valid = isinstance(existing_target, dict)
    except json.JSONDecodeError:
        existing_target = None
    if existing_valid:
        existing_count = count_entries(existing_target)

if replace_hooks_json:
    target = new_target
else:
    if had_existing:
        if not existing_valid:
            fail(f"{hooks_json} is not valid JSON; use --replace-hooks-json to rebuild it from source manifests")
        target = existing_target
    else:
        target = {"hooks": {}}

    if not isinstance(target, dict):
        fail(f"{hooks_json} must contain a JSON object")
    target_hooks = target.setdefault("hooks", {})
    if not isinstance(target_hooks, dict):
        fail(f"{hooks_json} must contain a top-level hooks object")
    target_script_entries = existing_script_index(hooks_json, target_hooks)

    arbiter_selected = any(
        "stop_lifecycle_arbiter.py" in entry_python_script_names(entry)
        for entry in source_hooks.get("Stop", [])
    )
    if arbiter_selected:
        stop_entries = target_hooks.get("Stop", [])
        if not isinstance(stop_entries, list):
            fail(f"{hooks_json} hooks.Stop must be an array")
        superseded_scripts = {
            "stop_sdlc_continue.py",
            "remediation_attempt_guard.py",
        }
        retained_stop_entries: list[object] = []
        for entry in stop_entries:
            if not isinstance(entry, dict):
                retained_stop_entries.append(entry)
                continue
            names = set(entry_python_script_names(entry))
            matched = names & superseded_scripts
            if not matched:
                retained_stop_entries.append(entry)
                continue
            handlers = entry.get("hooks")
            if (
                set(entry) != {"hooks"}
                or not isinstance(handlers, list)
                or len(handlers) != 1
                or not isinstance(handlers[0], dict)
                or names != matched
                or len(names) != 1
            ):
                fail(
                    "Legacy Stop registration migration matched a mixed handler "
                    "entry; refusing to remove unrelated policy"
                )
            handler = handlers[0]
            command = handler.get("command")
            if not isinstance(command, str):
                fail("Legacy Stop registration has no exact command")
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = []
            script_name = next(iter(matched))
            expected_paths = {
                f"${{CODEX_HOME:-$HOME/.codex}}/hooks/{script_name}",
                str((hooks_json.parent / "hooks" / script_name).resolve()),
            }
            if (
                len(tokens) != 2
                or tokens[0] != "python3"
                or tokens[1] not in expected_paths
            ):
                fail(
                    "Legacy Stop registration migration did not match the exact "
                    "managed command; refusing removal"
                )
            superseded_removed += 1
        if superseded_removed:
            target_hooks["Stop"] = retained_stop_entries
            target_script_entries = existing_script_index(hooks_json, target_hooks)

    for source_label, _manifest_path, loaded_hooks in loaded_sources:
        for event_name, source_entries in loaded_hooks.items():
            target_entries = target_hooks.setdefault(event_name, [])
            if not isinstance(target_entries, list):
                fail(f"{hooks_json} hooks.{event_name} must be an array")
            for entry in source_entries:
                label = entry_label(event_name, entry)
                if entry in target_entries:
                    unchanged_entries += 1
                    registration_statuses.append((source_label, "unchanged", label))
                    continue
                conflicts = {
                    id(existing_entry): existing_entry
                    for script_name in entry_python_script_names(entry)
                    if (
                        existing_entry := target_script_entries.get(
                            (event_name, script_name)
                        )
                    )
                    is not None
                }
                if conflicts:
                    if not refresh_hook_registrations:
                        fail(
                            "Refusing to register duplicate "
                            f"{event_name} hook script: {hooks_json} "
                            "already has a different entry for that Python file. "
                            "Remove the obsolete entry manually or rerun with "
                            "--refresh-hook-registrations to replace only this "
                            "same-event, same-script registration."
                        )
                    if len(conflicts) != 1:
                        fail(
                            "Targeted hook registration refresh matched multiple "
                            f"existing {event_name} entries; refusing ambiguous replacement"
                        )
                    existing_entry = next(iter(conflicts.values()))
                    if not handler_lists_refresh_compatible(existing_entry, entry):
                        fail(
                            "Targeted hook registration refresh matched an entry "
                            "with a differing or mixed handler list beyond "
                            "statusMessage metadata; refusing to replace "
                            "unrelated handlers"
                        )
                    target_entries[target_entries.index(existing_entry)] = entry
                    for script_name in entry_python_script_names(existing_entry):
                        target_script_entries.pop((event_name, script_name), None)
                    for script_name in entry_python_script_names(entry):
                        target_script_entries[(event_name, script_name)] = entry
                    refreshed += 1
                    registration_statuses.append((source_label, "refreshed", label))
                    continue
                target_entries.append(entry)
                added += 1
                registration_statuses.append((source_label, "added", label))
                for script_name in entry_python_script_names(entry):
                    target_script_entries[(event_name, script_name)] = entry

if replace_hooks_json:
    status = "unchanged" if existing_valid and existing_target == new_target else "selected"
    for source_label, _manifest_path, loaded_hooks in loaded_sources:
        for event_name, source_entries in loaded_hooks.items():
            for entry in source_entries:
                registration_statuses.append((source_label, status, entry_label(event_name, entry)))

if replace_hooks_json:
    unchanged = existing_valid and existing_target == target
else:
    unchanged = (
        had_existing
        and existing_valid
        and added == 0
        and refreshed == 0
        and superseded_removed == 0
    )

if preflight_only:
    raise SystemExit(0)

codex_home.mkdir(parents=True, exist_ok=True)
backup_path = ""
summary_message = ""
extra_messages: list[str] = []
if not unchanged:
    if hooks_json.exists():
        backup_path = str(
            hooks_json.with_name(
                f"hooks.json.bak.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            )
        )
        shutil.copy2(hooks_json, backup_path)

    mode = 0o644
    if hooks_json.exists():
        mode = stat.S_IMODE(hooks_json.stat().st_mode)

    fd, tmp_name = tempfile.mkstemp(prefix=".hooks.json.", dir=str(codex_home))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(target, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, hooks_json)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    if replace_hooks_json:
        summary_message = (
            f"Rebuilt hook registrations: {source_count} entries from "
            f"{len(source_manifests)} source manifest(s) -> {hooks_json}"
        )
        if existing_valid:
            removed = max(existing_count - source_count, 0)
            if removed:
                extra_messages.append(
                    f"Removed existing hook registrations not in selected source manifests: {removed}"
                )
        else:
            if had_existing:
                extra_messages.append(
                    "Previous hooks.json was not valid JSON and was replaced from source manifests."
                )
    else:
        summary_message = (
            f"Hook registrations summary: added {added}, refreshed {refreshed}, "
            f"unchanged {unchanged_entries} "
            f"from {len(source_manifests)} source manifest(s) -> {hooks_json}"
        )
        if superseded_removed:
            extra_messages.append(
                "Replaced legacy independently registered Stop handlers with "
                f"the lifecycle arbiter: {superseded_removed}"
            )
    if backup_path:
        extra_messages.append(f"Backed up previous hooks.json: {backup_path}")
else:
    if replace_hooks_json:
        summary_message = (
            f"Hook registrations unchanged: {source_count} source entries from "
            f"{len(source_manifests)} source manifest(s) -> {hooks_json}"
        )
    else:
        summary_message = (
            f"Hook registrations summary: added 0, refreshed 0, "
            f"unchanged {unchanged_entries} "
            f"from {len(source_manifests)} source manifest(s) -> {hooks_json}"
        )

if status_file:
    source_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_status_counts: dict[str, int] = defaultdict(int)
    for source_label, status, _label in registration_statuses:
        source_counts[source_label][status] += 1
        total_status_counts[status] += 1

    with Path(status_file).open("a", encoding="utf-8") as handle:
        handle.write(f"DEST\t{hooks_json}\n")
        for source_label, _manifest_path in source_manifests:
            counts = source_counts[source_label]
            handle.write(
                "SOURCE\t"
                f"{source_label}\t"
                f"{counts['added']}\t"
                f"{counts['refreshed']}\t"
                f"{counts['unchanged']}\t"
                f"{counts['selected']}\n"
            )
        handle.write(
            f"TOTAL\t{total_status_counts['added']}\t"
            f"{total_status_counts['refreshed']}\t"
            f"{total_status_counts['unchanged']}\t"
            f"{total_status_counts['selected']}\t"
            f"{0 if unchanged else 1}\t{summary_message}\n"
        )
        for message in extra_messages:
            handle.write(f"MESSAGE\t{message}\n")
else:
    print("Hook registrations:")
    current_source = ""
    for source_label, status, label in registration_statuses:
        if source_label != current_source:
            print(f"  {source_label} -> {hooks_json}")
            current_source = source_label
        print(f"    {status} {label}")
    print(summary_message)
    for message in extra_messages:
        print(message)
PY
}

register_hooks_manifest() {
  local hook_src="$1"
  local codex_home="$2"
  local replace_hooks_json="${3:-0}"
  local refresh_hook_registrations="${4:-0}"

  register_hooks_manifests "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "${hook_src}"
}

preflight_register_hooks_manifests() {
  local codex_home="$1"
  local replace_hooks_json="$2"
  local refresh_hook_registrations="$3"

  shift 3
  HOOK_REGISTRATION_PREFLIGHT=1 register_hooks_manifests "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "$@"
}

preflight_register_hooks_manifest() {
  local hook_src="$1"
  local codex_home="$2"
  local replace_hooks_json="${3:-0}"
  local refresh_hook_registrations="${4:-0}"

  preflight_register_hooks_manifests "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "${hook_src}"
}

HOOK_STATUS_REVIEW_NEEDED=0

format_updated_label() {
  local count="$1"

  if [[ "${count}" -gt 0 ]]; then
    printf '%b' "${S_GREEN}${S_BOLD}updated${S_RESET}"
  else
    printf '%s' "updated"
  fi
}

format_two_counts() {
  local first_label="$1"
  local first_count="$2"
  local second_label="$3"
  local second_count="$4"

  if [[ "${first_label}" == "updated" ]]; then
    first_label="$(format_updated_label "${first_count}")"
  fi

  if [[ "${first_count}" -gt 0 && "${second_count}" -gt 0 ]]; then
    printf '%s %s, %s %s\n' "${first_label}" "${first_count}" "${second_label}" "${second_count}"
  elif [[ "${first_count}" -gt 0 ]]; then
    printf '%s %s\n' "${first_label}" "${first_count}"
  else
    printf '%s %s\n' "${second_label}" "${second_count}"
  fi
}

format_registration_counts() {
  local added="$1"
  local refreshed="$2"
  local unchanged="$3"
  local selected="$4"
  local parts=()
  local result=""
  local part=""

  if [[ "${added}" -gt 0 ]]; then
    parts+=("added ${added}")
  fi
  if [[ "${refreshed}" -gt 0 ]]; then
    parts+=("refreshed ${refreshed}")
  fi
  if [[ "${selected}" -gt 0 ]]; then
    parts+=("selected ${selected}")
  fi
  if [[ "${unchanged}" -gt 0 || "${#parts[@]}" -eq 0 ]]; then
    parts+=("unchanged ${unchanged}")
  fi

  for part in "${parts[@]}"; do
    if [[ -z "${result}" ]]; then
      result="${part}"
    else
      result="${result}, ${part}"
    fi
  done
  printf '%s\n' "${result}"
}

registration_status_for_source() {
  local status_file="$1"
  local source_label="$2"

  [[ -f "${status_file}" ]] || return 1
  awk -F '\t' -v source_label="${source_label}" \
    '$1 == "SOURCE" && $2 == source_label { print $3 "\t" $4 "\t" $5 "\t" $6; found = 1; exit } END { exit found ? 0 : 1 }' \
    "${status_file}"
}

registration_dest_from_status() {
  local status_file="$1"

  [[ -f "${status_file}" ]] || return 1
  awk -F '\t' '$1 == "DEST" { print $2; found = 1; exit } END { exit found ? 0 : 1 }' "${status_file}"
}

registration_total_from_status() {
  local status_file="$1"

  [[ -f "${status_file}" ]] || return 1
  awk -F '\t' '$1 == "TOTAL" { print $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6; found = 1; exit } END { exit found ? 0 : 1 }' "${status_file}"
}

print_registration_messages() {
  local status_file="$1"

  [[ -f "${status_file}" ]] || return 0
  awk -F '\t' '$1 == "MESSAGE" { print $2 }' "${status_file}"
}

print_combined_hook_status() {
  local codex_home="$1"
  local file_status_file="$2"
  local registration_status_file="$3"
  local register_hooks="$4"
  local hook_dest="${codex_home}/hooks"
  local registration_dest=""
  local kind=""
  local source_label=""
  local file_updated=0
  local file_unchanged=0
  local reg_added=0
  local reg_refreshed=0
  local reg_unchanged=0
  local reg_selected=0
  local total_file_updated=0
  local total_file_unchanged=0
  local total_reg_added=0
  local total_reg_refreshed=0
  local total_reg_unchanged=0
  local total_reg_selected=0
  local reg_changed=0
  local source_changed=0
  local reg_line=""

  HOOK_STATUS_REVIEW_NEEDED=0
  registration_dest="$(registration_dest_from_status "${registration_status_file}" 2>/dev/null || true)"
  if [[ -z "${registration_dest}" ]]; then
    registration_dest="${codex_home}/hooks.json"
  fi

  printf '%b\n' "${S_BOLD}Hooks status:${S_RESET}"
  while IFS=$'\t' read -r kind source_label file_updated file_unchanged; do
    [[ "${kind}" == "FILE" ]] || continue
    total_file_updated=$((total_file_updated + file_updated))
    total_file_unchanged=$((total_file_unchanged + file_unchanged))
    reg_added=0
    reg_refreshed=0
    reg_unchanged=0
    reg_selected=0
    if [[ "${register_hooks}" -eq 1 ]]; then
      reg_line="$(registration_status_for_source "${registration_status_file}" "${source_label}" 2>/dev/null || true)"
      if [[ -n "${reg_line}" ]]; then
        IFS=$'\t' read -r reg_added reg_refreshed reg_unchanged reg_selected <<< "${reg_line}"
      fi
    fi

    source_changed=0
    if [[ "${file_updated}" -gt 0 || "${reg_added}" -gt 0 || "${reg_refreshed}" -gt 0 || "${reg_selected}" -gt 0 ]]; then
      source_changed=1
    fi

    if [[ "${source_changed}" -eq 1 ]]; then
      printf '%b\n' "  ${S_YELLOW}changed${S_RESET} ${S_CYAN}${source_label}${S_RESET}"
    else
      printf '%b\n' "  unchanged ${S_CYAN}${source_label}${S_RESET}"
    fi
    printf '%b\n' "    files: $(format_two_counts updated "${file_updated}" unchanged "${file_unchanged}") -> ${hook_dest}"
    if [[ "${register_hooks}" -eq 1 ]]; then
      printf '%b\n' "    registrations: $(format_registration_counts "${reg_added}" "${reg_refreshed}" "${reg_unchanged}" "${reg_selected}") -> ${registration_dest}"
    else
      printf '%b\n' "    registrations: not requested"
    fi
  done < "${file_status_file}"

  if [[ "${register_hooks}" -eq 1 ]]; then
    reg_line="$(registration_total_from_status "${registration_status_file}" 2>/dev/null || true)"
    if [[ -n "${reg_line}" ]]; then
      IFS=$'\t' read -r total_reg_added total_reg_refreshed total_reg_unchanged total_reg_selected reg_changed <<< "${reg_line}"
    fi
    printf '%b\n' "Summary: files $(format_updated_label "${total_file_updated}") ${total_file_updated}, unchanged ${total_file_unchanged}; registrations $(format_registration_counts "${total_reg_added}" "${total_reg_refreshed}" "${total_reg_unchanged}" "${total_reg_selected}")"
    print_registration_messages "${registration_status_file}"
  else
    printf '%b\n' "Summary: files $(format_updated_label "${total_file_updated}") ${total_file_updated}, unchanged ${total_file_unchanged}; registrations not requested"
  fi

  if [[ "${total_file_updated}" -gt 0 || "${total_reg_added}" -gt 0 || "${total_reg_refreshed}" -gt 0 || "${total_reg_selected}" -gt 0 || "${reg_changed}" -gt 0 ]]; then
    HOOK_STATUS_REVIEW_NEEDED=1
  fi
}

install_hooks() {
  local hook_source_arg="$1"
  local codex_home="$2"
  local register_hooks="${3:-0}"
  local report_extras="${4:-1}"
  local replace_hooks_json="${5:-0}"
  local refresh_hook_registrations="${6:-0}"
  local hook_src=""
  local file_status_file=""
  local registration_status_file=""

  require_command "install" "for hook installation"
  require_command "cmp" "for hook idempotency checks"
  require_command "chmod" "for hook permission repair"
  require_command "find" "for hook source discovery"
  require_command "awk" "for hook provenance checks"
  require_command "sort" "for hook drift reporting"
  require_command "date" "for hook overwrite backups"
  require_hook_hash_command

  if ! hook_src="$(resolve_hook_source_dir "${hook_source_arg}")"; then
    log_error "hook source directory not found: ${hook_source_arg}"
    log_note "Pass a hook-only source directory, for example sdlc-start/assets/hooks or config-codex/assets/hooks."
    exit 1
  fi

  reject_inline_agent_nebius_auth_config_hook "${codex_home}" "${hook_src}"

  if [[ "${register_hooks}" -eq 1 ]]; then
    preflight_register_hooks_manifest "${hook_src}" "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}"
  fi

  file_status_file="$(mktemp)"
  registration_status_file="$(mktemp)"
  HOOK_FILE_STATUS_FILE="${file_status_file}"
  sync_hook_files "${hook_src}" "${codex_home}"
  HOOK_FILE_STATUS_FILE=""
  if [[ "${register_hooks}" -eq 1 ]]; then
    HOOK_REGISTRATION_STATUS_FILE="${registration_status_file}"
    register_hooks_manifest "${hook_src}" "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}"
    HOOK_REGISTRATION_STATUS_FILE=""
  else
    log_note "This did not modify hooks.json. Pass --register-hooks to merge a source registration manifest."
  fi
  print_combined_hook_status "${codex_home}" "${file_status_file}" "${registration_status_file}" "${register_hooks}"
  if [[ "${HOOK_STATUS_REVIEW_NEEDED}" -eq 1 ]]; then
    log_action_required "Action required: hook files or registrations changed. Restart Codex and review/trust entries in /hooks."
  fi
  rm -f "${file_status_file}" "${registration_status_file}"
  if [[ "${report_extras}" -eq 1 ]]; then
    print_extra_destination_hooks "${codex_home}" "${hook_src}"
  fi
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
  local register_hooks="${3:-0}"
  local replace_hooks_json="${4:-0}"
  local refresh_hook_registrations="${5:-0}"
  local source_root=""
  local hook_src=""
  local hook_dirs=()
  local file_status_file=""
  local registration_status_file=""

  if [[ ! -d "${source_root_arg}" ]]; then
    log_error "source skills folder not found: ${source_root_arg}"
    exit 1
  fi
  source_root="$(cd "${source_root_arg}" && pwd -P)"

  require_command "install" "for hook installation"
  require_command "cmp" "for hook idempotency checks"
  require_command "chmod" "for hook permission repair"
  require_command "find" "for hook source discovery"
  require_command "awk" "for hook provenance checks"
  require_command "sort" "for hook source discovery"
  require_command "date" "for hook overwrite backups"
  require_hook_hash_command

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
  reject_inline_agent_nebius_auth_config_hook "${codex_home}" "${hook_dirs[@]}"

  if [[ "${register_hooks}" -eq 1 ]]; then
    preflight_register_hooks_manifests "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "${hook_dirs[@]}"
  fi

  file_status_file="$(mktemp)"
  registration_status_file="$(mktemp)"
  HOOK_FILE_STATUS_FILE="${file_status_file}"
  for hook_src in "${hook_dirs[@]}"; do
    sync_hook_files "${hook_src}" "${codex_home}"
  done
  HOOK_FILE_STATUS_FILE=""

  if [[ "${register_hooks}" -eq 1 ]]; then
    HOOK_REGISTRATION_STATUS_FILE="${registration_status_file}"
    register_hooks_manifests "${codex_home}" "${replace_hooks_json}" "${refresh_hook_registrations}" "${hook_dirs[@]}"
    HOOK_REGISTRATION_STATUS_FILE=""
  else
    log_note "This did not modify hooks.json. Pass --register-hooks to merge discovered source registration manifests."
  fi
  print_combined_hook_status "${codex_home}" "${file_status_file}" "${registration_status_file}" "${register_hooks}"
  if [[ "${HOOK_STATUS_REVIEW_NEEDED}" -eq 1 ]]; then
    log_action_required "Action required: hook files or registrations changed. Restart Codex and review/trust entries in /hooks."
  fi
  rm -f "${file_status_file}" "${registration_status_file}"

  print_extra_destination_hooks "${codex_home}" "${hook_dirs[@]}"
}

init_output_style

REMOVE_SKILL=""
HOOK_INSTALL_SOURCE=""
INSTALL_ALL_HOOKS=0
REGISTER_HOOKS=0
REPLACE_HOOKS_JSON=0
REFRESH_HOOK_REGISTRATIONS=0
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
      if [[ $# -lt 2 || "${2}" == -* ]]; then
        log_error "--install-hooks requires a source hook directory."
        log_note "Use --install-all-hooks to sync every reviewed hook bundle, or pass a hook-only source such as sdlc-start/assets/hooks."
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
    --register-hooks)
      if [[ "${REGISTER_HOOKS}" -eq 1 ]]; then
        log_error "--register-hooks may only be specified once."
        show_usage >&2
        exit 1
      fi
      REGISTER_HOOKS=1
      shift
      ;;
    --replace-hooks-json)
      if [[ "${REPLACE_HOOKS_JSON}" -eq 1 ]]; then
        log_error "--replace-hooks-json may only be specified once."
        show_usage >&2
        exit 1
      fi
      REPLACE_HOOKS_JSON=1
      shift
      ;;
    --refresh-hook-registrations)
      if [[ "${REFRESH_HOOK_REGISTRATIONS}" -eq 1 ]]; then
        log_error "--refresh-hook-registrations may only be specified once."
        show_usage >&2
        exit 1
      fi
      REFRESH_HOOK_REGISTRATIONS=1
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

if [[ "${REGISTER_HOOKS}" -eq 1 && -z "${HOOK_INSTALL_SOURCE}" && "${INSTALL_ALL_HOOKS}" -eq 0 ]]; then
  log_error "--register-hooks must be combined with --install-hooks or --install-all-hooks."
  show_usage >&2
  exit 1
fi

if [[ "${REPLACE_HOOKS_JSON}" -eq 1 && "${REGISTER_HOOKS}" -eq 0 ]]; then
  log_error "--replace-hooks-json must be combined with --register-hooks."
  show_usage >&2
  exit 1
fi

if [[ "${REFRESH_HOOK_REGISTRATIONS}" -eq 1 && "${REGISTER_HOOKS}" -eq 0 ]]; then
  log_error "--refresh-hook-registrations must be combined with --register-hooks."
  show_usage >&2
  exit 1
fi

if [[ "${REFRESH_HOOK_REGISTRATIONS}" -eq 1 && "${REPLACE_HOOKS_JSON}" -eq 1 ]]; then
  log_error "--refresh-hook-registrations cannot be combined with --replace-hooks-json."
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
  install_all_hooks "${DEFAULT_SRC_DIR}" "${CODEX_HOME_DIR}" "${REGISTER_HOOKS}" "${REPLACE_HOOKS_JSON}" "${REFRESH_HOOK_REGISTRATIONS}"
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
  install_hooks "${HOOK_INSTALL_SOURCE}" "${CODEX_HOME_DIR}" "${REGISTER_HOOKS}" 1 "${REPLACE_HOOKS_JSON}" "${REFRESH_HOOK_REGISTRATIONS}"
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
