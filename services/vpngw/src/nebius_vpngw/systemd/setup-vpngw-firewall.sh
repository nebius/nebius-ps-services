#!/bin/bash
set -euo pipefail

PUBLIC_IF="eth0"
# Get public IP - try metadata endpoint first, fall back to ip command
PUBLIC_IP="$(curl -s -m 5 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || ip -4 addr show dev eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo '')"

# Load peer IPs from config file (will be populated by agent)
PEER_IP_FILE="/etc/vpngw_peer_ips"
mapfile -t PEER_IPS < <(grep -vE '^[[:space:]]*(#|$)' "$PEER_IP_FILE" 2>/dev/null || true)

# Load management CIDRs
MGMT_CIDR_FILE="/etc/vpngw_mgmt_cidrs"
mapfile -t MGMT_CIDRS < <(grep -vE '^[[:space:]]*(#|$)' "$MGMT_CIDR_FILE" 2>/dev/null || true)

logger -t vpngw-firewall "Setting up UFW for VPN gateway"

# Reset and set defaults
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# Allow loopback
ufw allow in on lo

# Allow SSH from management CIDRs only
if [ "${#MGMT_CIDRS[@]}" -gt 0 ]; then
  for cidr in "${MGMT_CIDRS[@]}"; do
    ufw allow in on "$PUBLIC_IF" proto tcp from "$cidr" to any port 22 comment "SSH from management"
  done
  logger -t vpngw-firewall "Allowed SSH from ${#MGMT_CIDRS[@]} management CIDR(s)"
else
  # Fallback: allow SSH from anywhere (will be restricted by fail2ban)
  logger -t vpngw-firewall "WARNING: No management CIDRs configured, allowing SSH from anywhere"
  ufw allow in on "$PUBLIC_IF" proto tcp to any port 22 comment "SSH (unrestricted)"
fi

# Allow IPsec protocols from peer IPs
if [ "${#PEER_IPS[@]}" -gt 0 ]; then
  for peer in "${PEER_IPS[@]}"; do
    # IKE (Internet Key Exchange)
    ufw allow in on "$PUBLIC_IF" proto udp from "$peer" to "$PUBLIC_IP" port 500 comment "IKE from $peer"
    # NAT-T (NAT Traversal)
    ufw allow in on "$PUBLIC_IF" proto udp from "$peer" to "$PUBLIC_IP" port 4500 comment "NAT-T from $peer"
    # ESP (Encapsulating Security Payload)
    ufw allow in on "$PUBLIC_IF" proto esp from "$peer" to "$PUBLIC_IP" comment "ESP from $peer"
  done
  logger -t vpngw-firewall "Allowed IPsec from ${#PEER_IPS[@]} peer(s)"
else
  # No peers yet - allow from anywhere (will be restricted later by agent)
  logger -t vpngw-firewall "WARNING: No peer IPs configured yet, allowing IPsec from anywhere temporarily"
  ufw allow in on "$PUBLIC_IF" proto udp to any port 500 comment "IKE (unrestricted)"
  ufw allow in on "$PUBLIC_IF" proto udp to any port 4500 comment "NAT-T (unrestricted)"
  ufw allow in on "$PUBLIC_IF" proto esp comment "ESP (unrestricted)"
fi

# BGP runs only over XFRM interfaces; do not expose TCP 179 on public interface
# Remove any existing TCP/179 allow rule (if present)
if ufw --force delete allow 179/tcp >/dev/null 2>&1; then
  logger -t vpngw-firewall "Removed TCP/179 allow rule"
else
  logger -t vpngw-firewall "No TCP/179 allow rule to remove"
fi

# Allow traffic from local VPC subnets (forwarding through gateway)
# This enables VMs in the VPC to reach remote networks via VPN
LOCAL_PREFIXES_FILE="/etc/vpngw_local_prefixes"
if [ -f "$LOCAL_PREFIXES_FILE" ]; then
  mapfile -t LOCAL_PREFIXES < <(grep -vE '^[[:space:]]*(#|$)' "$LOCAL_PREFIXES_FILE" 2>/dev/null || true)
  for prefix in "${LOCAL_PREFIXES[@]}"; do
    ufw allow from "$prefix" comment "Local VPC subnet"
    logger -t vpngw-firewall "Allowed traffic from local subnet: $prefix"
  done
fi

# CRITICAL: Do NOT filter tunnel interfaces - BGP runs on them
# UFW by default only filters on eth0, but let's be explicit
# Allow all traffic on tunnel interfaces (XFRM xfrm-*)
logger -t vpngw-firewall "Tunnel interfaces are not filtered - BGP traffic allowed"

# Explicitly allow all traffic on XFRM interfaces (xfrm-*)
for xfrm_if in $(ip link show type xfrm 2>/dev/null | grep -oP '^[0-9]+: \K[^:]+'); do
  ufw allow in on "$xfrm_if"
  ufw allow out on "$xfrm_if"
  logger -t vpngw-firewall "Allowed traffic on XFRM interface: $xfrm_if"
