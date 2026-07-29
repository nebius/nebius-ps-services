#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
helper="${script_dir}/nosleep4mac.sh"
task_temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nosleep4mac-test.XXXXXX")"
test_root="${task_temp_dir}/fixture"
test_home="${test_root}/home"
fake_bin="${task_temp_dir}/bin"
fake_state="${task_temp_dir}/state"
label="local.nosleep4mac.caffeinate-ac"
plist="${test_home}/Library/LaunchAgents/${label}.plist"
lock_file="${test_home}/Library/LaunchAgents/.${label}.lock"
lock_holder_pid=""

cleanup() {
  if [[ -n "${lock_holder_pid}" ]]; then
    kill "${lock_holder_pid}" 2>/dev/null || true
    wait "${lock_holder_pid}" 2>/dev/null || true
  fi
  if [[ -d "${task_temp_dir}" ]]; then
    find "${task_temp_dir}" -depth -delete
  fi
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local message="$3"
  [[ "${actual}" == "${expected}" ]] \
    || fail "${message}: expected '${expected}', got '${actual}'"
}

assert_contains() {
  local value="$1"
  local expected="$2"
  local message="$3"
  [[ "${value}" == *"${expected}"* ]] \
    || fail "${message}: missing '${expected}'"
}

mutating_launchctl_call_count() {
  awk '$1 == "bootout" || $1 == "bootstrap" || $1 == "kickstart" { count++ } END { print count + 0 }' \
    "${fake_state}/calls"
}

mkdir -p "${test_home}/Library/LaunchAgents" "${fake_bin}" "${fake_state}"
chmod 755 "${test_home}" "${test_home}/Library" "${test_home}/Library/LaunchAgents"
printf 'AC\n' >"${fake_state}/power"
printf '4200\n' >"${fake_state}/next_pid"

cat >"${fake_bin}/launchctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
state="${NOSLEEP4MAC_FAKE_STATE:?}"
printf '%s\n' "$*" >>"${state}/calls"
command="${1:?}"
shift
case "${command}" in
  print)
    target="${1:?}"
    if [[ "${target}" != */local.nosleep4mac.caffeinate-ac ]]; then
      printf 'domain = %s\n' "${target}"
      exit 0
    fi
    if [[ ! -f "${state}/loaded" ]]; then
      printf 'Could not find service\n' >&2
      exit 3
    fi
    if [[ -f "${state}/running" ]]; then
      printf 'state = running\n'
      printf 'pid = %s\n' "$(<"${state}/pid")"
    else
      printf 'state = waiting\n'
    fi
    ;;
  bootout)
    if [[ -f "${state}/fail-next-bootout" ]]; then
      rm -f -- "${state}/fail-next-bootout"
      exit 5
    fi
    rm -f -- "${state}/loaded" "${state}/running" "${state}/pid"
    ;;
  bootstrap)
    if [[ -f "${state}/fail-next-bootstrap" ]]; then
      rm -f -- "${state}/fail-next-bootstrap"
      exit 5
    fi
    if [[ -f "${state}/arm-assertion-read-failure" ]]; then
      rm -f -- "${state}/arm-assertion-read-failure"
      : >"${state}/fail-next-assertion-read"
    fi
    if [[ -f "${state}/arm-rollback-bootout-failure" ]]; then
      rm -f -- "${state}/arm-rollback-bootout-failure"
      : >"${state}/fail-next-bootout"
    fi
    pid="$(<"${state}/next_pid")"
    printf '%s\n' "$((pid + 1))" >"${state}/next_pid"
    : >"${state}/loaded"
    : >"${state}/running"
    printf '%s\n' "${pid}" >"${state}/pid"
    rm -f -- "${state}/stale-command"
    ;;
  kickstart)
    [[ -f "${state}/loaded" ]] || exit 3
    pid="$(<"${state}/next_pid")"
    printf '%s\n' "$((pid + 1))" >"${state}/next_pid"
    : >"${state}/running"
    printf '%s\n' "${pid}" >"${state}/pid"
    ;;
  *)
    exit 2
    ;;
esac
SH

cat >"${fake_bin}/pmset" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
state="${NOSLEEP4MAC_FAKE_STATE:?}"
case "${2:?}" in
  ps)
    if [[ "$(<"${state}/power")" == "AC" ]]; then
      printf "Now drawing from 'AC Power'\n"
    else
      printf "Now drawing from 'Battery Power'\n"
    fi
    ;;
  assertions)
    if [[ -f "${state}/fail-next-assertion-read" ]]; then
      rm -f -- "${state}/fail-next-assertion-read"
      exit 5
    fi
    if [[ "$(<"${state}/power")" == "AC" \
      && -f "${state}/running" \
      && ! -f "${state}/suppress-assertion" ]]; then
      pid="$(<"${state}/pid")"
      printf 'Assertion status system-wide:\n'
      printf '   PreventSystemSleep             1\n'
      printf '   pid %s(caffeinate): assertion PreventSystemSleep\n' "${pid}"
    else
      printf 'Assertion status system-wide:\n'
      printf '   PreventSystemSleep             0\n'
    fi
    ;;
  *)
    exit 2
    ;;
