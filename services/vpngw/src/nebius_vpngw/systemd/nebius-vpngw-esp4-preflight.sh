#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${VPNGW_ESP4_STATE_DIR:-/var/lib/nebius-vpngw}"
MODPROBE_DIR="${VPNGW_ESP4_MODPROBE_DIR:-/etc/modprobe.d}"
REBOOT_REQUIRED_FILE="${VPNGW_ESP4_REBOOT_REQUIRED_FILE:-/var/run/reboot-required}"
READY_MARKER="${STATE_DIR}/esp4-ready"
REBOOT_PENDING_MARKER="${STATE_DIR}/esp4-reboot-pending"
CHANGED_MARKER="${STATE_DIR}/esp4-modprobe-policy-updated"
LOG_TAG="nebius-vpngw-esp4"
REBOOT_NEEDED_RC=75

BLOCK_PATTERN='^[[:space:]]*(install[[:space:]]+esp4[[:space:]]+/usr/bin/(false|true)|install[[:space:]]+esp4[[:space:]]+/bin/(false|true)|blacklist[[:space:]]+esp4)([[:space:]]|$)'

log() {
  local message="$1"
  printf '[%s] %s\n' "${LOG_TAG}" "${message}"
  if command -v logger >/dev/null 2>&1; then
    logger -t "${LOG_TAG}" -- "${message}" || true
  fi
}

usage() {
  cat <<'EOF'
Usage:
  nebius-vpngw-esp4-preflight.sh --prepare
  nebius-vpngw-esp4-preflight.sh --verify

Options:
  --prepare  Remove only esp4 module deny rules and request reboot when needed
  --verify   Verify esp4 is loadable and write the ready marker
  -h, --help Show help

Environment for tests:
  VPNGW_ESP4_MODPROBE_DIR
  VPNGW_ESP4_STATE_DIR
  VPNGW_ESP4_REBOOT_REQUIRED_FILE
  VPNGW_ESP4_SKIP_INITRAMFS=1
  VPNGW_ESP4_SKIP_MODPROBE=1
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 && -z "${VPNGW_ESP4_ALLOW_NON_ROOT:-}" ]]; then
    log "must run as root"
    exit 1
  fi
}

ensure_state_dir() {
  mkdir -p "${STATE_DIR}"
}

comment_esp4_blocks_in_file() {
  local file="$1"
  local tmp=""
  local rc=0

  tmp="$(mktemp)"
  if awk -v pattern="${BLOCK_PATTERN}" '
    $0 ~ pattern && $0 !~ /^# nebius-vpngw: disabled ESP4 block:/ {
      print "# nebius-vpngw: disabled ESP4 block: " $0
      changed = 1
      next
    }
    { print }
    END {
      if (changed) {
        exit 10
      }
    }
  ' "${file}" > "${tmp}"; then
    rm -f "${tmp}"
    return 1
  else
    rc=$?
    if [[ "${rc}" -ne 10 ]]; then
      rm -f "${tmp}"
      return "${rc}"
    fi
  fi

  cp -a "${file}" "${file}.nebius-vpngw-esp4.$(date +%Y%m%d%H%M%S).bak"
  cat "${tmp}" > "${file}"
  rm -f "${tmp}"
  return 0
}

remediate_modprobe_policy() {
  local changed=1
  local file=""

  shopt -s nullglob
  for file in "${MODPROBE_DIR}"/*.conf; do
    [[ -f "${file}" ]] || continue
    if comment_esp4_blocks_in_file "${file}"; then
      log "disabled esp4 block in ${file}"
      changed=0
    fi
  done
  shopt -u nullglob

  return "${changed}"
}

update_initramfs_if_needed() {
  if [[ "${VPNGW_ESP4_SKIP_INITRAMFS:-}" == "1" ]]; then
    log "skipping update-initramfs because VPNGW_ESP4_SKIP_INITRAMFS=1"
    return 0
  fi
  if ! command -v update-initramfs >/dev/null 2>&1; then
    log "update-initramfs is not available"
    return 1
  fi
  update-initramfs -u -k all
}

verify_esp4_loadable() {
  if [[ "${VPNGW_ESP4_SKIP_MODPROBE:-}" == "1" ]]; then
    log "skipping modprobe because VPNGW_ESP4_SKIP_MODPROBE=1"
    return 0
  fi
  if ! command -v modprobe >/dev/null 2>&1; then
    log "modprobe is not available"
    return 1
  fi
  modprobe esp4
}

write_ready() {
  rm -f "${REBOOT_PENDING_MARKER}"
  touch "${READY_MARKER}"
  log "esp4 is ready"
}

write_reboot_pending() {
  rm -f "${READY_MARKER}"
  touch "${REBOOT_PENDING_MARKER}"
  log "reboot is required before esp4 readiness can be confirmed"
}

prepare() {
  local changed=0

  require_root
  ensure_state_dir

  if remediate_modprobe_policy; then
    changed=1
    touch "${CHANGED_MARKER}"
    update_initramfs_if_needed
  fi

  if [[ "${changed}" -eq 1 || -f "${REBOOT_REQUIRED_FILE}" ]]; then
    write_reboot_pending
    exit "${REBOOT_NEEDED_RC}"
  fi

  if verify_esp4_loadable; then
    write_ready
    return 0
  fi

  log "esp4 is not loadable"
  return 1
}

verify() {
  require_root
  ensure_state_dir

  if verify_esp4_loadable; then
    write_ready
    return 0
  fi

  log "esp4 is not loadable"
  return 1
}

main() {
  local action="${1:-}"

  case "${action}" in
    --prepare)
      prepare
      ;;
    --verify)
      verify
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
