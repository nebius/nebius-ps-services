#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

STATUS_ONLY=0
ROTATE_EXISTING_TUNNELS=0
ACTIVE_ACCOUNT=""
DEFAULT_VPN_GATEWAY_NAME="ha-gw-nebius"
DEFAULT_CLOUD_ROUTER_NAME="cr-nebius-ha"
DEFAULT_EXTERNAL_GW_NAME="nebius-bgp-vpngw2"
DEFAULT_TUNNEL1_NAME="gcp-ha-tunnel-1"
DEFAULT_TUNNEL2_NAME="gcp-ha-tunnel-2"
DEFAULT_IFACE1_NAME="gcp-ha-if-1"
DEFAULT_IFACE2_NAME="gcp-ha-if-2"
DEFAULT_PEER1_NAME="gcp-ha-peer-1"
DEFAULT_PEER2_NAME="gcp-ha-peer-2"
LOCAL_CONFIG_FILE="${LOCAL_CONFIG_FILE:-}"
GCP_PROJECT_ID="${GCP_PROJECT_ID:-}"
NEBIUS_PUBLIC_IP="${NEBIUS_PUBLIC_IP:-}"
REGION="${REGION:-}"
NETWORK="${NETWORK:-default}"
VPN_GATEWAY_NAME="${VPN_GATEWAY_NAME:-${DEFAULT_VPN_GATEWAY_NAME}}"
CLOUD_ROUTER_NAME="${CLOUD_ROUTER_NAME:-${DEFAULT_CLOUD_ROUTER_NAME}}"
CLOUD_ROUTER_ASN="${CLOUD_ROUTER_ASN:-64514}"
NEBIUS_ASN="${NEBIUS_ASN:-}"
EXTERNAL_GW_NAME="${EXTERNAL_GW_NAME:-${DEFAULT_EXTERNAL_GW_NAME}}"
TUNNEL1_NAME="${TUNNEL1_NAME:-${DEFAULT_TUNNEL1_NAME}}"
TUNNEL2_NAME="${TUNNEL2_NAME:-${DEFAULT_TUNNEL2_NAME}}"
IFACE1_NAME="${IFACE1_NAME:-${DEFAULT_IFACE1_NAME}}"
IFACE2_NAME="${IFACE2_NAME:-${DEFAULT_IFACE2_NAME}}"
PEER1_NAME="${PEER1_NAME:-${DEFAULT_PEER1_NAME}}"
PEER2_NAME="${PEER2_NAME:-${DEFAULT_PEER2_NAME}}"
TUN1_CIDR="${TUN1_CIDR:-169.254.10.0/30}"
TUN1_NEBIUS_IP="${TUN1_NEBIUS_IP:-169.254.10.1}"
TUN1_GCP_IP="${TUN1_GCP_IP:-169.254.10.2}"
TUN2_CIDR="${TUN2_CIDR:-169.254.11.0/30}"
TUN2_NEBIUS_IP="${TUN2_NEBIUS_IP:-169.254.11.1}"
TUN2_GCP_IP="${TUN2_GCP_IP:-169.254.11.2}"
ACTIVE_ADVERTISED_ROUTE_PRIORITY="${ACTIVE_ADVERTISED_ROUTE_PRIORITY:-0}"
PASSIVE_ADVERTISED_ROUTE_PRIORITY="${PASSIVE_ADVERTISED_ROUTE_PRIORITY:-100}"
PSK1="${PSK1:-}"
PSK2="${PSK2:-}"
POSITIONAL_ARGS=()

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

network_cidr() {
  local cidr="$1"

  python3 - "${cidr}" <<'PY'
import ipaddress
import sys

print(ipaddress.ip_network(sys.argv[1], strict=False), end="")
PY
}

