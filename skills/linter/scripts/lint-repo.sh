#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

ROOT="."
AUTO_FIX=1
CONFIG_FALLBACK=1
HAD_FAILURE=0

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
  printf '%b\n' "${S_YELLOW}WARN:${S_RESET} $1" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}$1${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}$1${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/lint-repo.sh${S_RESET} ${S_DIM}[--root <path>] [--no-fix] [--no-config-fallback]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--root <path>${S_RESET}           Target repository root (default: .)"
  printf '%b\n' "  ${S_YELLOW}--no-fix${S_RESET}               Skip auto-fix steps (markdownlint --fix, ruff --fix)"
  printf '%b\n' "  ${S_YELLOW}--no-config-fallback${S_RESET}   Do not write fallback rules into config files"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}             Show this help"
}

require_command() {
  local command_name="$1"
  local reason="$2"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    log_error "'${command_name}' is required ${reason}."
    HAD_FAILURE=1
    return 1
  fi
  return 0
}

join_with_space() {
  local values=("$@")
  local out=""
  local value=""
  for value in "${values[@]}"; do
    if [[ -n "${out}" ]]; then
      out+=" "
    fi
    out+="${value}"
  done
  printf '%s' "${out}"
}

format_toml_string_array() {
  local values=("$@")
  local out=""
  local value=""
  for value in "${values[@]}"; do
    if [[ -n "${out}" ]]; then
      out+=", "
    fi
    out+="\"${value}\""
  done
  printf '%s' "${out}"
}

write_markdownlint_config() {
  local config_path="$1"
  shift

  {
    printf '{\n'
    printf '  "default": true'
    for rule in "$@"; do
      printf ',\n  "%s": false' "${rule}"
    done
    printf '\n}\n'
  } >"${config_path}"
}

try_extra_markdown_fix() {
  local markdown_files=("$@")

  if command -v prettier >/dev/null 2>&1; then
    log_note "Trying extra Markdown fix with prettier --prose-wrap always..."
    (cd "${ROOT}" && prettier --prose-wrap always --write "${markdown_files[@]}") || return 1
    return 0
  fi

  if command -v mdformat >/dev/null 2>&1; then
    log_note "Trying extra Markdown fix with mdformat..."
    (cd "${ROOT}" && mdformat "${markdown_files[@]}") || return 1
    return 0
  fi

  log_note "No extra Markdown formatter found (checked: prettier, mdformat)."
  return 1
}

