#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
HELPER_SRC="${PROJECT_ROOT}/src/nebius_vpngw/systemd/nebius-vpngw-esp4-preflight.sh"
REMOTE_TMP_HELPER="/tmp/nebius-vpngw-esp4-preflight.sh"

LOCAL_CONFIG_FILE="${LOCAL_CONFIG_FILE:-}"
SSH_USER="${VPNGW_SSH_USER:-ubuntu}"
IDENTITY_FILE="${VPNGW_SSH_KEY:-}"
YES=0
DRY_RUN=0
WAIT_TIMEOUT=900
HOSTS=()
SUMMARY=()

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
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} $*${S_RESET}" >&2
}

log_warn() {
  printf '%b\n' "${S_YELLOW}$*${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}$*${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}$*${S_RESET}"
}

show_usage() {
  local script_ref="${0}"

  if [[ "${script_ref}" != /* && "${script_ref}" != ./* ]]; then
    script_ref="./${script_ref}"
  fi

  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref}${S_RESET} ${S_DIM}--host <user@ip> [--host <user@ip> ...]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref}${S_RESET} ${S_DIM}--local-config-file <path>${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '  %-28s %s\n' "--host TARGET" "Gateway SSH target; repeatable. If no user is supplied, --ssh-user is used."
  printf '  %-28s %s\n' "-c, --local-config-file PATH" "Discover gateway public IPs from gateway_group.external_ips."
  printf '  %-28s %s\n' "--ssh-user USER" "Default SSH user for bare IP hosts (default: ubuntu)."
  printf '  %-28s %s\n' "--identity-file PATH" "SSH private key path."
  printf '  %-28s %s\n' "--wait-timeout SECONDS" "Time to wait for SSH after reboot (default: 900)."
  printf '  %-28s %s\n' "--dry-run" "Print planned actions without connecting or rebooting."
  printf '  %-28s %s\n' "--yes" "Skip typed REBOOT confirmation."
  printf '  %-28s %s\n' "-h, --help" "Show help."
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --host ubuntu@203.0.113.10${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --local-config-file nebius-vpngw.config.yaml${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --host 203.0.113.10 --ssh-user ubuntu --identity-file ~/.ssh/id_ed25519${S_RESET}"
}

expand_path() {
  local path="$1"
  case "${path}" in
    \~/*) printf '%s/%s' "${HOME}" "${path#\~/}" ;;
    *) printf '%s' "${path}" ;;
  esac
}

require_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ || "${value}" == "0" ]]; then
    log_error "${label} must be a positive integer"
    exit 1
  fi
}

parse_args() {
  while (($#)); do
    case "$1" in
      --host)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "--host requires a value"
          exit 1
        fi
        HOSTS+=("$2")
        shift 2
        ;;
      -c|--local-config-file)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "--local-config-file requires a value"
          exit 1
        fi
        LOCAL_CONFIG_FILE="$2"
        shift 2
        ;;
      --ssh-user)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "--ssh-user requires a value"
          exit 1
        fi
        SSH_USER="$2"
        shift 2
        ;;
      --identity-file)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "--identity-file requires a value"
          exit 1
        fi
        IDENTITY_FILE="$2"
        shift 2
        ;;
      --wait-timeout)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
          log_error "--wait-timeout requires a value"
          exit 1
        fi
        WAIT_TIMEOUT="$2"
        require_integer "${WAIT_TIMEOUT}" "--wait-timeout"
        shift 2
        ;;
      --yes)
        YES=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      *)
        log_error "Unknown option: $1"
        show_usage >&2
        exit 1
        ;;
    esac
  done
}

discover_config_identity() {
  local config_file="$1"

  if [[ -n "${IDENTITY_FILE}" ]]; then
    return 0
  fi

  IDENTITY_FILE="$(
    python3 - "${config_file}" <<'PY'
from pathlib import Path
import sys

try:
    import yaml
except Exception as exc:
    print(f"failed to import PyYAML: {exc}", file=sys.stderr)
    raise SystemExit(2)

cfg = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
vm_spec = (cfg.get("gateway_group") or {}).get("vm_spec") or {}
print(str(vm_spec.get("ssh_private_key_path") or ""))
PY
  )"
}

discover_config_hosts() {
  local config_file="$1"

  if [[ ! -f "${config_file}" ]]; then
    log_error "Config file not found: ${config_file}"
    exit 1
  fi

  discover_config_identity "${config_file}"

  while IFS= read -r target; do
    [[ -n "${target}" ]] || continue
    HOSTS+=("${target}")
  done < <(
    python3 - "${config_file}" "${SSH_USER}" <<'PY'
from pathlib import Path
import sys

try:
    import yaml
except Exception as exc:
    print(f"failed to import PyYAML: {exc}", file=sys.stderr)
    raise SystemExit(2)

cfg = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
gateway_group = cfg.get("gateway_group") or {}
vm_spec = gateway_group.get("vm_spec") or {}
ssh_user = str(vm_spec.get("ssh_username") or sys.argv[2] or "").strip()
external_ips = gateway_group.get("external_ips") or []

targets = []
for entry in external_ips:
    if isinstance(entry, str):
        candidates = [entry]
    elif isinstance(entry, list):
        candidates = entry
    else:
        continue
    for candidate in candidates:
        ip = str(candidate or "").strip()
        if not ip or ip.startswith("${"):
            continue
        targets.append(f"{ssh_user}@{ip}" if ssh_user and "@" not in ip else ip)

for target in dict.fromkeys(targets):
    print(target)
PY
  )
}

normalize_target() {
  local target="$1"

  if [[ "${target}" == *@* ]]; then
    printf '%s' "${target}"
  else
    printf '%s@%s' "${SSH_USER}" "${target}"
  fi
}

build_ssh_cmd() {
  local target="$1"
  SSH_CMD=(ssh)
  if [[ -n "${IDENTITY_FILE}" ]]; then
    SSH_CMD+=(-i "$(expand_path "${IDENTITY_FILE}")")
  fi
  SSH_CMD+=(
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=10
    -o LogLevel=ERROR
    "${target}"
  )
}

build_scp_cmd() {
  SCP_CMD=(scp)
  if [[ -n "${IDENTITY_FILE}" ]]; then
    SCP_CMD+=(-i "$(expand_path "${IDENTITY_FILE}")")
  fi
  SCP_CMD+=(
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=10
    -o LogLevel=ERROR
  )
}

get_remote_boot_id() {
  local target="$1"
  local boot_id=""

  build_ssh_cmd "${target}"
  boot_id="$("${SSH_CMD[@]}" "cat /proc/sys/kernel/random/boot_id" 2>/dev/null)"
  boot_id="${boot_id//$'\r'/}"
  boot_id="${boot_id//$'\n'/}"

  if [[ -z "${boot_id}" ]]; then
    return 1
  fi

  printf '%s' "${boot_id}"
}

print_targets() {
  printf '%b\n' "${S_BOLD}Target gateway(s):${S_RESET}"
  local target=""
  for target in "${HOSTS[@]}"; do
    printf '  - %s\n' "$(normalize_target "${target}")"
  done
}

confirm_reboot() {
  local reply=""

  print_targets
  printf '\n'
  printf '%b\n' "${S_YELLOW}This will upgrade packages and reboot each VPN gateway serially.${S_RESET}"
  printf '%b\n' "${S_YELLOW}VPN traffic through each target will be disrupted for a few minutes.${S_RESET}"

  if [[ "${DRY_RUN}" -eq 1 || "${YES}" -eq 1 ]]; then
    return 0
  fi

  printf '%b' "${S_BOLD}Type REBOOT to continue:${S_RESET} "
  if ! read -r reply; then
    printf '\n' >&2
    log_error "Confirmation aborted."
    exit 1
  fi

  if [[ "${reply}" != "REBOOT" ]]; then
    log_warn "Cancelled. No gateway was changed."
    exit 1
  fi
}

validate_inputs() {
  if [[ -n "${LOCAL_CONFIG_FILE}" ]]; then
    discover_config_hosts "${LOCAL_CONFIG_FILE}"
  fi

  if [[ "${#HOSTS[@]}" -eq 0 ]]; then
    log_error "No gateway targets found. Use --host or --local-config-file."
    exit 1
  fi

  if [[ ! -f "${HELPER_SRC}" ]]; then
    log_error "ESP4 helper not found: ${HELPER_SRC}"
    exit 1
  fi
}

upload_helper() {
  local target="$1"

  build_scp_cmd
  "${SCP_CMD[@]}" "${HELPER_SRC}" "${target}:${REMOTE_TMP_HELPER}"
}

run_remote_prepare_and_reboot() {
  local target="$1"

  build_ssh_cmd "${target}"
  "${SSH_CMD[@]}" "sudo bash -s" <<'REMOTE'
set -euo pipefail

install -m 0755 -o root -g root /tmp/nebius-vpngw-esp4-preflight.sh /usr/local/bin/nebius-vpngw-esp4-preflight.sh
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

set +e
/usr/local/bin/nebius-vpngw-esp4-preflight.sh --prepare
rc=$?
set -e
if [[ "${rc}" -ne 0 && "${rc}" -ne 75 ]]; then
  exit "${rc}"
fi

systemctl reboot --no-block
REMOTE
}

wait_for_rebooted_ssh() {
  local target="$1"
  local previous_boot_id="$2"
  local deadline=$((SECONDS + WAIT_TIMEOUT))
  local current_boot_id=""

  while ((SECONDS < deadline)); do
    if current_boot_id="$(get_remote_boot_id "${target}")"; then
      if [[ "${current_boot_id}" != "${previous_boot_id}" ]]; then
        return 0
      fi
    fi
    sleep 5
  done

  return 1
}

run_remote_verify_and_restart() {
  local target="$1"

  build_ssh_cmd "${target}"
  "${SSH_CMD[@]}" "sudo bash -s" <<'REMOTE'
set -euo pipefail

/usr/local/bin/nebius-vpngw-esp4-preflight.sh --verify

if systemctl list-unit-files strongswan-starter.service >/dev/null 2>&1; then
  systemctl restart strongswan-starter
elif systemctl list-unit-files strongswan.service >/dev/null 2>&1; then
  systemctl restart strongswan
fi

systemctl restart nebius-vpngw-agent

if systemctl list-unit-files strongswan-starter.service >/dev/null 2>&1; then
  systemctl is-active strongswan-starter
elif systemctl list-unit-files strongswan.service >/dev/null 2>&1; then
  systemctl is-active strongswan
else
  echo "strongSwan service not found"
fi
systemctl is-active nebius-vpngw-agent
REMOTE
}

repair_target() {
  local raw_target="$1"
  local target=""
  local previous_boot_id=""

  target="$(normalize_target "${raw_target}")"

  printf '%b\n' "${S_BOLD}Repairing ${target}${S_RESET}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry run: would upload ${HELPER_SRC}"
    log_note "Dry run: would run apt-get update && apt-get upgrade -y"
    log_note "Dry run: would run ESP4 preflight, reboot, confirm boot ID changed, verify, and restart services"
    SUMMARY+=("${target}: dry-run")
    return 0
  fi

  log_note "Uploading ESP4 helper"
  upload_helper "${target}"

  log_note "Capturing current boot ID"
  if ! previous_boot_id="$(get_remote_boot_id "${target}")"; then
    log_error "Could not read boot ID from ${target}"
    return 1
  fi

  log_note "Running package upgrade, ESP4 preflight, and reboot"
  run_remote_prepare_and_reboot "${target}"

  log_note "Waiting for SSH after reboot"
  sleep 10
  if ! wait_for_rebooted_ssh "${target}" "${previous_boot_id}"; then
    log_error "Timed out waiting for ${target} to reboot and return over SSH"
    return 1
  fi

  log_note "Verifying ESP4 and restarting gateway services"
  run_remote_verify_and_restart "${target}"

  log_success "${target}: ESP4 ready and gateway services restarted"
  SUMMARY+=("${target}: repaired")
}

main() {
  init_output_style
  parse_args "$@"
  validate_inputs
  confirm_reboot

  local target=""
  for target in "${HOSTS[@]}"; do
    repair_target "${target}"
  done

  printf '\n%b\n' "${S_BOLD}Summary:${S_RESET}"
  for target in "${SUMMARY[@]}"; do
    printf '  - %s\n' "${target}"
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