print_help_entry() {
  local label="$1"
  local description="$2"
  local color="${3:-${S_YELLOW}}"
  local width="${4:-34}"
  local padding=2

  if (( ${#label} < width )); then
    padding=$((width - ${#label}))
  fi

  printf '  %b%s%b' "${color}" "${label}" "${S_RESET}"
  printf '%*s%s\n' "${padding}" '' "${description}"
}

confirm_create() {
  local reply=""

  printf '\n'
  printf '%b\n' "${S_BOLD}Ready to create/update GCP VPN resources:${S_RESET}"
  printf '  GCP project:      %s\n' "${GCP_PROJECT_ID}"
  printf '  GCP region:       %s\n' "${REGION}"
  printf '  VPN gateway:      %s\n' "${VPN_GATEWAY_NAME}"
  printf '  Cloud Router:     %s\n' "${CLOUD_ROUTER_NAME}"
  printf '  GCP Cloud Router ASN: %s\n' "${CLOUD_ROUTER_ASN}"
  printf '  External GW:      %s\n' "${EXTERNAL_GW_NAME}"
  printf '  Nebius public IP: %s\n' "${NEBIUS_PUBLIC_IP}"
  printf '  Nebius peer ASN:  %s\n' "${NEBIUS_ASN}"
  printf '  Tunnel 1 name:    %s\n' "${TUNNEL1_NAME}"
  printf '  Tunnel 2 name:    %s\n' "${TUNNEL2_NAME}"
  printf '\n'
  printf '%b' "${S_YELLOW}Continue with VPN creation on this GCP project? [y/N] ${S_RESET}"

  if ! read -r reply; then
    printf '\n' >&2
    log_error "Confirmation aborted."
    exit 1
  fi

  case "${reply}" in
    [yY]|[yY][eE][sS])
      log_note "Proceeding with GCP VPN creation."
      ;;
    *)
      log_warn "Cancelled. No GCP resources were created or updated."
      exit 1
      ;;
  esac
}

show_usage() {
  local script_ref="${0}"

  if [[ "${script_ref}" != /* && "${script_ref}" != ./* ]]; then
    script_ref="./${script_ref}"
  fi

  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref}${S_RESET} ${S_DIM}<gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref}${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  print_help_entry "-c, --local-config-file PATH" "Read Nebius config for auto-discovery" "${S_YELLOW}"
  print_help_entry "--gcp-project-id ID" "Override GCP project ID" "${S_YELLOW}"
  print_help_entry "--region REGION" "Override GCP region" "${S_YELLOW}"
  print_help_entry "--nebius-public-ip IP" "Override Nebius public IP" "${S_YELLOW}"
  print_help_entry "--nebius-asn ASN" "Override Nebius peer ASN" "${S_YELLOW}"
  print_help_entry "--status" "Print GCP-side status only; make no changes" "${S_YELLOW}"
  print_help_entry "--rotate-existing-tunnels" "Recreate existing tunnels, interfaces, peers, and PSKs" "${S_YELLOW}"
  print_help_entry "-h, --help" "Show help" "${S_YELLOW}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Resolution Order:${S_RESET}"
  printf '%b\n' "  1. Positional args: ${S_CYAN}<gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>${S_RESET}"
  printf '%b\n' "  2. Explicit flags: ${S_CYAN}--gcp-project-id${S_RESET}, ${S_CYAN}--region${S_RESET}, ${S_CYAN}--nebius-public-ip${S_RESET}, ${S_CYAN}--nebius-asn${S_RESET}, ${S_CYAN}--local-config-file${S_RESET}"
  printf '%b\n' "  3. Environment: ${S_CYAN}GCP_PROJECT_ID${S_RESET}, ${S_CYAN}REGION${S_RESET}, ${S_CYAN}NEBIUS_PUBLIC_IP${S_RESET}, ${S_CYAN}NEBIUS_ASN${S_RESET}, ${S_CYAN}LOCAL_CONFIG_FILE${S_RESET}"
  printf '%b\n' "  4. Auto-discovery: active gcloud project, local YAML config, first allocated Nebius external IP, local_asn"
  printf '\n'

  printf '%b\n' "${S_BOLD}Useful Env Vars:${S_RESET}"
  print_help_entry "PSK1, PSK2" "Supply or override tunnel PSKs" "${S_CYAN}"
  print_help_entry "NEBIUS_ASN" "Override Nebius peer ASN" "${S_CYAN}"
  print_help_entry "CLOUD_ROUTER_ASN" "Set the GCP Cloud Router ASN for new routers" "${S_CYAN}"
  print_help_entry "REGION, NETWORK" "Override GCP region or VPC network" "${S_CYAN}"
  print_help_entry "CLOUD_ROUTER_NAME" "Override the default Cloud Router name" "${S_CYAN}"
  print_help_entry "VPN_GATEWAY_NAME" "Override the default HA VPN gateway name" "${S_CYAN}"
  print_help_entry "EXTERNAL_GW_NAME" "Override the default external VPN gateway name" "${S_CYAN}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --local-config-file <local-config-file> --region <gcp-region>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}GCP_PROJECT_ID=<gcp-project-id> REGION=<gcp-region> NEBIUS_ASN=<nebius-asn> ${script_ref} --nebius-public-ip <nebius-public-ip>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}PSK1=<tunnel-1-psk> PSK2=<tunnel-2-psk> ${script_ref} --rotate-existing-tunnels <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --status --region <gcp-region>${S_RESET}"
  printf '%b\n' "  ${S_CYAN}${script_ref} --status --local-config-file <local-config-file>${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  - This script requires the ${S_CYAN}gcloud${S_RESET} CLI to be installed and in ${S_CYAN}PATH${S_RESET}."
  printf '%b\n' "  - If gcloud is not authenticated, the script runs ${S_CYAN}gcloud auth login${S_RESET}."
  printf '%b\n' "  - Create mode requires a resolved GCP project, region, Nebius public IP, and Nebius peer ASN."
  printf '%b\n' "  - Status mode requires a resolved GCP project and region. Nebius public IP is optional there and can come from ${S_CYAN}--nebius-public-ip${S_RESET} or ${S_CYAN}--local-config-file${S_RESET}."
  printf '%b\n' "  - The Nebius workflow is fixed to one public peer IP and exactly two tunnels on one GCP HA VPN gateway: one active and one passive."
  printf '%b\n' "  - Google supports one HA VPN gateway and one Cloud Router serving multiple peer sites in the same region/VPC."
  printf '%b\n' "  - If the selected HA VPN gateway already exists, the script prompts to reuse it or create a new gateway."
  printf '%b\n' "  - If the selected Cloud Router already exists, the script prompts to reuse it or create a new one."
  printf '%b\n' "  - The required ${S_CYAN}<nebius-asn>${S_RESET} input is the Nebius peer ASN. The GCP Cloud Router ASN is separate: existing router ASN when reused, or ${S_CYAN}CLOUD_ROUTER_ASN${S_RESET} when a new router is created."
  printf '%b\n' "  - If that Cloud Router already has Cloud NAT attached, the script warns that GCP documents this as unsupported for HA VPN. It still lets you reuse the router if you choose it."
  printf '%b\n' "  - The script creates or reuses the GCP HA VPN gateway, Cloud Router, external VPN gateway, tunnels, interfaces, and BGP peers. It does not create Cloud NAT."
  printf '%b\n' "  - By default, connection-scoped names are derived from the Nebius public IP so reruns target the same external gateway, tunnels, interfaces, and peers."
  printf '%b\n' "  - If the selected gateway or router already hosts another site and the default tunnel or peer names would collide, the script prompts for a new connection resource prefix."
  printf '%b\n' "  - With ${S_CYAN}--rotate-existing-tunnels${S_RESET}, the script removes the matching BGP peers and router interfaces first, then recreates the tunnel pair with new PSKs."
  printf '%b\n' "  - Create mode prints the resolved inputs, then asks for confirmation."
  printf '%b\n' "  - Successful runs print tunnel PSKs and a YAML snippet for the Nebius config."
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

ensure_gcloud_installed() {
  if ! command -v gcloud >/dev/null 2>&1; then
    log_error "Google Cloud CLI (gcloud) is not installed or not in PATH."
    log_note "Install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
  fi
}

resource_exists() {
  gcloud "$@" >/dev/null 2>&1
}

validate_ipv4() {
  local ip="$1"
  local octet=""
  local parts=()

  [[ "${ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1

  IFS='.' read -r -a parts <<<"${ip}"
  [[ "${#parts[@]}" -eq 4 ]] || return 1

  for octet in "${parts[@]}"; do
    [[ "${octet}" =~ ^[0-9]+$ ]] || return 1
    (( octet >= 0 && octet <= 255 )) || return 1
  done
}

validate_asn() {
  local asn="$1"

  [[ "${asn}" =~ ^[0-9]+$ ]] || return 1
  (( asn >= 1 && asn <= 4294967295 ))
}

validate_gcp_resource_name() {
  local name="$1"

  [[ "${name}" =~ ^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$ ]]
}

require_python_yaml() {
  require_cmd "python3"
  if ! python3 - <<'PY' >/dev/null 2>&1
import yaml  # noqa: F401
PY
  then
    log_error "python3 is available, but PyYAML is not. Activate the project virtualenv or install PyYAML."
    exit 1
  fi
}

gen_psk() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr -d '\n'
  else
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32), end="")
PY
  fi
}

ensure_auth() {
  local active
  active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  if [[ -n "${active}" ]]; then
    ACTIVE_ACCOUNT="${active}"
    log_success "gcloud authenticated as ${ACTIVE_ACCOUNT}"
    return
  fi

  log_note "No active gcloud session found. Running: gcloud auth login"
  gcloud auth login

  active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  if [[ -z "${active}" ]]; then
    log_error "gcloud login completed, but no active account is configured."
    exit 1
  fi

  ACTIVE_ACCOUNT="${active}"
  log_success "gcloud authenticated as ${ACTIVE_ACCOUNT}"
}

resolve_gcp_project_id() {
  local detected=""
  local project_count=0
  local candidate=""
  local line=""

  if [[ -n "${GCP_PROJECT_ID}" ]]; then
    log_note "Using GCP project ${GCP_PROJECT_ID}"
    return
  fi

  detected="$(gcloud config get-value project 2>/dev/null || true)"
  detected="$(printf '%s' "${detected}" | tr -d '\r')"
  if [[ "${detected}" == "(unset)" ]]; then
    detected=""
  fi

  if [[ -n "${detected}" ]]; then
    GCP_PROJECT_ID="${detected}"
    log_success "Using active gcloud project ${GCP_PROJECT_ID}"
    return
  fi

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    candidate="${line}"
    project_count=$((project_count + 1))
    if [[ "${project_count}" -gt 1 ]]; then
      break
    fi
  done < <(gcloud projects list --format='value(projectId)' 2>/dev/null || true)

  if [[ "${project_count}" -eq 1 ]]; then
    GCP_PROJECT_ID="${candidate}"
    gcloud config set project "${GCP_PROJECT_ID}" >/dev/null 2>&1 || true
    log_warn "No active gcloud project was configured; using the only accessible project: ${GCP_PROJECT_ID}"
    return
  fi

  log_error "Unable to determine the GCP project automatically."
  log_note "Pass <gcp-project-id>, use --gcp-project-id, set GCP_PROJECT_ID, or run: gcloud config set project <PROJECT_ID>"
  exit 1
}

config_value() {
  local config_file="$1"
  local query="$2"

  python3 - "${config_file}" "${query}" <<'PY'
import re
import sys
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
query = sys.argv[2]
data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

if query == "first_external_ip":
    external_ips = ((data.get("gateway_group") or {}).get("external_ips") or [])
    for row in external_ips:
        if isinstance(row, list):
            for value in row:
                if isinstance(value, str):
                    value = value.strip()
                    if value and not re.fullmatch(r"\$\{[A-Za-z0-9_]+\}", value):
                        print(value)
                        raise SystemExit(0)
        elif isinstance(row, str):
            value = row.strip()
            if value and not re.fullmatch(r"\$\{[A-Za-z0-9_]+\}", value):
                print(value)
                raise SystemExit(0)
    raise SystemExit(1)

if query == "local_asn":
    local_asn = (data.get("gateway") or {}).get("local_asn")
    if local_asn is None:
        raise SystemExit(1)
    print(local_asn)
    raise SystemExit(0)

raise SystemExit(2)
PY
}

detect_local_config_file() {
  local candidate=""
  local file_count=0
  local external_ip_count=0
  local matched_file=""
  local line=""

  if [[ -n "${LOCAL_CONFIG_FILE}" ]]; then
    if [[ ! -f "${LOCAL_CONFIG_FILE}" ]]; then
      log_error "Local config file not found: ${LOCAL_CONFIG_FILE}"
      exit 1
    fi
    printf '%s\n' "${LOCAL_CONFIG_FILE}"
    return
  fi

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    file_count=$((file_count + 1))
    candidate="${line#./}"
  done < <(find . -maxdepth 1 -type f -name '*.config.yaml' -print | sort)

  if [[ "${file_count}" -eq 1 ]]; then
    printf '%s\n' "${candidate}"
    return
  fi

  if [[ "${file_count}" -gt 1 ]]; then
    while IFS= read -r line; do
      [[ -n "${line}" ]] || continue
      candidate="${line#./}"
      if config_value "${candidate}" "first_external_ip" >/dev/null 2>&1; then
        matched_file="${candidate}"
        external_ip_count=$((external_ip_count + 1))
      fi
    done < <(find . -maxdepth 1 -type f -name '*.config.yaml' -print | sort)

    if [[ "${external_ip_count}" -eq 1 ]]; then
      printf '%s\n' "${matched_file}"
      return
    fi
  fi

  log_error "Unable to determine the local config file automatically."
  log_note "Pass --local-config-file <path> or set LOCAL_CONFIG_FILE."
  exit 1
}

resolve_local_config_file() {
  require_python_yaml
  LOCAL_CONFIG_FILE="$(detect_local_config_file)"
  log_note "Using local config file ${LOCAL_CONFIG_FILE}"
}

resolve_nebius_public_ip() {
  if [[ -n "${NEBIUS_PUBLIC_IP}" ]]; then
    if ! validate_ipv4 "${NEBIUS_PUBLIC_IP}"; then
      log_error "Invalid Nebius public IP: ${NEBIUS_PUBLIC_IP}"
      exit 1
    fi
    log_note "Using Nebius public IP ${NEBIUS_PUBLIC_IP}"
    return
  fi

  resolve_local_config_file

  if ! NEBIUS_PUBLIC_IP="$(config_value "${LOCAL_CONFIG_FILE}" "first_external_ip" 2>/dev/null)"; then
    log_error "No allocated Nebius public IP was found in ${LOCAL_CONFIG_FILE}."
    log_note "Run: nebius-vpngw prep-network --local-config-file ${LOCAL_CONFIG_FILE}"
    log_note "Or pass <nebius-public-ip> / --nebius-public-ip."
    exit 1
  fi

  if ! validate_ipv4 "${NEBIUS_PUBLIC_IP}"; then
    log_error "Detected an invalid Nebius public IP in ${LOCAL_CONFIG_FILE}: ${NEBIUS_PUBLIC_IP}"
    exit 1
  fi

  log_success "Using Nebius public IP ${NEBIUS_PUBLIC_IP}"
}

resolve_nebius_asn() {
  if [[ -n "${NEBIUS_ASN}" ]]; then
    if ! validate_asn "${NEBIUS_ASN}"; then
      log_error "Invalid Nebius ASN: ${NEBIUS_ASN}"
      exit 1
    fi
    log_note "Using Nebius ASN ${NEBIUS_ASN}"
    return
  fi

  if [[ -n "${LOCAL_CONFIG_FILE}" ]] && NEBIUS_ASN="$(config_value "${LOCAL_CONFIG_FILE}" "local_asn" 2>/dev/null)"; then
    if ! validate_asn "${NEBIUS_ASN}"; then
      log_error "Invalid Nebius ASN in ${LOCAL_CONFIG_FILE}: ${NEBIUS_ASN}"
      exit 1
    fi
    log_success "Using Nebius ASN ${NEBIUS_ASN} from ${LOCAL_CONFIG_FILE}"
    return
  fi

  log_error "Unable to determine the Nebius ASN."
  log_note "Pass <nebius-asn>, use --nebius-asn, set NEBIUS_ASN, or define gateway.local_asn in the local config."
  exit 1
}

resolve_region() {
  if [[ -n "${REGION}" ]]; then
    log_note "Using GCP region ${REGION}"
    return
  fi

  log_error "Unable to determine the GCP region."
  log_note "Pass <gcp-region>, use --region, or set REGION."
  exit 1
}

compose_resource_name() {
  local base="${1%-}"
  local suffix="$2"
  local max_len=63
  local reserve=$(( ${#suffix} + 1 ))

  if (( ${#base} + reserve > max_len )); then
    base="${base:0:$((max_len - reserve))}"
    base="${base%-}"
  fi

  printf '%s-%s\n' "${base}" "${suffix}"
}

suggest_unique_gateway_name() {
  local base="$1"
  local suffix=2
  local candidate=""

  while true; do
    candidate="$(compose_resource_name "${base}" "${suffix}")"
    if ! resource_exists compute vpn-gateways describe "${candidate}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
      printf '%s\n' "${candidate}"
      return
    fi
    suffix=$((suffix + 1))
  done
}

suggest_unique_router_name() {
  local base="$1"
  local suffix=2
  local candidate=""

  while true; do
    candidate="$(compose_resource_name "${base}" "${suffix}")"
    if ! resource_exists compute routers describe "${candidate}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
      printf '%s\n' "${candidate}"
      return
    fi
    suffix=$((suffix + 1))
  done
}

apply_new_gateway_name() {
  local new_gateway_name="$1"

  VPN_GATEWAY_NAME="${new_gateway_name}"

  if [[ "${TUNNEL1_NAME}" == "${DEFAULT_TUNNEL1_NAME}" ]]; then
    TUNNEL1_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "tunnel-1")"
  fi
  if [[ "${TUNNEL2_NAME}" == "${DEFAULT_TUNNEL2_NAME}" ]]; then
    TUNNEL2_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "tunnel-2")"
  fi
  if [[ "${IFACE1_NAME}" == "${DEFAULT_IFACE1_NAME}" ]]; then
    IFACE1_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "if-1")"
  fi
  if [[ "${IFACE2_NAME}" == "${DEFAULT_IFACE2_NAME}" ]]; then
    IFACE2_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "if-2")"
  fi
  if [[ "${PEER1_NAME}" == "${DEFAULT_PEER1_NAME}" ]]; then
    PEER1_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "peer-1")"
  fi
  if [[ "${PEER2_NAME}" == "${DEFAULT_PEER2_NAME}" ]]; then
    PEER2_NAME="$(compose_resource_name "${VPN_GATEWAY_NAME}" "peer-2")"
  fi

  log_note "Using new HA VPN gateway ${VPN_GATEWAY_NAME}"
  log_note "Tunnel names: ${TUNNEL1_NAME}, ${TUNNEL2_NAME}"
  log_note "Router interfaces: ${IFACE1_NAME}, ${IFACE2_NAME}"
  log_note "BGP peers: ${PEER1_NAME}, ${PEER2_NAME}"
}

apply_new_cloud_router_name() {
  local new_router_name="$1"

  CLOUD_ROUTER_NAME="${new_router_name}"
  log_note "Using Cloud Router ${CLOUD_ROUTER_NAME}"
  log_note "Cloud NAT is not created by this script. If you need a separate NAT on the new router, create it explicitly after tunnel setup."
}

adopt_existing_cloud_router_asn() {
  local existing_asn="$1"

  if [[ -n "${existing_asn}" && "${CLOUD_ROUTER_ASN}" != "${existing_asn}" ]]; then
    log_note "Using existing Cloud Router ASN ${existing_asn} for ${CLOUD_ROUTER_NAME}."
    CLOUD_ROUTER_ASN="${existing_asn}"
  fi
}

resource_basename() {
  local ref="${1:-}"

  if [[ -z "${ref}" ]]; then
    printf '\n'
    return
  fi

  printf '%s\n' "${ref##*/}"
}

list_contains_token() {
  local haystack="$1"
  local token="$2"
  local normalized=""

  normalized="$(printf '%s' "${haystack}" | tr ',[:space:]' ';')"
  normalized=";${normalized};"
  [[ "${normalized}" == *";${token};"* ]]
}

connection_prefix_base() {
  printf 'nebius-%s\n' "${NEBIUS_PUBLIC_IP//./-}"
}

connection_names_are_default() {
  [[ "${EXTERNAL_GW_NAME}" == "${DEFAULT_EXTERNAL_GW_NAME}" ]] && \
    [[ "${TUNNEL1_NAME}" == "${DEFAULT_TUNNEL1_NAME}" ]] && \
    [[ "${TUNNEL2_NAME}" == "${DEFAULT_TUNNEL2_NAME}" ]] && \
    [[ "${IFACE1_NAME}" == "${DEFAULT_IFACE1_NAME}" ]] && \
    [[ "${IFACE2_NAME}" == "${DEFAULT_IFACE2_NAME}" ]] && \
    [[ "${PEER1_NAME}" == "${DEFAULT_PEER1_NAME}" ]] && \
    [[ "${PEER2_NAME}" == "${DEFAULT_PEER2_NAME}" ]]
}

set_connection_prefix_names() {
  local prefix="$1"

  EXTERNAL_GW_NAME="$(compose_resource_name "${prefix}" "peer")"
  TUNNEL1_NAME="$(compose_resource_name "${prefix}" "tunnel-1")"
  TUNNEL2_NAME="$(compose_resource_name "${prefix}" "tunnel-2")"
  IFACE1_NAME="$(compose_resource_name "${prefix}" "if-1")"
  IFACE2_NAME="$(compose_resource_name "${prefix}" "if-2")"
  PEER1_NAME="$(compose_resource_name "${prefix}" "peer-1")"
  PEER2_NAME="$(compose_resource_name "${prefix}" "peer-2")"
}

initialize_connection_names() {
  if [[ -n "${NEBIUS_PUBLIC_IP}" ]] && connection_names_are_default; then
    set_connection_prefix_names "$(connection_prefix_base)"
  fi
}

connection_prefix_in_use() {
  local prefix="$1"
  local ext_name=""
  local tunnel1_name=""
  local tunnel2_name=""
  local iface1_name=""
  local iface2_name=""
  local peer1_name=""
  local peer2_name=""
  local router_interfaces=""
  local router_peers=""

  ext_name="$(compose_resource_name "${prefix}" "peer")"
  tunnel1_name="$(compose_resource_name "${prefix}" "tunnel-1")"
  tunnel2_name="$(compose_resource_name "${prefix}" "tunnel-2")"
  iface1_name="$(compose_resource_name "${prefix}" "if-1")"
  iface2_name="$(compose_resource_name "${prefix}" "if-2")"
  peer1_name="$(compose_resource_name "${prefix}" "peer-1")"
  peer2_name="$(compose_resource_name "${prefix}" "peer-2")"

  if resource_exists compute external-vpn-gateways describe "${ext_name}" --project "${GCP_PROJECT_ID}"; then
    return 0
  fi
  if resource_exists compute vpn-tunnels describe "${tunnel1_name}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    return 0
  fi
  if resource_exists compute vpn-tunnels describe "${tunnel2_name}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    return 0
  fi

  if resource_exists compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    router_interfaces="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(interfaces.name)' 2>/dev/null || true)"
    router_peers="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name)' 2>/dev/null || true)"
    if list_contains_token "${router_interfaces}" "${iface1_name}" || \
      list_contains_token "${router_interfaces}" "${iface2_name}" || \
      list_contains_token "${router_peers}" "${peer1_name}" || \
      list_contains_token "${router_peers}" "${peer2_name}"; then
      return 0
    fi
  fi

  return 1
}

suggest_unique_connection_prefix() {
  local base="$1"
  local suffix=2
  local candidate="${base}"

  while connection_prefix_in_use "${candidate}"; do
    candidate="$(compose_resource_name "${base}" "${suffix}")"
    suffix=$((suffix + 1))
  done

  printf '%s\n' "${candidate}"
}

apply_connection_prefix() {
  local prefix="$1"

  set_connection_prefix_names "${prefix}"

  log_note "Using connection resource prefix ${prefix}"
  log_note "External VPN gateway: ${EXTERNAL_GW_NAME}"
  log_note "Tunnel names: ${TUNNEL1_NAME}, ${TUNNEL2_NAME}"
  log_note "Router interfaces: ${IFACE1_NAME}, ${IFACE2_NAME}"
  log_note "BGP peers: ${PEER1_NAME}, ${PEER2_NAME}"
}

router_has_interface() {
  local interface_name="$1"
  local router_interfaces=""

  router_interfaces="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(interfaces.name)' 2>/dev/null || true)"
  list_contains_token "${router_interfaces}" "${interface_name}"
}

router_has_peer() {
  local peer_name="$1"
  local router_peers=""

  router_peers="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name)' 2>/dev/null || true)"
  list_contains_token "${router_peers}" "${peer_name}"
}

remove_bgp_peer_if_present() {
  local peer_name="$1"

  if router_has_peer "${peer_name}"; then
    log_warn "Removing BGP peer ${peer_name}"
    gcloud compute routers remove-bgp-peer "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --peer-name "${peer_name}"
  fi
}

remove_router_interface_if_present() {
  local interface_name="$1"

  if router_has_interface "${interface_name}"; then
    log_warn "Removing router interface ${interface_name}"
    gcloud compute routers remove-interface "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --interface-name "${interface_name}"
  fi
}

connection_conflict_report() {
  local report=""
  local existing_ip=""
  local existing_redundancy_type=""
  local existing_interface_ids=""
  local existing_interface_count=0
  local tunnel_state=""
  local tunnel_peer_ext=""
  local tunnel_vpn_gw=""
  local tunnel_router=""
  local tunnel_iface=""
  local tunnel_peer_iface=""
  local router_interfaces=""
  local router_peers=""
  local iface_name=""
  local iface_range=""
  local iface_tunnel=""
  local peer_name=""
  local peer_iface_name=""
  local peer_ip=""
  local peer_asn=""
  local peer_priority=""

  if resource_exists compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}"; then
    existing_ip="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='value(interfaces[0].ipAddress)' 2>/dev/null || true)"
    existing_redundancy_type="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='value(redundancyType)' 2>/dev/null || true)"
    existing_interface_ids="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](interfaces[].id)' 2>/dev/null || true)"
    if [[ -n "${existing_interface_ids}" ]]; then
      existing_interface_count="$(awk -F',' '{print NF}' <<<"${existing_interface_ids}")"
    fi
    if [[ "${existing_ip}" != "${NEBIUS_PUBLIC_IP}" || "${existing_redundancy_type}" != "SINGLE_IP_INTERNALLY_REDUNDANT" || "${existing_interface_count}" -ne 1 ]]; then
      report+="  - External VPN gateway '${EXTERNAL_GW_NAME}' already represents a different peer or gateway shape.\n"
    fi
  fi

  if resource_exists compute vpn-tunnels describe "${TUNNEL1_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tunnel_state="$(gcloud compute vpn-tunnels describe "${TUNNEL1_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](peerExternalGateway,vpnGateway,router,vpnGatewayInterface,peerExternalGatewayInterface)' 2>/dev/null || true)"
    IFS=',' read -r tunnel_peer_ext tunnel_vpn_gw tunnel_router tunnel_iface tunnel_peer_iface <<<"${tunnel_state}"
    if [[ "$(resource_basename "${tunnel_peer_ext}")" != "${EXTERNAL_GW_NAME}" || \
      "$(resource_basename "${tunnel_vpn_gw}")" != "${VPN_GATEWAY_NAME}" || \
      "$(resource_basename "${tunnel_router}")" != "${CLOUD_ROUTER_NAME}" || \
      "${tunnel_iface}" != "0" || \
      "${tunnel_peer_iface}" != "0" ]]; then
      report+="  - Tunnel '${TUNNEL1_NAME}' already exists but is attached to different gateway, router, or peer resources.\n"
    fi
  fi

  if resource_exists compute vpn-tunnels describe "${TUNNEL2_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tunnel_state="$(gcloud compute vpn-tunnels describe "${TUNNEL2_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](peerExternalGateway,vpnGateway,router,vpnGatewayInterface,peerExternalGatewayInterface)' 2>/dev/null || true)"
    IFS=',' read -r tunnel_peer_ext tunnel_vpn_gw tunnel_router tunnel_iface tunnel_peer_iface <<<"${tunnel_state}"
    if [[ "$(resource_basename "${tunnel_peer_ext}")" != "${EXTERNAL_GW_NAME}" || \
      "$(resource_basename "${tunnel_vpn_gw}")" != "${VPN_GATEWAY_NAME}" || \
      "$(resource_basename "${tunnel_router}")" != "${CLOUD_ROUTER_NAME}" || \
      "${tunnel_iface}" != "1" || \
      "${tunnel_peer_iface}" != "0" ]]; then
      report+="  - Tunnel '${TUNNEL2_NAME}' already exists but is attached to different gateway, router, or peer resources.\n"
    fi
  fi

  if resource_exists compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    router_interfaces="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](interfaces.name,interfaces.ipRange,interfaces.linkedVpnTunnel)' 2>/dev/null || true)"
    while IFS=',' read -r iface_name iface_range iface_tunnel; do
      [[ -n "${iface_name}" ]] || continue
      case "${iface_name}" in
        "${IFACE1_NAME}")
          if [[ "${iface_range}" != "${TUN1_GCP_IP}/30" || "$(resource_basename "${iface_tunnel}")" != "${TUNNEL1_NAME}" ]]; then
            report+="  - Router interface '${IFACE1_NAME}' already exists with a different IP range or linked tunnel.\n"
          fi
          ;;
        "${IFACE2_NAME}")
          if [[ "${iface_range}" != "${TUN2_GCP_IP}/30" || "$(resource_basename "${iface_tunnel}")" != "${TUNNEL2_NAME}" ]]; then
            report+="  - Router interface '${IFACE2_NAME}' already exists with a different IP range or linked tunnel.\n"
          fi
          ;;
      esac
    done <<<"${router_interfaces}"

    router_peers="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](bgpPeers.name,bgpPeers.interfaceName,bgpPeers.peerIpAddress,bgpPeers.peerAsn,bgpPeers.advertisedRoutePriority)' 2>/dev/null || true)"
    while IFS=',' read -r peer_name peer_iface_name peer_ip peer_asn peer_priority; do
      [[ -n "${peer_name}" ]] || continue
      case "${peer_name}" in
        "${PEER1_NAME}")
          if [[ "${peer_iface_name}" != "${IFACE1_NAME}" || "${peer_ip}" != "${TUN1_NEBIUS_IP}" || "${peer_asn}" != "${NEBIUS_ASN}" || "${peer_priority}" != "${ACTIVE_ADVERTISED_ROUTE_PRIORITY}" ]]; then
            report+="  - BGP peer '${PEER1_NAME}' already exists with a different interface, peer IP, ASN, or route priority.\n"
          fi
          ;;
        "${PEER2_NAME}")
          if [[ "${peer_iface_name}" != "${IFACE2_NAME}" || "${peer_ip}" != "${TUN2_NEBIUS_IP}" || "${peer_asn}" != "${NEBIUS_ASN}" || "${peer_priority}" != "${PASSIVE_ADVERTISED_ROUTE_PRIORITY}" ]]; then
            report+="  - BGP peer '${PEER2_NAME}' already exists with a different interface, peer IP, ASN, or route priority.\n"
          fi
          ;;
      esac
    done <<<"${router_peers}"
  fi

  printf '%b' "${report}"
}

choose_connection_resource_names() {
  local conflict_report=""
  local suggested_prefix=""
  local entered_prefix=""

  conflict_report="$(connection_conflict_report)"
  if [[ -z "${conflict_report}" ]]; then
    return
  fi

  printf '\n'
  printf '%b\n' "${S_BOLD}Connection resource name conflicts detected:${S_RESET}"
  printf '%b' "${conflict_report}"
  printf '\n'
  log_note "The selected HA VPN gateway and Cloud Router can still be reused."
  log_note "This Nebius site needs its own external VPN gateway, tunnel, interface, and BGP peer names."

  suggested_prefix="$(suggest_unique_connection_prefix "$(connection_prefix_base)")"
  while true; do
    printf '%b' "${S_YELLOW}Enter a new connection resource prefix [${suggested_prefix}]: ${S_RESET}"
    if ! read -r entered_prefix; then
      printf '\n' >&2
      log_error "Connection resource selection aborted."
      exit 1
    fi

    if [[ -z "${entered_prefix}" ]]; then
      entered_prefix="${suggested_prefix}"
    fi

    if ! validate_gcp_resource_name "${entered_prefix}"; then
      log_warn "Invalid connection prefix '${entered_prefix}'. Use lowercase letters, numbers, and hyphens, starting with a letter."
      continue
    fi

    if connection_prefix_in_use "${entered_prefix}"; then
      log_warn "The connection prefix '${entered_prefix}' would still collide with existing resources."
      suggested_prefix="$(suggest_unique_connection_prefix "${entered_prefix}")"
      continue
    fi

    apply_connection_prefix "${entered_prefix}"
    return
  done
}

choose_vpn_gateway_name() {
  local reply=""
  local suggested_name=""
  local entered_name=""
  local confirm_new=""

  if ! resource_exists compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    return
  fi

  printf '\n'
  printf '%b\n' "${S_BOLD}Existing HA VPN gateway detected:${S_RESET}"
  printf '  Gateway: %s\n' "${VPN_GATEWAY_NAME}"
  printf '  Region:  %s\n' "${REGION}"
  printf '\n'
  log_note "Google supports one HA VPN gateway connecting to multiple remote sites."
  log_note "Default best practice: reuse the existing HA VPN gateway and Cloud Router when the region, VPC, and ASN fit."
  log_note "Create a new HA VPN gateway only when you want isolation, a different region/VPC, or additional scale/capacity."
  printf '\n'

  while true; do
    printf '%b' "${S_YELLOW}Reuse this gateway or create a new one? [R/n] ${S_RESET}"
    if ! read -r reply; then
      printf '\n' >&2
      log_error "Gateway selection aborted."
      exit 1
    fi

    case "${reply}" in
      ""|[rR]|[rR][eE][uU][sS][eE]|[yY]|[yY][eE][sS])
        log_note "Reusing existing HA VPN gateway ${VPN_GATEWAY_NAME}."
        return
        ;;
      [nN]|[nN][eE][wW])
        printf '\n'
        log_warn "A separate HA VPN gateway is usually an isolation or scale choice, not a requirement for another peer site."
        log_warn "Google recommends active-active when you deploy multiple HA VPN gateways. Active-passive across multiple gateways can delay failover until all active tunnels on all gateways have failed."
        printf '%b' "${S_YELLOW}Create a new HA VPN gateway anyway? [y/N] ${S_RESET}"
        if ! read -r confirm_new; then
          printf '\n' >&2
          log_error "Gateway selection aborted."
          exit 1
        fi
        case "${confirm_new}" in
          [yY]|[yY][eE][sS])
            ;;
          *)
            log_note "Keeping the existing HA VPN gateway ${VPN_GATEWAY_NAME}."
            return
            ;;
        esac

        suggested_name="$(suggest_unique_gateway_name "${VPN_GATEWAY_NAME}")"
        while true; do
          printf '%b' "${S_YELLOW}Enter new HA VPN gateway name [${suggested_name}]: ${S_RESET}"
          if ! read -r entered_name; then
            printf '\n' >&2
            log_error "Gateway selection aborted."
            exit 1
          fi

          if [[ -z "${entered_name}" ]]; then
            entered_name="${suggested_name}"
          fi

          if ! validate_gcp_resource_name "${entered_name}"; then
            log_warn "Invalid GCP resource name '${entered_name}'. Use lowercase letters, numbers, and hyphens, starting with a letter."
            continue
          fi

          if resource_exists compute vpn-gateways describe "${entered_name}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
            log_warn "HA VPN gateway '${entered_name}' already exists in ${REGION}."
            suggested_name="$(suggest_unique_gateway_name "${entered_name}")"
            continue
          fi

          apply_new_gateway_name "${entered_name}"
          return
        done
        ;;
      *)
        log_warn "Please answer 'r' to reuse the existing gateway or 'n' to create a new one."
        ;;
    esac
  done
}