esac
SH

cat >"${fake_bin}/ps" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
state="${NOSLEEP4MAC_FAKE_STATE:?}"
requested_pid="${2:?}"
if [[ -f "${state}/running" && "$(<"${state}/pid")" == "${requested_pid}" ]]; then
  if [[ -f "${state}/stale-command" ]]; then
    printf '/usr/bin/caffeinate -i\n'
    exit 0
  fi
  printf '/usr/bin/caffeinate -s\n'
  exit 0
fi
exit 1
SH

chmod 755 "${fake_bin}/launchctl" "${fake_bin}/pmset" "${fake_bin}/ps"

run_helper() {
  NOSLEEP4MAC_TEST_MODE=1 \
  NOSLEEP4MAC_TEST_ROOT="${test_root}" \
  NOSLEEP4MAC_TEST_BIN_DIR="${fake_bin}" \
  NOSLEEP4MAC_FAKE_STATE="${fake_state}" \
  NOSLEEP4MAC_TEST_UID="$(id -u)" \
  NO_COLOR=1 \
    "${helper}" "$@"
}

if run_helper --check >/dev/null 2>&1; then
  fail "check unexpectedly succeeded before installation"
fi
[[ ! -e "${plist}" ]] || fail "check mutated the plist"

: >"${fake_state}/fail-next-bootstrap"
if run_helper >/dev/null 2>&1; then
  fail "first-install bootstrap failure unexpectedly succeeded"
fi
[[ ! -e "${plist}" ]] || fail "failed first install left a managed plist"
[[ ! -e "${fake_state}/loaded" ]] || fail "failed first install left the job loaded"

first_output="$(run_helper)"
assert_contains "${first_output}" "status=installed" "first convergence"
[[ -f "${plist}" && ! -L "${plist}" ]] || fail "first convergence did not create a regular plist"
assert_eq "644" "$(stat -f '%Lp' "${plist}")" "plist mode"
plutil -lint "${plist}" >/dev/null || fail "plist lint"

first_digest="$(shasum -a 256 "${plist}" | awk '{print $1}')"
first_mtime="$(stat -f '%m' "${plist}")"
first_inode="$(stat -f '%i' "${plist}")"
first_pid="$(<"${fake_state}/pid")"
first_call_count="$(wc -l <"${fake_state}/calls" | tr -d ' ')"
first_mutating_call_count="$(mutating_launchctl_call_count)"

second_output="$(run_helper)"
assert_contains "${second_output}" "status=unchanged" "second convergence"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" "no-op digest"
assert_eq "${first_mtime}" "$(stat -f '%m' "${plist}")" "no-op mtime"
assert_eq "${first_inode}" "$(stat -f '%i' "${plist}")" "no-op inode"
assert_eq "644" "$(stat -f '%Lp' "${plist}")" "no-op mode"
assert_eq "${first_pid}" "$(<"${fake_state}/pid")" "no-op pid"
second_call_count="$(wc -l <"${fake_state}/calls" | tr -d ' ')"
assert_eq "$((first_call_count + 2))" "${second_call_count}" "no-op launchctl calls"
assert_eq "${first_mutating_call_count}" "$(mutating_launchctl_call_count)" \
  "no-op mutating launchctl calls"

check_output="$(run_helper --check)"
assert_contains "${check_output}" "status=healthy" "healthy internal check"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" "check digest"
assert_eq "${first_mtime}" "$(stat -f '%m' "${plist}")" "check mtime"
assert_eq "${first_inode}" "$(stat -f '%i' "${plist}")" "check inode"
assert_eq "${first_pid}" "$(<"${fake_state}/pid")" "check pid"
assert_eq "${first_mutating_call_count}" "$(mutating_launchctl_call_count)" \
  "check mutating launchctl calls"

: >"${fake_state}/suppress-assertion"
if missing_assertion_output="$(run_helper --check)"; then
  fail "AC check unexpectedly accepted a missing owned assertion"
fi
assert_contains "${missing_assertion_output}" "status=needs-convergence" \
  "missing assertion internal check"
assert_eq "${first_mutating_call_count}" "$(mutating_launchctl_call_count)" \
  "missing assertion check mutating launchctl calls"
rm -f -- "${fake_state}/suppress-assertion"

: >"${fake_state}/stale-command"
stale_command_output="$(run_helper)"
assert_contains "${stale_command_output}" "status=repaired" "stale loaded command repair"
[[ ! -e "${fake_state}/stale-command" ]] \
  || fail "stale loaded command was not reloaded from the plist"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" \
  "stale loaded command repair preserves plist"

rm -f -- "${fake_state}/running"
stopped_output="$(run_helper)"
assert_contains "${stopped_output}" "status=repaired" "loaded-but-stopped repair"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" \
  "loaded-but-stopped repair preserves plist"