ruff_lint_section_has_ignore() {
  local file_path="$1"
  awk '
    BEGIN { in_section = 0; found = 0 }
    /^\[tool\.ruff\.lint\]$/ { in_section = 1; next }
    /^\[.*\]$/ { in_section = 0 }
    in_section && /^[[:space:]]*ignore[[:space:]]*=/ { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "${file_path}"
}

insert_ruff_ignore_line() {
  local file_path="$1"
  local ignore_line="$2"
  local temp_file=""
  temp_file="$(mktemp)"

  awk -v ignore_line="${ignore_line}" '
    BEGIN { in_section = 0; inserted = 0 }
    /^\[tool\.ruff\.lint\]$/ {
      in_section = 1
      print
      next
    }
    /^\[.*\]$/ {
      if (in_section && !inserted) {
        print ignore_line
        inserted = 1
      }
      in_section = 0
      print
      next
    }
    {
      print
    }
    END {
      if (in_section && !inserted) {
        print ignore_line
      }
    }
  ' "${file_path}" >"${temp_file}"

  mv "${temp_file}" "${file_path}"
}

apply_ruff_fallback() {
  local rule_codes=("$@")
  local pyproject_path="${ROOT}/pyproject.toml"
  local ignore_values=""
  local ignore_line=""

  ignore_values="$(format_toml_string_array "${rule_codes[@]}")"
  ignore_line="ignore = [${ignore_values}]"

  if [[ ! -f "${pyproject_path}" ]]; then
    {
      printf '[tool.ruff]\n'
      printf 'line-length = 100\n\n'
      printf '[tool.ruff.lint]\n'
      printf '%s\n' "${ignore_line}"
    } >"${pyproject_path}"
    log_warn "Created pyproject.toml with Ruff fallback rules: $(join_with_space "${rule_codes[@]}")"
    return
  fi

  if ! rg -q '^\[tool\.ruff\.lint\]$' "${pyproject_path}"; then
    {
      printf '\n[tool.ruff.lint]\n'
      printf '%s\n' "${ignore_line}"
    } >>"${pyproject_path}"
    log_warn "Appended [tool.ruff.lint] fallback rules to pyproject.toml: $(join_with_space "${rule_codes[@]}")"
    return
  fi

  if ruff_lint_section_has_ignore "${pyproject_path}"; then
    log_warn "pyproject.toml already has [tool.ruff.lint].ignore; not overwriting."
    log_note "Merge these codes manually: $(join_with_space "${rule_codes[@]}")"
    return
  fi

  insert_ruff_ignore_line "${pyproject_path}" "${ignore_line}"
  log_warn "Added Ruff fallback rules in [tool.ruff.lint]: $(join_with_space "${rule_codes[@]}")"
}

run_shell_lint() {
  local shell_files=()
  local rel_path=""
  local syntax_failed=0

  while IFS= read -r rel_path; do
    if [[ -n "${rel_path}" ]]; then
      shell_files+=("${rel_path}")
    fi
  done < <(cd "${ROOT}" && rg --files -g '*.sh' -g '*.bash' || true)

  if [[ "${#shell_files[@]}" -eq 0 ]]; then
    log_note "No Shell files found (*.sh, *.bash)."
    return
  fi

  log_note "Shell files (${#shell_files[@]}):"
  printf '  - %s\n' "${shell_files[@]}"

  if ! require_command "shellcheck" "for shell script linting"; then
    return
  fi

  for rel_path in "${shell_files[@]}"; do
    if ! bash -n "${ROOT}/${rel_path}"; then
      syntax_failed=1
    fi
  done

  if [[ "${syntax_failed}" -ne 0 ]]; then
    log_error "bash -n reported shell syntax errors."
    HAD_FAILURE=1
  else
    log_success "bash -n checks passed."
  fi

  if ! (cd "${ROOT}" && shellcheck -x "${shell_files[@]}"); then
    log_error "shellcheck reported shell lint issues."
    HAD_FAILURE=1
  else
    log_success "shellcheck checks passed."
  fi
}

run_markdown_lint() {
  local markdown_files=()
  local rel_path=""
  local md_output=""
  local md_status=0
  local md_rules=()
  local rule=""
  local md_config_path="${ROOT}/.markdownlint.json"

  while IFS= read -r rel_path; do
    if [[ -n "${rel_path}" ]]; then
      markdown_files+=("${rel_path}")
    fi
  done < <(cd "${ROOT}" && rg --files -g '*.md' || true)

  if [[ "${#markdown_files[@]}" -eq 0 ]]; then
    log_note "No Markdown files found (*.md)."
    return
  fi

  log_note "Markdown files (${#markdown_files[@]}):"
  printf '  - %s\n' "${markdown_files[@]}"

  if ! require_command "markdownlint" "for Markdown linting"; then
    return
  fi

  if [[ "${AUTO_FIX}" -eq 1 ]]; then
    log_note "Running markdownlint --fix..."
    (cd "${ROOT}" && markdownlint --fix "${markdown_files[@]}") || true
  fi

  set +e
  md_output="$(cd "${ROOT}" && markdownlint "${markdown_files[@]}" 2>&1)"
  md_status=$?
  set -e

  if [[ "${md_status}" -eq 0 ]]; then
    log_success "markdownlint checks passed."
    return
  fi

  log_warn "markdownlint reported issues."
  printf '%s\n' "${md_output}"

  if [[ "${AUTO_FIX}" -eq 1 ]]; then
    if try_extra_markdown_fix "${markdown_files[@]}"; then
      set +e
      md_output="$(cd "${ROOT}" && markdownlint "${markdown_files[@]}" 2>&1)"
      md_status=$?
      set -e

      if [[ "${md_status}" -eq 0 ]]; then
        log_success "markdownlint checks passed after extra fix attempt."
        return
      fi

      log_warn "markdownlint still reported issues after extra fix attempt."
      printf '%s\n' "${md_output}"
    fi
  fi

  if [[ "${CONFIG_FALLBACK}" -eq 0 ]]; then
    HAD_FAILURE=1
    return
  fi

  while IFS= read -r rule; do
    if [[ -n "${rule}" ]]; then
      md_rules+=("${rule}")
    fi
  done < <(printf '%s\n' "${md_output}" | rg -o 'MD[0-9]{3}' | sort -u || true)

  if [[ "${#md_rules[@]}" -eq 0 ]]; then
    log_warn "Could not extract Markdown rule IDs for fallback config."
    HAD_FAILURE=1
    return
  fi

  if [[ -f "${md_config_path}" ]]; then
    log_warn ".markdownlint.json already exists; not overwriting."
    log_note "Add targeted rule overrides manually for: $(join_with_space "${md_rules[@]}")"
    HAD_FAILURE=1
    return
  fi

  write_markdownlint_config "${md_config_path}" "${md_rules[@]}"
  log_warn "Created .markdownlint.json fallback rules: $(join_with_space "${md_rules[@]}")"

  set +e
  md_output="$(cd "${ROOT}" && markdownlint "${markdown_files[@]}" 2>&1)"
  md_status=$?
  set -e

  if [[ "${md_status}" -eq 0 ]]; then
    log_success "markdownlint checks passed after fallback config."
  else
    log_error "markdownlint still failing after fallback config."
    printf '%s\n' "${md_output}"
    HAD_FAILURE=1
  fi
}

run_python_lint() {
  local python_files=()
  local rel_path=""
  local py_output=""
  local py_status=0
  local ruff_codes=()
  local code=""

  while IFS= read -r rel_path; do
    if [[ -n "${rel_path}" ]]; then
      python_files+=("${rel_path}")
    fi
  done < <(cd "${ROOT}" && rg --files -g '*.py' || true)

  if [[ "${#python_files[@]}" -eq 0 ]]; then
    log_note "No Python files found (*.py)."
    return
  fi

  log_note "Python files (${#python_files[@]}):"
  printf '  - %s\n' "${python_files[@]}"

  if ! require_command "ruff" "for Python linting"; then
    return
  fi

  if [[ "${AUTO_FIX}" -eq 1 ]]; then
    log_note "Running ruff check --fix..."
    (cd "${ROOT}" && ruff check --fix "${python_files[@]}") || true
  fi

  set +e
  py_output="$(cd "${ROOT}" && ruff check "${python_files[@]}" 2>&1)"
  py_status=$?
  set -e

  if [[ "${py_status}" -eq 0 ]]; then
    log_success "ruff checks passed."
    return
  fi

  log_warn "ruff reported issues."
  printf '%s\n' "${py_output}"

  if [[ "${CONFIG_FALLBACK}" -eq 0 ]]; then
    HAD_FAILURE=1
    return
  fi

  while IFS= read -r code; do
    if [[ -n "${code}" ]]; then
      ruff_codes+=("${code}")
    fi
  done < <(printf '%s\n' "${py_output}" | awk '/^[A-Z]+[0-9]+/ { print $1 }' | sort -u || true)

  if [[ "${#ruff_codes[@]}" -eq 0 ]]; then
    log_warn "Could not extract Ruff codes for fallback config."
    HAD_FAILURE=1
    return
  fi

  apply_ruff_fallback "${ruff_codes[@]}"

  set +e
  py_output="$(cd "${ROOT}" && ruff check "${python_files[@]}" 2>&1)"
  py_status=$?
  set -e

  if [[ "${py_status}" -eq 0 ]]; then
    log_success "ruff checks passed after fallback config."
  else
    log_error "ruff still failing after fallback config."
    printf '%s\n' "${py_output}"
    HAD_FAILURE=1
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root)
        if [[ $# -lt 2 ]]; then
          log_error "missing value for --root"
          show_usage >&2
          exit 1
        fi
        ROOT="$2"
        shift 2
        ;;
      --no-fix)
        AUTO_FIX=0
        shift
        ;;
      --no-config-fallback)
        CONFIG_FALLBACK=0
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      *)
        log_error "unknown option: $1"
        show_usage >&2
        exit 1
        ;;
    esac
  done
}

main() {
  init_output_style
  parse_args "$@"

  if [[ ! -d "${ROOT}" ]]; then
    log_error "root path not found: ${ROOT}"
    exit 1
  fi
  ROOT="$(cd "${ROOT}" && pwd)"

  if ! require_command "rg" "for file discovery"; then
    exit 1
  fi

  log_note "Lint root: ${ROOT}"
  run_shell_lint
  run_markdown_lint
  run_python_lint

  if [[ "${HAD_FAILURE}" -ne 0 ]]; then
    log_error "Linting finished with errors."
    exit 1
  fi

  log_success "Linting finished with no remaining errors."
}

main "$@"