choose_cloud_router_name() {
  local reply=""
  local suggested_name=""
  local entered_name=""
  local existing_asn=""
  local existing_nats=""

  if ! resource_exists compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    return
  fi

  existing_asn="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgp.asn)' 2>/dev/null || true)"
  existing_nats="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](nats[].name)' 2>/dev/null || true)"

  printf '\n'
  printf '%b\n' "${S_BOLD}Existing Cloud Router detected:${S_RESET}"
  printf '  Router: %s\n' "${CLOUD_ROUTER_NAME}"
  printf '  Region: %s\n' "${REGION}"
  if [[ -n "${existing_asn}" ]]; then
    printf '  ASN:    %s\n' "${existing_asn}"
  fi
  if [[ -n "${existing_nats}" ]]; then
    printf '  NAT:    %s\n' "${existing_nats}"
  else
    printf '  NAT:    <none>\n'
  fi
  printf '\n'
  log_note "Default best practice for this Nebius workflow: reuse the same Cloud Router when the region, VPC, and ASN fit."
  if [[ -n "${existing_nats}" ]]; then
    log_warn "GCP documentation says a Cloud Router used for HA VPN should not already be used for Cloud NAT."
    log_warn "The script still lets you reuse this router if you choose it, but treat that as outside the documented support path."
  fi
  if [[ -n "${existing_asn}" && "${existing_asn}" != "${CLOUD_ROUTER_ASN}" ]]; then
    log_warn "Existing GCP Cloud Router ASN is ${existing_asn}."
    log_warn "The script's new-router GCP ASN setting is ${CLOUD_ROUTER_ASN}; it is used only if you create a new Cloud Router."
    log_warn "If you reuse this router, GCP will keep using ASN ${existing_asn} for all BGP sessions on that router, and the Nebius YAML will use it as bgp.remote_asn."
  fi
  printf '\n'

  while true; do
    printf '%b' "${S_YELLOW}Reuse this Cloud Router or create a new one? [R/n] ${S_RESET}"
    if ! read -r reply; then
      printf '\n' >&2
      log_error "Cloud Router selection aborted."
      exit 1
    fi

    case "${reply}" in
      ""|[rR]|[rR][eE][uU][sS][eE]|[yY]|[yY][eE][sS])
        adopt_existing_cloud_router_asn "${existing_asn}"
        log_note "Reusing Cloud Router ${CLOUD_ROUTER_NAME}."
        return
        ;;
      [nN]|[nN][eE][wW])
        suggested_name="$(suggest_unique_router_name "${CLOUD_ROUTER_NAME}")"
        while true; do
          printf '%b' "${S_YELLOW}Enter new Cloud Router name [${suggested_name}]: ${S_RESET}"
          if ! read -r entered_name; then
            printf '\n' >&2
            log_error "Cloud Router selection aborted."
            exit 1
          fi

          if [[ -z "${entered_name}" ]]; then
            entered_name="${suggested_name}"
          fi

          if ! validate_gcp_resource_name "${entered_name}"; then
            log_warn "Invalid GCP resource name '${entered_name}'. Use lowercase letters, numbers, and hyphens, starting with a letter."
            continue
          fi

          if resource_exists compute routers describe "${entered_name}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
            log_warn "Cloud Router '${entered_name}' already exists in ${REGION}."
            suggested_name="$(suggest_unique_router_name "${entered_name}")"
            continue
          fi

          apply_new_cloud_router_name "${entered_name}"
          return
        done
        ;;
      *)
        log_warn "Please answer 'r' to reuse the existing Cloud Router or 'n' to create a new one."
        ;;
    esac
  done
}

