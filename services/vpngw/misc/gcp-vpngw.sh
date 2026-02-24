#!/usr/bin/env bash
# Run this script with caution, we do not accept any responsibility for service disruption or downtime resulting from its execution.
# Creates/uses the HA VPN gateway and Cloud Router, creates the external VPN gateway
# + two tunnels + two BGP peers, and then prints the Nebius-side values (ASNs,
# GCP public IPs, PSKs, APIPA /30s).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./gcp-vpngw.sh [--status] <nebius-public-ip>

--status: report existing resources only; public IP is optional.

Common environment overrides (defaults shown):
  PROJECT_ID=productteam-nebius
  REGION=europe-west9
  NETWORK=default
  VPN_GATEWAY_NAME=ha-gw-nebius
  CLOUD_ROUTER_NAME=cr-nebius-ha
  CLOUD_ROUTER_ASN=65014
  NEBIUS_ASN=65011
  EXTERNAL_GW_NAME=nebius-bgp-vpngw2

PSK env vars (required if tunnels need to be created):
  PSK1=<pre-shared-key-1>
  PSK2=<pre-shared-key-2>

APIPA /30 overrides (optional):
  TUN1_CIDR=169.254.30.0/30
  TUN2_CIDR=169.254.30.4/30

Note: Additional resource-name overrides are defined near the top of the script.
EOF
}

STATUS_ONLY=false
positional=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --status)
      STATUS_ONLY=true
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        positional+=("$1")
        shift
      done
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

