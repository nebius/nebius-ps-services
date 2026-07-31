#!/usr/bin/env bash
set -euo pipefail

umask 077

label="local.nosleep4mac.caffeinate-ac"
check_only="false"

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""

active_tmp_file=""
backup_file=""
lock_file=""
lock_held="false"
rollback_on_exit="false"
preserve_backup_on_exit="false"

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_YELLOW=$'\033[33m'
    S_GREEN=$'\033[32m'
  fi
}

log_error() {
  printf '%b%s%b\n' \
    "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} " "$1" "${S_RESET}" >&2
}

log_warn() {
  printf '%b%s%b\n' "${S_YELLOW}" "$1" "${S_RESET}" >&2
}

log_note() {
  printf '%b%s%b\n' "${S_DIM}" "$1" "${S_RESET}"
}

log_success() {
  printf '%b%s%b\n' "${S_GREEN}" "$1" "${S_RESET}"
}

die() {
  log_error "$1"
  exit "${2:-1}"
}

show_usage() {
  printf 'Usage: nosleep4mac.sh [--check]\n' >&2
  printf 'Run without arguments to ensure the canonical AC-only LaunchAgent.\n' >&2
}

cleanup() {
  if [[ "${rollback_on_exit}" == "true" ]]; then
    rollback_on_exit="false"
    if ! rollback_changed_plist; then
      preserve_backup_on_exit="true"
    fi
  fi
  if [[ -n "${active_tmp_file}" && -e "${active_tmp_file}" ]]; then
    /bin/rm -f -- "${active_tmp_file}"
  fi
  if [[ "${preserve_backup_on_exit}" != "true" \
    && -n "${backup_file}" \
    && -e "${backup_file}" ]]; then
    /bin/rm -f -- "${backup_file}"
  fi
  if [[ "${lock_held}" == "true" && -n "${lock_file}" ]]; then
    lock_path_inode=""
    lock_fd_inode=""
    if [[ -f "${lock_file}" && ! -L "${lock_file}" ]]; then
      lock_path_inode="$("${stat_bin}" -f '%i' "${lock_file}" 2>/dev/null || true)"
      lock_fd_inode="$("${stat_bin}" -f '%i' /dev/fd/9 2>/dev/null || true)"
    fi
    if [[ -n "${lock_path_inode}" \
      && "${lock_path_inode}" == "${lock_fd_inode}" \
      && "$(path_owner "${lock_file}")" == "${user_id}" ]]; then
      /bin/rm -f -- "${lock_file}"
    fi
    exec 9>&-
  fi
  if [[ "${preserve_backup_on_exit}" == "true" \
    && -n "${backup_file}" \
    && -e "${backup_file}" ]]; then
    log_error "rollback was incomplete; the prior plist backup was retained at ${backup_file}"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$#" -eq 1 && "$1" == "--check" ]]; then
  check_only="true"
elif [[ "$#" -ne 0 ]]; then
  show_usage
  exit 2
fi

test_mode="${NOSLEEP4MAC_TEST_MODE:-0}"
if [[ "${test_mode}" == "1" ]]; then
  test_root="${NOSLEEP4MAC_TEST_ROOT:?NOSLEEP4MAC_TEST_ROOT is required in test mode}"
  test_bin_dir="${NOSLEEP4MAC_TEST_BIN_DIR:?NOSLEEP4MAC_TEST_BIN_DIR is required in test mode}"
  case "${test_root}" in
    /tmp/* | /private/tmp/* | /var/folders/*) ;;
    *) die "test root must be below the system temporary directory" ;;
  esac
  home_dir="${test_root}/home"
  launchctl_bin="${test_bin_dir}/launchctl"
  pmset_bin="${test_bin_dir}/pmset"
  ps_bin="${test_bin_dir}/ps"
else
  [[ -z "${NOSLEEP4MAC_TEST_ROOT:-}" && -z "${NOSLEEP4MAC_TEST_BIN_DIR:-}" ]] \
    || die "test overrides require NOSLEEP4MAC_TEST_MODE=1"
  home_dir="${HOME:?HOME is required}"
  launchctl_bin="/bin/launchctl"
  pmset_bin="/usr/bin/pmset"
  ps_bin="/bin/ps"
fi

uname_bin="/usr/bin/uname"
id_bin="/usr/bin/id"
stat_bin="/usr/bin/stat"
plutil_bin="/usr/bin/plutil"
cmp_bin="/usr/bin/cmp"
mktemp_bin="/usr/bin/mktemp"
awk_bin="/usr/bin/awk"
grep_bin="/usr/bin/grep"
sed_bin="/usr/bin/sed"
sleep_bin="/bin/sleep"
lockf_bin="/usr/bin/lockf"

agent_dir="${home_dir}/Library/LaunchAgents"
plist_path="${agent_dir}/${label}.plist"
service_domain=""
service_target=""

for required_bin in \
  "${uname_bin}" "${id_bin}" "${stat_bin}" "${plutil_bin}" "${cmp_bin}" \
  "${mktemp_bin}" "${awk_bin}" "${grep_bin}" "${sed_bin}" "${sleep_bin}" \
  "${lockf_bin}" "${launchctl_bin}" "${pmset_bin}" "${ps_bin}"; do
  [[ -x "${required_bin}" ]] || die "required executable is unavailable: ${required_bin}" 127
done

[[ "$("${uname_bin}" -s)" == "Darwin" ]] \
  || die "nosleep4mac supports macOS only"

user_id="${NOSLEEP4MAC_TEST_UID:-$("${id_bin}" -u)}"
[[ "${user_id}" =~ ^[0-9]+$ ]] || die "current user ID is invalid"
[[ "${user_id}" != "0" ]] || die "run nosleep4mac as the logged-in user, not root"
[[ "${home_dir}" == /* && -d "${home_dir}" && ! -L "${home_dir}" ]] \
  || die "HOME must be an existing, non-symlinked absolute directory"

service_domain="gui/${user_id}"
service_target="${service_domain}/${label}"

render_plist() {
  cat <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.nosleep4mac.caffeinate-ac</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-s</string>
    </array>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLIST
}

path_owner() {
  "${stat_bin}" -f '%u' "$1"
}

path_mode() {
  "${stat_bin}" -f '%Lp' "$1"
}

mode_is_group_or_world_writable() {
  local mode="$1"
  (( (8#${mode} & 8#022) != 0 ))
}

validate_safe_directory() {
  local path="$1"
  local owner
  local mode

  [[ -d "${path}" && ! -L "${path}" ]] \
    || die "unsafe managed directory: ${path}"
  owner="$(path_owner "${path}")"
  mode="$(path_mode "${path}")"
  [[ "${owner}" == "${user_id}" ]] \
    || die "managed directory is not owned by the current user: ${path}"
  if mode_is_group_or_world_writable "${mode}"; then
    die "managed directory is group/world writable: ${path}"
  fi
  return 0
}

validate_safe_file() {
  local path="$1"
  local subject="${2:-managed plist}"
  local owner
  local mode

  [[ -f "${path}" && ! -L "${path}" ]] \
    || die "${subject} must be a non-symlinked regular file"
  owner="$(path_owner "${path}")"
  mode="$(path_mode "${path}")"
  [[ "${owner}" == "${user_id}" ]] \
    || die "${subject} is not owned by the current user"
  if mode_is_group_or_world_writable "${mode}"; then
    die "${subject} is group/world writable"
  fi
  return 0
}

plist_matches() {
  [[ -f "${plist_path}" && ! -L "${plist_path}" ]] || return 1
  "${cmp_bin}" -s "${plist_path}" <(render_plist)
}

job_loaded="false"
job_running="false"
job_pid=""
job_output=""

inspect_job() {
  "${launchctl_bin}" print "${service_domain}" >/dev/null 2>&1 \
    || die "current GUI launchd domain is unavailable"

  if job_output="$("${launchctl_bin}" print "${service_target}" 2>&1)"; then
    job_loaded="true"
    if printf '%s\n' "${job_output}" \
      | "${grep_bin}" -Eq '^[[:space:]]*state = running[[:space:]]*$'; then
      job_running="true"
    else
      job_running="false"
    fi
    # The awk program intentionally uses awk fields, not shell variables.
    # shellcheck disable=SC2016
    job_pid="$(
      printf '%s\n' "${job_output}" \
        | "${awk_bin}" '$1 == "pid" && $2 == "=" { print $3; exit }'
    )"
  else
    if printf '%s\n' "${job_output}" \
      | "${grep_bin}" -Eqi 'could not find service|service not found'; then
      job_loaded="false"
      job_running="false"
      job_pid=""
    else
      die "launchctl could not classify the managed service"
    fi
  fi
}

job_output_reports_absent() {
  local output="$1"

  printf '%s\n' "${output}" \
    | "${grep_bin}" -Eqi 'could not find service|service not found'
}

ensure_service_unloaded() {
  local output

  if output="$("${launchctl_bin}" print "${service_target}" 2>&1)"; then
    if ! "${launchctl_bin}" bootout "${service_target}" >/dev/null 2>&1; then
      return 1
    fi
  elif ! job_output_reports_absent "${output}"; then
    return 1
  fi

  if output="$("${launchctl_bin}" print "${service_target}" 2>&1)"; then
    return 1
  fi
  job_output_reports_absent "${output}"
}

power_source=""
assertions_output=""
assertion_active="false"
assertion_owned="false"
process_matches="false"

inspect_power_and_process() {
  local power_output
  local process_output=""

  power_output="$("${pmset_bin}" -g ps 2>&1)" \
    || die "pmset could not report the current power source"
  if printf '%s\n' "${power_output}" | "${grep_bin}" -q "'AC Power'"; then
    power_source="AC"
  elif printf '%s\n' "${power_output}" | "${grep_bin}" -q "'Battery Power'"; then
    power_source="Battery"
  else
    die "pmset returned an unrecognized power source"
  fi

  assertions_output="$("${pmset_bin}" -g assertions 2>&1)" \
    || die "pmset could not report power assertions"
  if printf '%s\n' "${assertions_output}" \
    | "${grep_bin}" -Eq 'PreventSystemSleep[[:space:]]+1'; then
    assertion_active="true"
  else
    assertion_active="false"
  fi

  assertion_owned="false"
  process_matches="false"
  if [[ "${job_running}" == "true" && "${job_pid}" =~ ^[0-9]+$ ]]; then
    process_output="$("${ps_bin}" -p "${job_pid}" -o command= 2>/dev/null || true)"
    process_output="$(
      printf '%s' "${process_output}" \
        | "${sed_bin}" -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
    )"
    [[ "${process_output}" == "/usr/bin/caffeinate -s" ]] \
      && process_matches="true"
    if printf '%s\n' "${assertions_output}" \
      | "${grep_bin}" -Eq "pid[[:space:]]+${job_pid}\\(caffeinate\\):.*PreventSystemSleep"; then
      assertion_owned="true"
    fi
  fi
}

plist_state="absent"

inspect_plist() {
  if [[ -L "${plist_path}" || ( -e "${plist_path}" && ! -f "${plist_path}" ) ]]; then
    plist_state="unsafe"
    return
  fi
  if [[ ! -e "${plist_path}" ]]; then
    plist_state="absent"
    return
  fi

  validate_safe_file "${plist_path}"
  if plist_matches; then
    if [[ "$(path_mode "${plist_path}")" == "644" ]]; then
      plist_state="canonical"
    else
      plist_state="mode-drift"
    fi
  else
    plist_state="content-drift"
  fi
}

healthy="false"

inspect_all() {
  inspect_plist
  inspect_job
  inspect_power_and_process

  healthy="false"
  if [[ "${plist_state}" == "canonical" \
    && "${job_loaded}" == "true" \
    && "${job_running}" == "true" \
    && "${process_matches}" == "true" ]]; then
    if [[ "${power_source}" == "Battery" \
      || ( "${assertion_active}" == "true" && "${assertion_owned}" == "true" ) ]]; then
      healthy="true"
    fi
  fi
}

print_status() {
  local overall="$1"
  local assertion_status="inactive"

  if [[ "${assertion_active}" == "true" && "${assertion_owned}" == "true" ]]; then
    assertion_status="active"
  elif [[ "${power_source}" == "Battery" ]]; then
    assertion_status="inactive-on-battery"
  fi

  printf 'status=%s\n' "${overall}"
  printf 'plist=%s\n' "${plist_state}"
  printf 'service=%s\n' "$(
    if [[ "${job_running}" == "true" ]]; then
      printf 'running'
    elif [[ "${job_loaded}" == "true" ]]; then
      printf 'loaded'
    else
      printf 'unloaded'
    fi
  )"
  printf 'pid=%s\n' "${job_pid:-none}"
  printf 'power=%s\n' "${power_source}"
  printf 'assertion=%s\n' "${assertion_status}"
}

if [[ "${check_only}" == "true" ]]; then
  init_output_style
  inspect_all
  if [[ "${healthy}" == "true" ]]; then
    print_status "healthy"
    exit 0
  fi
  print_status "needs-convergence"
  exit 1
fi

init_output_style
validate_safe_directory "${home_dir}"

library_dir="${home_dir}/Library"
if [[ ! -e "${library_dir}" ]]; then
  /bin/mkdir -m 700 -- "${library_dir}"
fi
validate_safe_directory "${library_dir}"

if [[ ! -e "${agent_dir}" ]]; then
  /bin/mkdir -m 700 -- "${agent_dir}"
fi
validate_safe_directory "${agent_dir}"

lock_file="${agent_dir}/.${label}.lock"
if [[ -e "${lock_file}" || -L "${lock_file}" ]]; then
  validate_safe_file "${lock_file}" "existing nosleep4mac lock"
fi
exec 9>"${lock_file}"
validate_safe_file "${lock_file}" "nosleep4mac lock"
if ! "${lockf_bin}" -s -t 0 9; then
  exec 9>&-
  die "another nosleep4mac convergence is already active"
fi
lock_held="true"

inspect_all
if [[ "${plist_state}" == "unsafe" ]]; then
  die "managed plist path is a symlink or non-regular file"
fi
if [[ "${job_loaded}" == "true" && "${plist_state}" == "absent" ]]; then
  die "managed launchd label is loaded without its managed plist"
fi

if [[ "${healthy}" == "true" ]]; then
  print_status "unchanged"
  log_success "nosleep4mac is already converged."
  exit 0
fi

previous_plist_existed="false"
previous_job_loaded="${job_loaded}"
previous_job_running="${job_running}"
plist_changed="false"
mode_changed="false"
result="repaired"

active_tmp_file="$("${mktemp_bin}" "${agent_dir}/.${label}.new.XXXXXX")"
render_plist >"${active_tmp_file}"
/bin/chmod 644 "${active_tmp_file}"
"${plutil_bin}" -lint "${active_tmp_file}" >/dev/null \
  || die "generated plist failed validation"

if [[ -e "${plist_path}" ]]; then
  previous_plist_existed="true"
  if [[ "${plist_state}" == "content-drift" ]]; then
    backup_file="$("${mktemp_bin}" "${agent_dir}/.${label}.backup.XXXXXX")"
    /bin/cp -p -- "${plist_path}" "${backup_file}"
    plist_changed="true"
    result="updated"
  elif [[ "${plist_state}" == "mode-drift" ]]; then
    mode_changed="true"
    result="updated"
  fi
else
  plist_changed="true"
  result="installed"
fi

rollback_changed_plist() {
  log_warn "Convergence failed; restoring prior managed state."
  if ! ensure_service_unloaded; then
    log_error "managed service could not be verified stopped; the current plist was left in place"
    return 1
  fi
  if [[ "${previous_plist_existed}" == "true" ]]; then
    if [[ -n "${backup_file}" && -e "${backup_file}" ]]; then
      if ! /bin/mv -f -- "${backup_file}" "${plist_path}"; then
        log_error "prior plist backup could not be restored"
        return 1
      fi
      backup_file=""
    fi
  else
    if ! /bin/rm -f -- "${plist_path}"; then
      log_error "failed new plist could not be removed"
      return 1
    fi
  fi
  if [[ "${previous_job_loaded}" == "true" && -e "${plist_path}" ]]; then
    if ! "${launchctl_bin}" bootstrap "${service_domain}" "${plist_path}" >/dev/null 2>&1; then
      log_error "prior plist was restored, but its prior launchd state could not be reloaded"
      return 1
    fi
    if [[ "${previous_job_running}" == "true" ]]; then
      if ! "${launchctl_bin}" kickstart -k "${service_target}" >/dev/null 2>&1; then
        log_error "prior launchd state was reloaded but could not be restarted"
        return 1
      fi
    fi
  fi
  return 0
}

if [[ "${plist_changed}" == "true" ]]; then
  rollback_on_exit="true"
  if [[ "${job_loaded}" == "true" ]]; then
    if ! ensure_service_unloaded; then
      die "could not stop the exact managed service before updating it"
    fi
  fi
  if ! /bin/mv -f -- "${active_tmp_file}" "${plist_path}"; then
    die "could not atomically replace the managed plist"
  fi
  active_tmp_file=""
elif [[ "${mode_changed}" == "true" ]]; then
  /bin/chmod 644 "${plist_path}"
  /bin/rm -f -- "${active_tmp_file}"
  active_tmp_file=""
else
  /bin/rm -f -- "${active_tmp_file}"
  active_tmp_file=""
fi

if [[ "${plist_changed}" == "true" || "${job_loaded}" == "false" ]]; then
  if ! "${launchctl_bin}" bootstrap "${service_domain}" "${plist_path}" >/dev/null 2>&1; then
    if ! rollback_changed_plist; then
      preserve_backup_on_exit="true"
    fi
    rollback_on_exit="false"
    die "could not bootstrap the managed service"
  fi
elif [[ "${process_matches}" != "true" ]]; then
  if ! ensure_service_unloaded; then
    die "could not stop the exact managed service before reloading its plist"
  fi
  if ! "${launchctl_bin}" bootstrap "${service_domain}" "${plist_path}" >/dev/null 2>&1; then
    die "could not reload the managed service from its canonical plist"
  fi
elif [[ "${job_running}" != "true" \
  || ( "${power_source}" == "AC" \
    && ( "${assertion_active}" != "true" || "${assertion_owned}" != "true" ) ) ]]; then
  if ! "${launchctl_bin}" kickstart -k "${service_target}" >/dev/null 2>&1; then
    die "could not restart the exact managed service"
  fi
fi

verified="false"
attempt=1
while [[ "${attempt}" -le 5 ]]; do
  inspect_all
  if [[ "${healthy}" == "true" ]]; then
    verified="true"
    break
  fi
  "${sleep_bin}" 1
  attempt=$((attempt + 1))
done

if [[ "${verified}" != "true" ]]; then
  if [[ "${plist_changed}" == "true" ]]; then
    if ! rollback_changed_plist; then
      preserve_backup_on_exit="true"
    fi
    rollback_on_exit="false"
  fi
  die "managed service did not reach the verified canonical state"
fi

rollback_on_exit="false"
print_status "${result}"
log_success "nosleep4mac convergence completed."