status_report() {
  local gw_exists="false"
  local router_exists="false"
  local ext_exists="false"
  local gcp_ip0=""
  local gcp_ip1=""
  local router_asn="${CLOUD_ROUTER_ASN}"
  local ext_ip="${NEBIUS_PUBLIC_IP:-}"
  local tun1_status="MISSING"
  local tun2_status="MISSING"
  local iface1_range=""
  local iface2_range=""
  local peer1_ip=""
  local peer2_ip=""
  local peer1_asn=""
  local tun1_cidr="${TUN1_CIDR}"
  local tun2_cidr="${TUN2_CIDR}"
  local tun1_gcp_ip="${TUN1_GCP_IP}"
  local tun2_gcp_ip="${TUN2_GCP_IP}"
  local tun1_nebius_ip="${TUN1_NEBIUS_IP}"
  local tun2_nebius_ip="${TUN2_NEBIUS_IP}"
  local psk1_display="${PSK1:-existing: no display}"
  local psk2_display="${PSK2:-existing: no display}"
  local neb_asn="${NEBIUS_ASN}"

  if resource_exists compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    gw_exists="true"
    gcp_ip0="$(gcloud compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(vpnInterfaces[0].ipAddress)' 2>/dev/null || true)"
    gcp_ip1="$(gcloud compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(vpnInterfaces[1].ipAddress)' 2>/dev/null || true)"
  fi

  if resource_exists compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    router_exists="true"
    router_asn="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgp.asn)' 2>/dev/null || true)"
    iface1_range="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(interfaces.name,interfaces.ipRange)' 2>/dev/null | awk -v n="${IFACE1_NAME}" '$1==n {print $2; exit}')"
    iface2_range="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(interfaces.name,interfaces.ipRange)' 2>/dev/null | awk -v n="${IFACE2_NAME}" '$1==n {print $2; exit}')"
    peer1_ip="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name,bgpPeers.peerIpAddress)' 2>/dev/null | awk -v n="${PEER1_NAME}" '$1==n {print $2; exit}')"
    peer2_ip="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name,bgpPeers.peerIpAddress)' 2>/dev/null | awk -v n="${PEER2_NAME}" '$1==n {print $2; exit}')"
    peer1_asn="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name,bgpPeers.peerAsn)' 2>/dev/null | awk -v n="${PEER1_NAME}" '$1==n {print $2; exit}')"
  fi

  if resource_exists compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}"; then
    ext_exists="true"
    ext_ip="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='value(interfaces[0].ipAddress)' 2>/dev/null || true)"
  fi

  if resource_exists compute vpn-tunnels describe "${TUNNEL1_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tun1_status="$(gcloud compute vpn-tunnels describe "${TUNNEL1_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(status)' 2>/dev/null || true)"
  fi
  if resource_exists compute vpn-tunnels describe "${TUNNEL2_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tun2_status="$(gcloud compute vpn-tunnels describe "${TUNNEL2_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(status)' 2>/dev/null || true)"
  fi

  if [[ -n "${iface1_range}" ]]; then
    tun1_cidr="$(network_cidr "${iface1_range}")"
    tun1_gcp_ip="${iface1_range%/*}"
  fi
  if [[ -n "${iface2_range}" ]]; then
    tun2_cidr="$(network_cidr "${iface2_range}")"
    tun2_gcp_ip="${iface2_range%/*}"
  fi
  if [[ -n "${peer1_ip}" ]]; then
    tun1_nebius_ip="${peer1_ip}"
  fi
  if [[ -n "${peer2_ip}" ]]; then
    tun2_nebius_ip="${peer2_ip}"
  fi
  if [[ -n "${peer1_asn}" ]]; then
    neb_asn="${peer1_asn}"
  fi

  if [[ -z "${gcp_ip0}" ]]; then gcp_ip0="<missing>"; fi
  if [[ -z "${gcp_ip1}" ]]; then gcp_ip1="<missing>"; fi
  if [[ -z "${ext_ip}" ]]; then ext_ip="<missing>"; fi

  cat <<EOF

Status (no changes made):
  GCP project:         ${GCP_PROJECT_ID}
  GCP account:         ${ACTIVE_ACCOUNT:-<unknown>}
  Region:              ${REGION}
  Network:             ${NETWORK}
  HA VPN gateway:      ${VPN_GATEWAY_NAME} (${gw_exists})
  Cloud Router:        ${CLOUD_ROUTER_NAME} (${router_exists})
  External VPN GW:     ${EXTERNAL_GW_NAME} (${ext_exists})
  Tunnel 1:            ${TUNNEL1_NAME} (${tun1_status})
  Tunnel 2:            ${TUNNEL2_NAME} (${tun2_status})

Nebius-side configuration values:
  Remote ASN (GCP Cloud Router): ${router_asn}
  Local ASN (Nebius):           ${neb_asn}
  Number of tunnels:            2
  BGP timers (optional):        hold_time_seconds=6, keepalive_seconds=2

Tunnel 1:
  remote_public_ip: ${gcp_ip0}
  psk:              ${psk1_display}
  inner_cidr:       ${tun1_cidr}
  inner_local_ip:   ${tun1_nebius_ip}
  inner_remote_ip:  ${tun1_gcp_ip}

Tunnel 2:
  remote_public_ip: ${gcp_ip1}
  psk:              ${psk2_display}
  inner_cidr:       ${tun2_cidr}
  inner_local_ip:   ${tun2_nebius_ip}
  inner_remote_ip:  ${tun2_gcp_ip}

Nebius gateway public IP: ${ext_ip}
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        show_usage
        exit 0
        ;;
      --status)
        STATUS_ONLY=1
        shift
        ;;
      --rotate-existing-tunnels)
        ROTATE_EXISTING_TUNNELS=1
        shift
        ;;
      -c|--local-config-file)
        if [[ $# -lt 2 ]]; then
          log_error "Missing value for $1"
          exit 1
        fi
        LOCAL_CONFIG_FILE="$2"
        shift 2
        ;;
      --gcp-project-id)
        if [[ $# -lt 2 ]]; then
          log_error "Missing value for $1"
          exit 1
        fi
        GCP_PROJECT_ID="$2"
        shift 2
        ;;
      --region)
        if [[ $# -lt 2 ]]; then
          log_error "Missing value for $1"
          exit 1
        fi
        REGION="$2"
        shift 2
        ;;
      --nebius-public-ip)
        if [[ $# -lt 2 ]]; then
          log_error "Missing value for $1"
          exit 1
        fi
        NEBIUS_PUBLIC_IP="$2"
        shift 2
        ;;
      --nebius-asn)
        if [[ $# -lt 2 ]]; then
          log_error "Missing value for $1"
          exit 1
        fi
        NEBIUS_ASN="$2"
        shift 2
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
        POSITIONAL_ARGS+=("$1")
        shift
        ;;
    esac
  done

  if [[ $# -gt 0 ]]; then
    log_error "Unexpected trailing arguments: $*"
    show_usage >&2
    exit 1
  fi

  if [[ "${#POSITIONAL_ARGS[@]}" -ne 0 && "${#POSITIONAL_ARGS[@]}" -ne 4 ]]; then
    log_error "Expected either no positional arguments or exactly four: <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>"
    show_usage >&2
    exit 1
  fi

  if [[ "${#POSITIONAL_ARGS[@]}" -ge 1 ]]; then
    GCP_PROJECT_ID="${POSITIONAL_ARGS[0]}"
  fi
  if [[ "${#POSITIONAL_ARGS[@]}" -ge 2 ]]; then
    REGION="${POSITIONAL_ARGS[1]}"
  fi
  if [[ "${#POSITIONAL_ARGS[@]}" -ge 3 ]]; then
    NEBIUS_PUBLIC_IP="${POSITIONAL_ARGS[2]}"
  fi
  if [[ "${#POSITIONAL_ARGS[@]}" -ge 4 ]]; then
    NEBIUS_ASN="${POSITIONAL_ARGS[3]}"
  fi

  if [[ -n "${NEBIUS_PUBLIC_IP}" ]] && ! validate_ipv4 "${NEBIUS_PUBLIC_IP}"; then
    log_error "Invalid Nebius public IP: ${NEBIUS_PUBLIC_IP}"
    exit 1
  fi
  if [[ -n "${NEBIUS_ASN}" ]] && ! validate_asn "${NEBIUS_ASN}"; then
    log_error "Invalid Nebius ASN: ${NEBIUS_ASN}"
    exit 1
  fi
}

main() {
  init_output_style
  parse_args "$@"

  ensure_gcloud_installed
  ensure_auth
  resolve_gcp_project_id
  resolve_region

  if [[ -n "${LOCAL_CONFIG_FILE}" ]]; then
    resolve_local_config_file
  fi

  if [[ "${STATUS_ONLY}" -eq 0 ]]; then
    if [[ -z "${LOCAL_CONFIG_FILE}" && ( -z "${NEBIUS_PUBLIC_IP}" || -z "${NEBIUS_ASN}" ) ]]; then
      resolve_local_config_file
    fi
    resolve_nebius_asn
    resolve_nebius_public_ip
  elif [[ -z "${NEBIUS_PUBLIC_IP}" ]] && [[ -n "${LOCAL_CONFIG_FILE}" ]]; then
    NEBIUS_PUBLIC_IP="$(config_value "${LOCAL_CONFIG_FILE}" "first_external_ip" 2>/dev/null || true)"
  fi

  if [[ -n "${NEBIUS_ASN}" ]]; then
    log_note "Using gcp_project=${GCP_PROJECT_ID} region=${REGION} network=${NETWORK} nebius_asn=${NEBIUS_ASN}"
  else
    log_note "Using gcp_project=${GCP_PROJECT_ID} region=${REGION} network=${NETWORK}"
  fi

  initialize_connection_names

  if [[ "${STATUS_ONLY}" -eq 1 ]]; then
    status_report
    exit 0
  fi

  choose_vpn_gateway_name
  choose_cloud_router_name
  choose_connection_resource_names
  confirm_create

  if resource_exists compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    log_success "HA VPN gateway exists: ${VPN_GATEWAY_NAME}"
  else
    log_note "Creating HA VPN gateway ${VPN_GATEWAY_NAME}"
    gcloud compute vpn-gateways create "${VPN_GATEWAY_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --network "${NETWORK}"
  fi

  if resource_exists compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    existing_asn="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgp.asn)' 2>/dev/null || true)"
    existing_nats="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(nats[].name)' 2>/dev/null || true)"
    if [[ -n "${existing_nats}" ]]; then
      log_warn "Cloud Router ${CLOUD_ROUTER_NAME} is also used for Cloud NAT (${existing_nats})."
      log_warn "This can work in practice, but Google documents it as unsupported for HA VPN setup."
    fi
    if [[ -n "${existing_asn}" && "${existing_asn}" != "${CLOUD_ROUTER_ASN}" ]]; then
      log_warn "Existing GCP Cloud Router ASN is ${existing_asn}; the selected router will keep using that ASN."
    fi
    log_success "Cloud Router exists: ${CLOUD_ROUTER_NAME}"
  else
    log_note "Creating Cloud Router ${CLOUD_ROUTER_NAME} with GCP ASN ${CLOUD_ROUTER_ASN}"
    gcloud compute routers create "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --network "${NETWORK}" \
      --asn "${CLOUD_ROUTER_ASN}"
  fi

  if resource_exists compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}"; then
    existing_ip="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='value(interfaces[0].ipAddress)' 2>/dev/null || true)"
    existing_redundancy_type="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='value(redundancyType)' 2>/dev/null || true)"
    existing_interface_ids="$(gcloud compute external-vpn-gateways describe "${EXTERNAL_GW_NAME}" --project "${GCP_PROJECT_ID}" --format='csv[no-heading](interfaces[].id)' 2>/dev/null || true)"
    existing_interface_count=0
    if [[ -n "${existing_interface_ids}" ]]; then
      existing_interface_count="$(awk -F',' '{print NF}' <<<"${existing_interface_ids}")"
    fi
    if [[ "${existing_ip}" != "${NEBIUS_PUBLIC_IP}" ]]; then
      log_error "External VPN gateway ${EXTERNAL_GW_NAME} already exists with IP ${existing_ip}, expected ${NEBIUS_PUBLIC_IP}."
      log_note "Delete it first or use a different EXTERNAL_GW_NAME."
      exit 1
    fi
    if [[ "${existing_redundancy_type}" != "SINGLE_IP_INTERNALLY_REDUNDANT" || "${existing_interface_count}" -ne 1 ]]; then
      log_error "External VPN gateway ${EXTERNAL_GW_NAME} is not a single-IP peer gateway (redundancyType=${existing_redundancy_type:-<unset>}, interfaces=${existing_interface_count})."
      log_note "This script expects the Nebius peer to be modeled as a single-interface external VPN gateway using interface 0."
      exit 1
    fi
    log_success "External VPN gateway exists: ${EXTERNAL_GW_NAME} (${NEBIUS_PUBLIC_IP})"
  else
    log_note "Creating external VPN gateway ${EXTERNAL_GW_NAME}"
    gcloud compute external-vpn-gateways create "${EXTERNAL_GW_NAME}" \
      --project "${GCP_PROJECT_ID}" \
      --interfaces=0="${NEBIUS_PUBLIC_IP}"
  fi

  tunnel1_exists="false"
  tunnel2_exists="false"
  if resource_exists compute vpn-tunnels describe "${TUNNEL1_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tunnel1_exists="true"
  fi
  if resource_exists compute vpn-tunnels describe "${TUNNEL2_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}"; then
    tunnel2_exists="true"
  fi

  if [[ "${tunnel1_exists}" == "true" && -n "${PSK1}" && "${ROTATE_EXISTING_TUNNELS}" -eq 0 ]]; then
    log_warn "Ignoring PSK1 because ${TUNNEL1_NAME} already exists. Use --rotate-existing-tunnels to recreate it."
    PSK1="existing: no display"
  fi
  if [[ "${tunnel2_exists}" == "true" && -n "${PSK2}" && "${ROTATE_EXISTING_TUNNELS}" -eq 0 ]]; then
    log_warn "Ignoring PSK2 because ${TUNNEL2_NAME} already exists. Use --rotate-existing-tunnels to recreate it."
    PSK2="existing: no display"
  fi

  if [[ -z "${PSK1}" ]]; then
    if [[ "${tunnel1_exists}" == "true" && "${ROTATE_EXISTING_TUNNELS}" -eq 0 ]]; then
      PSK1="existing: no display"
    else
      PSK1="$(gen_psk)"
    fi
  fi
  if [[ -z "${PSK2}" ]]; then
    if [[ "${tunnel2_exists}" == "true" && "${ROTATE_EXISTING_TUNNELS}" -eq 0 ]]; then
      PSK2="existing: no display"
    else
      PSK2="$(gen_psk)"
    fi
  fi

  if [[ "${tunnel1_exists}" == "true" && "${ROTATE_EXISTING_TUNNELS}" -eq 1 ]]; then
    log_warn "Recreating tunnel ${TUNNEL1_NAME}"
    remove_bgp_peer_if_present "${PEER1_NAME}"
    remove_router_interface_if_present "${IFACE1_NAME}"
    gcloud compute vpn-tunnels delete "${TUNNEL1_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --quiet
    tunnel1_exists="false"
  fi

  if [[ "${tunnel1_exists}" == "true" ]]; then
    log_success "Tunnel exists: ${TUNNEL1_NAME}"
  else
    log_note "Creating tunnel ${TUNNEL1_NAME}"
    gcloud compute vpn-tunnels create "${TUNNEL1_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --vpn-gateway "${VPN_GATEWAY_NAME}" \
      --interface 0 \
      --peer-external-gateway "${EXTERNAL_GW_NAME}" \
      --peer-external-gateway-interface 0 \
      --router "${CLOUD_ROUTER_NAME}" \
      --ike-version 2 \
      --shared-secret "${PSK1}"
  fi

  if [[ "${tunnel2_exists}" == "true" && "${ROTATE_EXISTING_TUNNELS}" -eq 1 ]]; then
    log_warn "Recreating tunnel ${TUNNEL2_NAME}"
    remove_bgp_peer_if_present "${PEER2_NAME}"
    remove_router_interface_if_present "${IFACE2_NAME}"
    gcloud compute vpn-tunnels delete "${TUNNEL2_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --quiet
    tunnel2_exists="false"
  fi

  if [[ "${tunnel2_exists}" == "true" ]]; then
    log_success "Tunnel exists: ${TUNNEL2_NAME}"
  else
    log_note "Creating tunnel ${TUNNEL2_NAME}"
    gcloud compute vpn-tunnels create "${TUNNEL2_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --vpn-gateway "${VPN_GATEWAY_NAME}" \
      --interface 1 \
      --peer-external-gateway "${EXTERNAL_GW_NAME}" \
      --peer-external-gateway-interface 0 \
      --router "${CLOUD_ROUTER_NAME}" \
      --ike-version 2 \
      --shared-secret "${PSK2}"
  fi

  router_ifaces="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(interfaces.name,interfaces.ipRange)' 2>/dev/null || true)"
  iface1_range="$(awk -v n="${IFACE1_NAME}" '$1==n {print $2; exit}' <<<"${router_ifaces}")"
  iface2_range="$(awk -v n="${IFACE2_NAME}" '$1==n {print $2; exit}' <<<"${router_ifaces}")"

  if [[ -n "${iface1_range}" ]]; then
    if [[ "${iface1_range}" != "${TUN1_GCP_IP}/30" ]]; then
      log_error "Router interface ${IFACE1_NAME} exists with ${iface1_range}, expected ${TUN1_GCP_IP}/30."
      exit 1
    fi
    log_success "Router interface exists: ${IFACE1_NAME} (${iface1_range})"
  else
    log_note "Creating router interface ${IFACE1_NAME}"
    gcloud compute routers add-interface "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --interface-name "${IFACE1_NAME}" \
      --ip-address "${TUN1_GCP_IP}" \
      --mask-length 30 \
      --vpn-tunnel "${TUNNEL1_NAME}" \
      --vpn-tunnel-region "${REGION}"
  fi

  if [[ -n "${iface2_range}" ]]; then
    if [[ "${iface2_range}" != "${TUN2_GCP_IP}/30" ]]; then
      log_error "Router interface ${IFACE2_NAME} exists with ${iface2_range}, expected ${TUN2_GCP_IP}/30."
      exit 1
    fi
    log_success "Router interface exists: ${IFACE2_NAME} (${iface2_range})"
  else
    log_note "Creating router interface ${IFACE2_NAME}"
    gcloud compute routers add-interface "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --interface-name "${IFACE2_NAME}" \
      --ip-address "${TUN2_GCP_IP}" \
      --mask-length 30 \
      --vpn-tunnel "${TUNNEL2_NAME}" \
      --vpn-tunnel-region "${REGION}"
  fi

  router_peers="$(gcloud compute routers describe "${CLOUD_ROUTER_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(bgpPeers.name,bgpPeers.peerIpAddress,bgpPeers.peerAsn,bgpPeers.advertisedRoutePriority)' 2>/dev/null || true)"
  peer1_state="$(awk -v n="${PEER1_NAME}" '$1==n {print $2 "|" $3 "|" $4; exit}' <<<"${router_peers}")"
  peer2_state="$(awk -v n="${PEER2_NAME}" '$1==n {print $2 "|" $3 "|" $4; exit}' <<<"${router_peers}")"

  if [[ -n "${peer1_state}" ]]; then
    if [[ "${peer1_state}" != "${TUN1_NEBIUS_IP}|${NEBIUS_ASN}|${ACTIVE_ADVERTISED_ROUTE_PRIORITY}" ]]; then
      log_error "BGP peer ${PEER1_NAME} exists with state ${peer1_state}, expected ${TUN1_NEBIUS_IP}|${NEBIUS_ASN}|${ACTIVE_ADVERTISED_ROUTE_PRIORITY}."
      exit 1
    fi
    log_success "BGP peer exists: ${PEER1_NAME} (${peer1_state})"
  else
    log_note "Creating BGP peer ${PEER1_NAME}"
    gcloud compute routers add-bgp-peer "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --peer-name "${PEER1_NAME}" \
      --interface "${IFACE1_NAME}" \
      --peer-ip-address "${TUN1_NEBIUS_IP}" \
      --peer-asn "${NEBIUS_ASN}" \
      --advertised-route-priority "${ACTIVE_ADVERTISED_ROUTE_PRIORITY}"
  fi

  if [[ -n "${peer2_state}" ]]; then
    if [[ "${peer2_state}" != "${TUN2_NEBIUS_IP}|${NEBIUS_ASN}|${PASSIVE_ADVERTISED_ROUTE_PRIORITY}" ]]; then
      log_error "BGP peer ${PEER2_NAME} exists with state ${peer2_state}, expected ${TUN2_NEBIUS_IP}|${NEBIUS_ASN}|${PASSIVE_ADVERTISED_ROUTE_PRIORITY}."
      exit 1
    fi
    log_success "BGP peer exists: ${PEER2_NAME} (${peer2_state})"
  else
    log_note "Creating BGP peer ${PEER2_NAME}"
    gcloud compute routers add-bgp-peer "${CLOUD_ROUTER_NAME}" \
      --region "${REGION}" \
      --project "${GCP_PROJECT_ID}" \
      --peer-name "${PEER2_NAME}" \
      --interface "${IFACE2_NAME}" \
      --peer-ip-address "${TUN2_NEBIUS_IP}" \
      --peer-asn "${NEBIUS_ASN}" \
      --advertised-route-priority "${PASSIVE_ADVERTISED_ROUTE_PRIORITY}"
  fi

  gcp_ip0="$(gcloud compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(vpnInterfaces[0].ipAddress)')"
  gcp_ip1="$(gcloud compute vpn-gateways describe "${VPN_GATEWAY_NAME}" --region "${REGION}" --project "${GCP_PROJECT_ID}" --format='value(vpnInterfaces[1].ipAddress)')"

  if [[ "${PSK1}" == "existing: no display" || "${PSK2}" == "existing: no display" ]]; then
    psk_notice=$'Existing tunnel PSKs cannot be read back from GCP.\nRotate existing tunnels if you need newly generated PSKs printed here.'
    psk_block=$'PSKs:\n  Tunnel 1: existing value retained (not displayed)\n  Tunnel 2: existing value retained (not displayed)'
  else
    psk_notice="Export these PSKs before running validate/apply:"
    printf -v psk_block "  export GCP_TUNNEL_1_PSK='%s'\n  export GCP_TUNNEL_2_PSK='%s'" "${PSK1}" "${PSK2}"
  fi

  cat <<EOF

GCP side setup complete.

  GCP project:         ${GCP_PROJECT_ID}
  GCP account:         ${ACTIVE_ACCOUNT}
  Nebius public IP:    ${NEBIUS_PUBLIC_IP}
  GCP Cloud Router ASN: ${CLOUD_ROUTER_ASN}
  Nebius peer ASN:     ${NEBIUS_ASN}
  Active route prio:   ${ACTIVE_ADVERTISED_ROUTE_PRIORITY}
  Passive route prio:  ${PASSIVE_ADVERTISED_ROUTE_PRIORITY}

${psk_notice}
${psk_block}

YAML snippet:
  bgp:
    enabled: true
    remote_asn: ${CLOUD_ROUTER_ASN}
    advertise_local_prefixes: true
  tunnels:
    - name: "${TUNNEL1_NAME}"
      gateway_instance_index: 0
      local_public_ip_index: 0
      ha_role: "active"
      remote_public_ip: "${gcp_ip0}"
      psk: "\${GCP_TUNNEL_1_PSK}"
      inner_cidr: "${TUN1_CIDR}"
      inner_local_ip: "${TUN1_NEBIUS_IP}"
      inner_remote_ip: "${TUN1_GCP_IP}"
    - name: "${TUNNEL2_NAME}"
      gateway_instance_index: 0
      local_public_ip_index: 0
      ha_role: "passive"
      remote_public_ip: "${gcp_ip1}"
      psk: "\${GCP_TUNNEL_2_PSK}"
      inner_cidr: "${TUN2_CIDR}"
      inner_local_ip: "${TUN2_NEBIUS_IP}"
      inner_remote_ip: "${TUN2_GCP_IP}"
EOF
}

main "$@"