if [[ "$STATUS_ONLY" == "true" ]]; then
  if [[ ${#positional[@]} -gt 1 ]]; then
    usage
    exit 1
  fi
  NEBIUS_PUBLIC_IP="${positional[0]:-}"
else
  if [[ ${#positional[@]} -ne 1 ]]; then
    usage
    exit 1
  fi
  NEBIUS_PUBLIC_IP="${positional[0]}"
fi

if [[ -n "$NEBIUS_PUBLIC_IP" && ! "$NEBIUS_PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: Invalid public IP: $NEBIUS_PUBLIC_IP" >&2
  exit 1
fi

command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud CLI is not installed or not on PATH." >&2
  exit 1
}

PROJECT_ID="${PROJECT_ID:-productteam-nebius}"
REGION="${REGION:-europe-west9}"
NETWORK="${NETWORK:-default}"
VPN_GATEWAY_NAME="${VPN_GATEWAY_NAME:-ha-gw-nebius}"
CLOUD_ROUTER_NAME="${CLOUD_ROUTER_NAME:-cr-nebius-ha}"
CLOUD_ROUTER_ASN="${CLOUD_ROUTER_ASN:-65014}"
NEBIUS_ASN="${NEBIUS_ASN:-65011}"
EXTERNAL_GW_NAME="${EXTERNAL_GW_NAME:-nebius-bgp-vpngw2}"

TUNNEL1_NAME="${TUNNEL1_NAME:-tunnel-if2}"
TUNNEL2_NAME="${TUNNEL2_NAME:-tunnel-if3}"
IFACE1_NAME="${IFACE1_NAME:-if-bgp-session-tunnel-2}"
IFACE2_NAME="${IFACE2_NAME:-if-bgp-session-tunnel-3}"
PEER1_NAME="${PEER1_NAME:-bgp-session-tunnel-2}"
PEER2_NAME="${PEER2_NAME:-bgp-session-tunnel-3}"

TUN1_CIDR="${TUN1_CIDR:-169.254.30.0/30}"
TUN1_GCP_IP="${TUN1_GCP_IP:-169.254.30.1}"
TUN1_NEBIUS_IP="${TUN1_NEBIUS_IP:-169.254.30.2}"
TUN2_CIDR="${TUN2_CIDR:-169.254.30.4/30}"
TUN2_GCP_IP="${TUN2_GCP_IP:-169.254.30.5}"
TUN2_NEBIUS_IP="${TUN2_NEBIUS_IP:-169.254.30.6}"

PSK1="${PSK1:-}"
PSK2="${PSK2:-}"

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

resource_exists() {
  gcloud "$@" >/dev/null 2>&1
}

ensure_auth() {
  local active
  active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1 || true)"
  if [[ -z "$active" ]]; then
    echo "No active gcloud auth account; running: gcloud auth login" >&2
    gcloud auth login
  else
    echo "✓ gcloud authenticated as $active"
  fi
}

confirm_update() {
  local prompt="$1"
  local reply
  read -r -p "$prompt [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) return 0 ;;
    *) return 1 ;;
  esac
}

status_report() {
  local gw_exists=false
  local router_exists=false
  local ext_exists=false
  if resource_exists compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID"; then
    gw_exists=true
  fi
  if resource_exists compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID"; then
    router_exists=true
  fi
  if resource_exists compute external-vpn-gateways describe "$EXTERNAL_GW_NAME" --project "$PROJECT_ID"; then
    ext_exists=true
  fi

  local gcp_ip0 gcp_ip1 router_asn ext_ip
  if [[ "$gw_exists" == "true" ]]; then
    gcp_ip0="$(gcloud compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(vpnInterfaces[0].ipAddress)' 2>/dev/null || true)"
    gcp_ip1="$(gcloud compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(vpnInterfaces[1].ipAddress)' 2>/dev/null || true)"
  else
    gcp_ip0=""
    gcp_ip1=""
  fi

  router_asn="$CLOUD_ROUTER_ASN"
  if [[ "$router_exists" == "true" ]]; then
    router_asn="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgp.asn)' 2>/dev/null || true)"
    if [[ -z "$router_asn" ]]; then
      router_asn="$CLOUD_ROUTER_ASN"
    fi
  fi

  ext_ip=""
  if [[ "$ext_exists" == "true" ]]; then
    ext_ip="$(gcloud compute external-vpn-gateways describe "$EXTERNAL_GW_NAME" --project "$PROJECT_ID" --format='value(interfaces[0].ipAddress)' 2>/dev/null || true)"
  fi
  if [[ -n "$NEBIUS_PUBLIC_IP" ]]; then
    ext_ip="$NEBIUS_PUBLIC_IP"
  fi

  local tun1_status tun2_status
  if resource_exists compute vpn-tunnels describe "$TUNNEL1_NAME" --region "$REGION" --project "$PROJECT_ID"; then
    tun1_status="$(gcloud compute vpn-tunnels describe "$TUNNEL1_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status)' 2>/dev/null || true)"
  else
    tun1_status="MISSING"
  fi
  if resource_exists compute vpn-tunnels describe "$TUNNEL2_NAME" --region "$REGION" --project "$PROJECT_ID"; then
    tun2_status="$(gcloud compute vpn-tunnels describe "$TUNNEL2_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status)' 2>/dev/null || true)"
  else
    tun2_status="MISSING"
  fi

  local iface1_range iface2_range peer1_ip peer2_ip peer1_asn
  iface1_range=""
  iface2_range=""
  peer1_ip=""
  peer2_ip=""
  peer1_asn=""
  if [[ "$router_exists" == "true" ]]; then
    iface1_range="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(interfaces.name,interfaces.ipRange)' 2>/dev/null | awk -v n="$IFACE1_NAME" '$1==n {print $2; exit}')"
    iface2_range="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(interfaces.name,interfaces.ipRange)' 2>/dev/null | awk -v n="$IFACE2_NAME" '$1==n {print $2; exit}')"
    peer1_ip="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgpPeers.name,bgpPeers.peerIpAddress)' 2>/dev/null | awk -v n="$PEER1_NAME" '$1==n {print $2; exit}')"
    peer2_ip="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgpPeers.name,bgpPeers.peerIpAddress)' 2>/dev/null | awk -v n="$PEER2_NAME" '$1==n {print $2; exit}')"
    peer1_asn="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgpPeers.name,bgpPeers.peerAsn)' 2>/dev/null | awk -v n="$PEER1_NAME" '$1==n {print $2; exit}')"
  fi

  local tun1_cidr tun2_cidr tun1_gcp_ip tun2_gcp_ip tun1_nebius_ip tun2_nebius_ip
  tun1_cidr="${iface1_range:-$TUN1_CIDR}"
  tun2_cidr="${iface2_range:-$TUN2_CIDR}"
  tun1_gcp_ip="${iface1_range%/*}"
  tun2_gcp_ip="${iface2_range%/*}"
  if [[ -z "$iface1_range" ]]; then tun1_gcp_ip="$TUN1_GCP_IP"; fi
  if [[ -z "$iface2_range" ]]; then tun2_gcp_ip="$TUN2_GCP_IP"; fi
  tun1_nebius_ip="${peer1_ip:-$TUN1_NEBIUS_IP}"
  tun2_nebius_ip="${peer2_ip:-$TUN2_NEBIUS_IP}"

  local psk1_display psk2_display neb_asn
  psk1_display="${PSK1:-existing: no display}"
  psk2_display="${PSK2:-existing: no display}"
  neb_asn="${NEBIUS_ASN}"
  if [[ -n "$peer1_asn" ]]; then
    neb_asn="$peer1_asn"
  fi

  if [[ -z "$gcp_ip0" ]]; then gcp_ip0="<missing>"; fi
  if [[ -z "$gcp_ip1" ]]; then gcp_ip1="<missing>"; fi
  if [[ -z "$ext_ip" ]]; then ext_ip="<missing>"; fi

  cat <<EOF

Status (no changes made):
  HA VPN gateway:      $VPN_GATEWAY_NAME (${gw_exists})
  Cloud Router:        $CLOUD_ROUTER_NAME (${router_exists})
  External VPN GW:     $EXTERNAL_GW_NAME (${ext_exists})
  Tunnel 1:            $TUNNEL1_NAME ($tun1_status)
  Tunnel 2:            $TUNNEL2_NAME ($tun2_status)

Nebius-side configuration values:
  Remote ASN (GCP Cloud Router): $router_asn
  Local ASN (Nebius):           $neb_asn
  Number of tunnels:           2
  BGP timers (optional):       hold_time_seconds=6, keepalive_seconds=2

Tunnel 1:
  remote_public_ip: $gcp_ip0
  psk:              $psk1_display
  inner_cidr:       $tun1_cidr
  inner_local_ip:   $tun1_nebius_ip
  inner_remote_ip:  $tun1_gcp_ip

Tunnel 2:
  remote_public_ip: $gcp_ip1
  psk:              $psk2_display
  inner_cidr:       $tun2_cidr
  inner_local_ip:   $tun2_nebius_ip
  inner_remote_ip:  $tun2_gcp_ip

Nebius gateway public IP (use in external_ips): $ext_ip
EOF
}

echo "Using project=$PROJECT_ID region=$REGION"
ensure_auth

if [[ "$STATUS_ONLY" == "true" ]]; then
  status_report
  exit 0
fi

if resource_exists compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID"; then
  echo "✓ HA VPN gateway exists: $VPN_GATEWAY_NAME"
else
  echo "Creating HA VPN gateway: $VPN_GATEWAY_NAME"
  gcloud compute vpn-gateways create "$VPN_GATEWAY_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --network "$NETWORK"
fi

if resource_exists compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID"; then
  existing_asn="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgp.asn)')"
  if [[ -n "$existing_asn" && "$existing_asn" != "$CLOUD_ROUTER_ASN" ]]; then
    echo "WARNING: Cloud Router ASN is $existing_asn (expected $CLOUD_ROUTER_ASN)." >&2
  fi
  echo "✓ Cloud Router exists: $CLOUD_ROUTER_NAME"
else
  echo "Creating Cloud Router: $CLOUD_ROUTER_NAME"
  gcloud compute routers create "$CLOUD_ROUTER_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --network "$NETWORK" \
    --asn "$CLOUD_ROUTER_ASN"
fi

if resource_exists compute external-vpn-gateways describe "$EXTERNAL_GW_NAME" --project "$PROJECT_ID"; then
  existing_ip="$(gcloud compute external-vpn-gateways describe "$EXTERNAL_GW_NAME" --project "$PROJECT_ID" --format='value(interfaces[0].ipAddress)')"
  if [[ "$existing_ip" != "$NEBIUS_PUBLIC_IP" ]]; then
    echo "ERROR: External VPN gateway $EXTERNAL_GW_NAME exists with IP $existing_ip, expected $NEBIUS_PUBLIC_IP." >&2
    echo "Either delete it or set EXTERNAL_GW_NAME to a new resource name." >&2
    exit 1
  fi
  echo "✓ External VPN gateway exists: $EXTERNAL_GW_NAME ($NEBIUS_PUBLIC_IP)"
else
  echo "Creating external VPN gateway: $EXTERNAL_GW_NAME"
  gcloud compute external-vpn-gateways create "$EXTERNAL_GW_NAME" \
    --project "$PROJECT_ID" \
    --interfaces=0="$NEBIUS_PUBLIC_IP"
fi

tunnel1_exists=false
tunnel2_exists=false
if resource_exists compute vpn-tunnels describe "$TUNNEL1_NAME" --region "$REGION" --project "$PROJECT_ID"; then
  tunnel1_exists=true
fi
if resource_exists compute vpn-tunnels describe "$TUNNEL2_NAME" --region "$REGION" --project "$PROJECT_ID"; then
  tunnel2_exists=true
fi

rotate_tunnel1=false
rotate_tunnel2=false
if [[ "$tunnel1_exists" == "true" && -n "$PSK1" ]]; then
  echo "Tunnel $TUNNEL1_NAME exists. GCP does not expose the current PSK, so it cannot be compared."
  if confirm_update "Recreate $TUNNEL1_NAME to apply PSK1?"; then
    rotate_tunnel1=true
  else
    PSK1="existing: no display"
  fi
fi
if [[ "$tunnel2_exists" == "true" && -n "$PSK2" ]]; then
  echo "Tunnel $TUNNEL2_NAME exists. GCP does not expose the current PSK, so it cannot be compared."
  if confirm_update "Recreate $TUNNEL2_NAME to apply PSK2?"; then
    rotate_tunnel2=true
  else
    PSK2="existing: no display"
  fi
fi

if [[ -z "$PSK1" ]]; then
  if [[ "$tunnel1_exists" == "true" ]]; then
    PSK1="existing: no display"
  else
    PSK1="$(gen_psk)"
  fi
fi
if [[ -z "$PSK2" ]]; then
  if [[ "$tunnel2_exists" == "true" ]]; then
    PSK2="existing: no display"
  else
    PSK2="$(gen_psk)"
  fi
fi

if [[ "$tunnel1_exists" == "true" && "$rotate_tunnel1" == "true" ]]; then
  echo "Recreating tunnel: $TUNNEL1_NAME"
  gcloud compute vpn-tunnels delete "$TUNNEL1_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --quiet
  tunnel1_exists=false
fi

if [[ "$tunnel1_exists" == "true" ]]; then
  echo "✓ Tunnel exists: $TUNNEL1_NAME"
else
  echo "Creating tunnel: $TUNNEL1_NAME"
  gcloud compute vpn-tunnels create "$TUNNEL1_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --vpn-gateway "$VPN_GATEWAY_NAME" \
    --interface 0 \
    --peer-external-gateway "$EXTERNAL_GW_NAME" \
    --peer-external-gateway-interface 0 \
    --router "$CLOUD_ROUTER_NAME" \
    --ike-version 2 \
    --shared-secret "$PSK1"
fi

if [[ "$tunnel2_exists" == "true" && "$rotate_tunnel2" == "true" ]]; then
  echo "Recreating tunnel: $TUNNEL2_NAME"
  gcloud compute vpn-tunnels delete "$TUNNEL2_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --quiet
  tunnel2_exists=false
fi

if [[ "$tunnel2_exists" == "true" ]]; then
  echo "✓ Tunnel exists: $TUNNEL2_NAME"
else
  echo "Creating tunnel: $TUNNEL2_NAME"
  gcloud compute vpn-tunnels create "$TUNNEL2_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --vpn-gateway "$VPN_GATEWAY_NAME" \
    --interface 1 \
    --peer-external-gateway "$EXTERNAL_GW_NAME" \
    --peer-external-gateway-interface 0 \
    --router "$CLOUD_ROUTER_NAME" \
    --ike-version 2 \
    --shared-secret "$PSK2"
fi

router_ifaces="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(interfaces.name)')"
if echo "$router_ifaces" | grep -Fxq "$IFACE1_NAME"; then
  echo "✓ Router interface exists: $IFACE1_NAME"
else
  echo "Creating router interface: $IFACE1_NAME"
  gcloud compute routers add-interface "$CLOUD_ROUTER_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --interface-name "$IFACE1_NAME" \
    --ip-address "$TUN1_GCP_IP" \
    --mask-length 30 \
    --vpn-tunnel "$TUNNEL1_NAME" \
    --vpn-tunnel-region "$REGION"
fi

if echo "$router_ifaces" | grep -Fxq "$IFACE2_NAME"; then
  echo "✓ Router interface exists: $IFACE2_NAME"
else
  echo "Creating router interface: $IFACE2_NAME"
  gcloud compute routers add-interface "$CLOUD_ROUTER_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --interface-name "$IFACE2_NAME" \
    --ip-address "$TUN2_GCP_IP" \
    --mask-length 30 \
    --vpn-tunnel "$TUNNEL2_NAME" \
    --vpn-tunnel-region "$REGION"
fi

router_peers="$(gcloud compute routers describe "$CLOUD_ROUTER_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(bgpPeers.name)')"
if echo "$router_peers" | grep -Fxq "$PEER1_NAME"; then
  echo "✓ BGP peer exists: $PEER1_NAME"
else
  echo "Creating BGP peer: $PEER1_NAME"
  gcloud compute routers add-bgp-peer "$CLOUD_ROUTER_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --peer-name "$PEER1_NAME" \
    --interface "$IFACE1_NAME" \
    --peer-ip-address "$TUN1_NEBIUS_IP" \
    --peer-asn "$NEBIUS_ASN"
fi

if echo "$router_peers" | grep -Fxq "$PEER2_NAME"; then
  echo "✓ BGP peer exists: $PEER2_NAME"
else
  echo "Creating BGP peer: $PEER2_NAME"
  gcloud compute routers add-bgp-peer "$CLOUD_ROUTER_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --peer-name "$PEER2_NAME" \
    --interface "$IFACE2_NAME" \
    --peer-ip-address "$TUN2_NEBIUS_IP" \
    --peer-asn "$NEBIUS_ASN"
fi

GCP_IP0="$(gcloud compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(vpnInterfaces[0].ipAddress)')"
GCP_IP1="$(gcloud compute vpn-gateways describe "$VPN_GATEWAY_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(vpnInterfaces[1].ipAddress)')"

cat <<EOF

GCP side setup complete.

Nebius-side configuration values:
  Remote ASN (GCP Cloud Router): $CLOUD_ROUTER_ASN
  Local ASN (Nebius):           $NEBIUS_ASN
  Number of tunnels:           2
  BGP timers (optional):       hold_time_seconds=6, keepalive_seconds=2

Tunnel 1:
  remote_public_ip: $GCP_IP0
  psk:              $PSK1
  inner_cidr:       $TUN1_CIDR
  inner_local_ip:   $TUN1_NEBIUS_IP
  inner_remote_ip:  $TUN1_GCP_IP

Tunnel 2:
  remote_public_ip: $GCP_IP1
  psk:              $PSK2
  inner_cidr:       $TUN2_CIDR
  inner_local_ip:   $TUN2_NEBIUS_IP
  inner_remote_ip:  $TUN2_GCP_IP

Nebius gateway public IP (use in external_ips): $NEBIUS_PUBLIC_IP
EOF