rm -f -- "${fake_state}/loaded" "${fake_state}/running" "${fake_state}/pid"
repair_output="$(run_helper)"
assert_contains "${repair_output}" "status=repaired" "unloaded repair"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" "repair preserves plist"

printf 'drifted\n' >"${plist}"
chmod 644 "${plist}"
update_output="$(run_helper)"
assert_contains "${update_output}" "status=updated" "drift convergence"
plutil -lint "${plist}" >/dev/null || fail "updated plist lint"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" "drift canonicalized"

printf '\n' >>"${plist}"
newline_update_output="$(run_helper)"
assert_contains "${newline_update_output}" "status=updated" "trailing-newline drift"
assert_eq "${first_digest}" "$(shasum -a 256 "${plist}" | awk '{print $1}')" \
  "trailing-newline drift canonicalized"

printf 'prior-content\n' >"${plist}"
chmod 600 "${plist}"
: >"${fake_state}/fail-next-bootstrap"
if run_helper >/dev/null 2>&1; then
  fail "bootstrap failure unexpectedly succeeded"
fi
assert_eq "prior-content" "$(<"${plist}")" "failed update rollback"
assert_eq "600" "$(stat -f '%Lp' "${plist}")" "failed update rollback mode"
[[ -f "${fake_state}/loaded" && -f "${fake_state}/running" ]] \
  || fail "failed update did not restore loaded state"

: >"${fake_state}/arm-assertion-read-failure"
if run_helper >/dev/null 2>&1; then
  fail "post-bootstrap assertion-read failure unexpectedly succeeded"
fi
assert_eq "prior-content" "$(<"${plist}")" "verification failure rollback"
assert_eq "600" "$(stat -f '%Lp' "${plist}")" "verification failure rollback mode"
[[ -f "${fake_state}/loaded" && -f "${fake_state}/running" ]] \
  || fail "verification failure did not restore loaded state"

printf 'prior-content\n' >"${plist}"
chmod 600 "${plist}"
: >"${fake_state}/arm-assertion-read-failure"
: >"${fake_state}/arm-rollback-bootout-failure"
if run_helper >/dev/null 2>&1; then
  fail "rollback bootout failure unexpectedly succeeded"
fi
[[ -f "${plist}" && ! -L "${plist}" ]] \
  || fail "rollback bootout failure removed the managed plist"
[[ -f "${fake_state}/loaded" ]] \
  || fail "rollback bootout failure unexpectedly unloaded the managed job"
backup_count="$(
  find "${test_home}/Library/LaunchAgents" \
    -name ".${label}.backup.*" \
    -type f \
    | wc -l \
    | tr -d ' '
)"
assert_eq "1" "${backup_count}" "rollback bootout failure retained prior backup"
run_helper >/dev/null

printf 'AC\n' >"${fake_state}/power"
run_helper >/dev/null
rm -f -- "${plist}"
ln -s "${fake_state}/foreign" "${plist}"
if run_helper >/dev/null 2>&1; then
  fail "symlink target unexpectedly succeeded"
fi
[[ -L "${plist}" ]] || fail "symlink target was modified"
rm -f -- "${plist}"
NOSLEEP4MAC_FAKE_STATE="${fake_state}" \
  "${fake_bin}/launchctl" bootout "gui/$(id -u)/${label}" >/dev/null

run_helper >/dev/null
lock_holder_ready="${fake_state}/lock-holder-ready"
(
  exec 8>"${lock_file}"
  /usr/bin/lockf -s -t 0 8
  : >"${lock_holder_ready}"
  while :; do
    :
  done
) &
lock_holder_pid="$!"
lock_wait_attempt=1
while [[ ! -e "${lock_holder_ready}" && "${lock_wait_attempt}" -le 20 ]]; do
  /bin/sleep 0.1
  lock_wait_attempt=$((lock_wait_attempt + 1))
done
[[ -e "${lock_holder_ready}" ]] || fail "concurrent lock holder did not start"
if run_helper >/dev/null 2>&1; then
  fail "concurrent convergence unexpectedly succeeded"
fi
kill "${lock_holder_pid}"
wait "${lock_holder_pid}" 2>/dev/null || true
lock_holder_pid=""
rm -f -- "${lock_holder_ready}"

crash_recovery_output="$(run_helper)"
assert_contains "${crash_recovery_output}" "status=unchanged" "crash lock recovery"
[[ ! -e "${lock_file}" ]] || fail "lock file was not cleaned after convergence"

printf 'Battery\n' >"${fake_state}/power"
battery_output="$(run_helper --check)"
assert_contains "${battery_output}" "status=healthy" "battery check"
assert_contains "${battery_output}" "assertion=inactive-on-battery" "battery assertion"

if run_helper --apply >/dev/null 2>&1; then
  fail "unsupported --apply argument unexpectedly succeeded"
fi

printf 'PASS: nosleep4mac isolated convergence tests\n'