done

# Ensure ICMP and TCP MSS clamping are applied via /etc/ufw/before.rules
BEFORE_RULES="/etc/ufw/before.rules"
if [ -f "$BEFORE_RULES" ]; then
python3 - <<'PY'
from pathlib import Path

path = Path("/etc/ufw/before.rules")
try:
    lines = path.read_text().splitlines()

    def find_table(lines, table):
        start = None
        for i, line in enumerate(lines):
            if line.strip() == f"*{table}":
                start = i
                break
        if start is None:
            return None
        for j in range(start + 1, len(lines)):
            if lines[j].strip() == "COMMIT":
                return start, j
        return None

    def upsert_block(lines, marker, end_marker, block_lines, table):
        if marker in lines and end_marker in lines:
            start = lines.index(marker)
            end = lines.index(end_marker, start + 1)
            new_block = [marker] + block_lines + [end_marker]
            if lines[start : end + 1] != new_block:
                return lines[:start] + new_block + lines[end + 1 :], True
            return lines, False

        block = [marker] + block_lines + [end_marker]
        table_loc = find_table(lines, table)
        if table_loc:
            _, end = table_loc
            return lines[:end] + block + lines[end:], True

        if table == "mangle":
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(
                [
                    "*mangle",
                    ":PREROUTING ACCEPT [0:0]",
                    ":INPUT ACCEPT [0:0]",
                    ":FORWARD ACCEPT [0:0]",
                    ":OUTPUT ACCEPT [0:0]",
                    ":POSTROUTING ACCEPT [0:0]",
                ]
            )
            lines.extend(block)
            lines.append("COMMIT")
            return lines, True

        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
        return lines, True

    changed = False
    icmp_block = [
        "-A ufw-before-input -i eth0 -p icmp --icmp-type destination-unreachable -j ACCEPT",
        "-A ufw-before-input -i eth0 -p icmp --icmp-type time-exceeded -j ACCEPT",
        "-A ufw-before-input -i eth0 -p icmp --icmp-type parameter-problem -j ACCEPT",
        "-A ufw-before-input -i eth0 -p icmp --icmp-type echo-request -j ACCEPT",
        "-A ufw-before-input -p icmp --icmp-type fragmentation-needed -j ACCEPT",
        "-A ufw-before-output -p icmp --icmp-type fragmentation-needed -j ACCEPT",
    ]
    lines, updated = upsert_block(
        lines,
        "# vpngw-icmp-allow",
        "# vpngw-icmp-allow-end",
        icmp_block,
        "filter",
    )
    changed = changed or updated

    mss_block = [
        "-A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu",
    ]
    lines, updated = upsert_block(
        lines,
        "# vpngw-mss-clamp",
        "# vpngw-mss-clamp-end",
        mss_block,
        "mangle",
    )
    changed = changed or updated

    if changed:
        path.write_text("\n".join(lines).rstrip() + "\n")
except Exception as exc:
    print(f"[vpngw-firewall] Failed to update before.rules: {exc}")
PY
fi

# Allow ICMP for troubleshooting (ufw may not support proto icmp directly)
if ufw allow in on "$PUBLIC_IF" proto icmp comment "ICMP for troubleshooting" >/dev/null 2>&1; then
  logger -t vpngw-firewall "Allowed ICMP on $PUBLIC_IF via UFW rule"
else
  logger -t vpngw-firewall "UFW does not support proto icmp; relying on /etc/ufw/before.rules"
fi

# CRITICAL: Set forward policy to ACCEPT for VPN routing
# Default is DROP which blocks all forwarded packets
logger -t vpngw-firewall "Setting DEFAULT_FORWARD_POLICY to ACCEPT"
python3 - <<'PY'
from pathlib import Path

path = Path("/etc/default/ufw")
contents = path.read_text()
updated = contents.replace(
    'DEFAULT_FORWARD_POLICY="DROP"',
    'DEFAULT_FORWARD_POLICY="ACCEPT"',
)
if 'DEFAULT_FORWARD_POLICY="ACCEPT"' not in updated:
    raise RuntimeError("unable to set DEFAULT_FORWARD_POLICY to ACCEPT")
if updated != contents:
    # Write the already-mounted file directly.  systemd exposes this exact file
    # through ReadWritePaths while keeping its parent directory read-only, so
    # tools such as `sed -i` that create a sibling temporary file cannot work.
    path.write_text(updated)
PY

# Enable firewall
ufw --force enable

# Preserve the exact private VM-HA mTLS peer rule across the full UFW reset.
if [ -x /usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh ]; then
  /usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh \
    /etc/nebius-vpngw/config-resolved.yaml
fi

logger -t vpngw-firewall "UFW configuration complete"
ufw status verbose | logger -t vpngw-firewall
