# Nebius VPN Gateway (VM-Based)

> Note: Legacy VTI support has been removed. XFRM interfaces are the only supported mode going forward.

VM-based site-to-site IPsec/BGP VPN gateway for Nebius AI Cloud. Supports GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS, and custom peers.

## Table of Contents

- [Security Notice](#security-notice)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Commands](#commands)
- [Routing Modes](#routing-modes)
- [BGP Configuration](#bgp-configuration)
- [Static Routing](#static-routing)
- [Peer Integration](#peer-integration)
- [VM Management](#vm-management)
- [Monitoring](#monitoring)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Release & Versioning](#release--versioning)
- [Project Structure](#project-structure)

## Security Notice

**Configuration files contain sensitive secrets (PSKs, service account keys).**

- **Recommended:** Name configs `*.config.yaml` (auto-ignored by git)
- **Required:** Ensure `.gitignore` includes your config file patterns
- **Best practice:** Use environment variables for secrets with `${VAR}` syntax

## Features

- **IPsec:** IKEv2 (default) + IKEv1 fallback, PSK auth, modern crypto (AES-256, SHA-256/384/512)
- **Routing:** BGP (FRR, preferred) or static routes
- **Idempotent:** Declarative YAML config, no manual state management
- **Peer support:** GCP HA VPN, AWS Site-to-Site, Azure VPN Gateway, Cisco IOS
- **Validation:** Strict Pydantic schema catches typos and invalid values
- **HA options:** Single VM (multi-tunnel) or gateway group (VM-level HA, not supported on the current Nebius VM)

## Installation

### End users (pipx + GitHub release wheel)

- Requirements: Python 3.10–3.12 (e.g., `brew install python@3.12` on macOS, `sudo apt-get install python3.12 python3.12-venv` on Ubuntu).
- Install pipx (preferred via package manager to avoid PEP 668 errors):
  - macOS (Homebrew): `brew install pipx && pipx ensurepath`
  - Ubuntu/Debian: `sudo apt-get install pipx && pipx ensurepath`
  - If you must use pip: `python3 -m pip install --user pipx --break-system-packages && python3 -m pipx ensurepath`
- Download the latest `nebius_vpngw-<version>-py3-none-any.whl` from this repository’s GitHub Release assets (version comes from the Git tag).
- Install with pipx:

```bash
pipx install /path/to/nebius_vpngw-<version>-py3-none-any.whl
```

If pipx reports that its bin dir is not on PATH (e.g., `~/.local/bin`), run:

```bash
pipx ensurepath
# then restart your shell, or:
exec $SHELL
```

- Upgrade when a new tag is released: `pipx upgrade nebius-vpngw`.
- Verify: `nebius-vpngw --version`.

**Version:** Sourced from Git tags (SemVer). Run `nebius-vpngw --version` after install.

### Developers (editable install)

- Create a virtual environment (Python 3.10–3.12) and activate it:

```bash
python3 -m venv ~/venvs/nebius-vpngw
source ~/venvs/nebius-vpngw/bin/activate
```

- Install in editable mode with developer tools:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

- Confirm the CLI is reachable: `nebius-vpngw --help`. Developer extras include linting, tests, PyInstaller, and build tooling.

## Quick Start

### Prerequisites

- Nebius AI Cloud account
- Nebius AI Cloud project with VPC network
- Python 3.10–3.12 runtime (install the CLI with pipx or via editable install as above)

### Firewall Requirements

The VPN gateway automatically configures UFW on the VPN gateway with the following rules:

**Required Ports (automatically configured):**

> Public (eth0):  IKE / ESP only, SSH, ICMP
> Tunnel (xfrm*): BGP (tcp/179), ICMP, routed traffic

- **UDP 500** - IKE (Internet Key Exchange) for IPsec tunnel establishment
- **UDP 4500** - IPsec NAT-T (NAT Traversal) for ESP over UDP
- **ESP (IP Protocol 50)** - Encapsulating Security Payload for encrypted data
- **TCP 22** - SSH for management access
- **ICMP** - For troubleshooting and diagnostics
- **TCP 179** - BGP for dynamic routing (over xfrm* only; not exposed on public interface)

**BGP (TCP/179) scope:**

- Allowed only on xfrm interfaces, between APIPA peers (inner_local_ip ↔ inner_remote_ip)
- APIPA inner IPs are assigned to xfrm interfaces and only exist after IPsec decryption
- TCP/179 is NOT opened on the public interface (eth0)

**Interface-specific rules (conceptual):**

```text
eth0 (public):
  allow udp/500 from <peer_public_ips>
  allow udp/4500 from <peer_public_ips>
  allow esp from <peer_public_ips>
  allow tcp/22 from <management_cidrs> (or anywhere if unset)
  allow icmp

xfrm*:
  allow all (includes tcp/179 between APIPA peers)
```

**Traffic Flow:**

- **Inbound:** Restricted to peer VPN gateway IPs (for IPsec) and management CIDRs (for SSH)
- **Outbound:** Unrestricted (default allow)
- **Local VPC subnets:** Allowed to forward traffic through the gateway
- **Tunnel interfaces (xfrm*):** Unrestricted (required for BGP and encrypted traffic)
- **BGP:** Runs only over xfrm* using APIPA inner IPs (no TCP/179 on eth0)

**Peer Gateway Requirements:**

- **GCP Cloud VPN:** No additional firewall configuration needed (handled automatically by GCP)
- **AWS VPN Gateway:** No additional firewall configuration needed (handled automatically by AWS)
- **Azure VPN Gateway:** No additional firewall configuration needed (handled automatically by Azure)
- **On-premises/Cisco:** Ensure firewall allows UDP 500, UDP 4500, and ESP (protocol 50) from/to Nebius gateway public IP

**Note:** UFW is the default and recommended firewall. The system automatically enables and configures it during VM deployment.

### Permanent MTU Strategy (XFRM)

The gateway enforces a conservative MTU policy so workloads don't rely on PMTUD alone:

- Always enable TCP MSS clamping on the gateway
- Enable TCP MTU probing
- Set XFRM MTU to parent MTU minus IPsec/NAT-T overhead (default 64 bytes)
- Keep eth0 MTU unchanged
- Expect PMTU ~1380-1386 for GCP HA VPN with NAT-T

**Rules applied by the agent:**

```bash
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
# nftables equivalent:
nft add rule ip mangle forward tcp flags syn tcp option maxseg size set rt mtu
```

**Sysctl (persistent):**

```bash
net.ipv4.tcp_mtu_probing = 1
```

### Required Information from Peer Gateway

Before configuring your VPN gateway, collect the following information from your peer gateway (e.g., GCP Cloud Router, AWS VPN, Azure VPN Gateway):

**Routing mode:** `bgp` or `static`

**For BGP mode:**

- Remote ASN number (e.g., `65014`)
- BGP timers (optional, defaults shown):
  - `hold_time_seconds: 60`
  - `keepalive_seconds: 20`
- Number of tunnels (e.g., `2` for HA VPN)

**For static mode:**

- Remote prefixes/subnets (e.g., `10.10.0.0/16`, `10.20.0.0/16`)
- Number of tunnels (e.g., `2` for HA VPN)

**For each tunnel (all modes):**

- Remote public IP address
- Pre-shared key (PSK)
- Inner tunnel CIDR (e.g., `169.254.5.152/30`)
- Inner local IP (e.g., `169.254.5.154`)
- Inner remote IP (e.g., `169.254.5.153`)

> **Note:** Inner /30s are required for XFRM interface addressing even in static mode; GCP Cloud Router and AWS VPN provide all this information in their console/CLI output after creating the VPN gateway and tunnels.

### First Deployment

**1. Create configuration from template:**

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

**2. Edit configuration:**

```yaml
version: 1

tenant_id: ${TENANT_ID}
project_id: ${PROJECT_ID}
region_id: ${REGION_ID}

gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 1
  external_ips: []
  vm_spec:
    platform: "cpu-d3"
    preset: "4vcpu-16gb"
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 50
    disk_type: "network_ssd"
    disk_block_bytes: 4096
    num_nics: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
  
gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
  # ipsec_mode: xfrm-interface  # Default: modern XFRM (recommended)

defaults:
  vpn_type: ipsec
  ike_version: 2
  allow_ikev1: true
  auth:
    method: psk
  crypto:
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
    ike_lifetime_seconds: 28800
    esp_proposals:
      - "aes256gcm16-modp2048"
    esp_lifetime_seconds: 3600
    dh_groups:
      - 14
  dpd:
    interval_seconds: 30
    timeout_seconds: 120
  routing:
    mode: bgp
    bgp:
      hold_time_seconds: 60
      keepalive_seconds: 20
      graceful_restart: true
      max_prefixes: 1000
    
connections:
  - name: gcp-ha-vpn
    vendor: gcp
    routing_mode: bgp
    bgp:
      enabled: true
      remote_asn: 65001
      advertise_local_prefixes: true
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        ha_role: "active"
        psk: ${GCP_TUNNEL_1_PSK}
        remote_public_ip: "203.0.113.1"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: tunnel-2
        gateway_instance_index: 0
        ha_role: "passive"
        psk: ${GCP_TUNNEL_2_PSK}
        remote_public_ip: "203.0.113.2"
        inner_cidr: "169.254.10.4/30"
        inner_local_ip: "169.254.10.5"
        inner_remote_ip: "169.254.10.6"
```

**3. Set environment variables:**

```bash
export TENANT_ID="my-tenant-id"
export PROJECT_ID="my-project-id"
export REGION_ID="eu-north1"
export GCP_TUNNEL_1_PSK="your-pre-shared-key-1"
export GCP_TUNNEL_2_PSK="your-pre-shared-key-2"
```

**4. Validate configuration:**

```bash
nebius-vpngw validate-config my-vpn.config.yaml
```

**5. Deploy:**

```bash
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

**6. Check status:**

```bash
nebius-vpngw status --local-config-file my-vpn.config.yaml
```

## Architecture

**Components:**

- **Orchestrator CLI:** Runs locally, manages VM lifecycle and config deployment
- **Gateway VM(s):** Ubuntu LTS with strongSwan (IPsec), FRR (BGP), agent daemon
- **Agent:** On-VM service that renders and applies configs idempotently

**Deployment modes:**

- Single VM: Multiple tunnels, VM is single point of failure
- Gateway group: Multiple VMs with per-tunnel pinning for VM-level HA

**Networking:**

- Dedicated `vpngw-subnet` (/24 CIDR) for gateway isolation
- One NIC per VM (platform constraint), future-ready for multi-NIC
- Public IP allocations preserved across VM recreation

For detailed architecture, see [design document](doc/design.md).

## Configuration

### File Structure

```yaml
version: 1

tenant_id: ${TENANT_ID}
project_id: ${PROJECT_ID}
region_id: ${REGION_ID}

gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 2
  external_ips: []  # Auto-allocate
  vm_spec:
    platform: "cpu-d3"
    preset: "4vcpu-16gb"
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 50
    disk_type: "network_ssd"
    disk_block_bytes: 4096
    num_nics: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"

gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
    - "10.1.0.0/16"

defaults:
  vpn_type: ipsec
  ike_version: 2
  allow_ikev1: true
  auth:
    method: psk
  crypto:
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
    ike_lifetime_seconds: 28800
    esp_proposals:
      - "aes256gcm16-modp2048"
    esp_lifetime_seconds: 3600
    dh_groups:
      - 14
  dpd:
    interval_seconds: 30
    timeout_seconds: 120
  routing:
    mode: bgp
    bgp:
      hold_time_seconds: 60
      keepalive_seconds: 20
      graceful_restart: true
      max_prefixes: 1000

connections:
  - name: peer-vpn
    vendor: generic
    routing_mode: bgp
    # Optional in BGP mode: used for filtering received BGP routes
    # remote_prefixes:
    #   - "192.168.0.0/16"
    bgp:
      enabled: true
      remote_asn: 65001
      advertise_local_prefixes: true
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        local_public_ip_index: 0
        psk: ${TUNNEL_1_PSK}
        remote_public_ip: "203.0.113.1"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: tunnel-2
        gateway_instance_index: 1
        local_public_ip_index: 0
        psk: ${TUNNEL_2_PSK}
        remote_public_ip: "203.0.113.2"
        inner_cidr: "169.254.10.4/30"
        inner_local_ip: "169.254.10.5"
        inner_remote_ip: "169.254.10.6"
```

### Remote Prefixes: Static vs BGP Mode

**The `remote_prefixes` field has different semantics depending on routing mode:**

**BGP Mode (default):**

- `remote_prefixes` is **optional**
- If omitted: BGP learns ALL routes advertised by peer dynamically
- If specified: Acts as an inbound **whitelist filter** - only listed prefixes are accepted from BGP
- Routes are installed automatically by BGP, not manually
- Example: Peer advertises 300 networks → you don't need to list all 300 in YAML

```yaml
connections:
  - name: gcp-vpn
    vendor: gcp
    routing_mode: bgp
    # Optional: Whitelist specific prefixes (filter)
    remote_prefixes:
      - "10.0.0.0/8"   # Only accept 10.0.0.0/8 from peer
    bgp:
      enabled: true
      remote_asn: 65001
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
```

**Static Mode:**

- `remote_prefixes` is **required** (or specified per-tunnel in `static_routes`)
- Used to install kernel routes via XFRM interfaces (rightsubnet stays 0.0.0.0/0)
- You must enumerate each remote network manually
- No dynamic route learning

```yaml
connections:
  - name: peer-vpn
    vendor: generic
    routing_mode: static
    remote_prefixes:      # Required: actual routes to install
      - "192.168.1.0/24"
      - "192.168.2.0/24"
    bgp:
      enabled: false
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.20.0/30"
        inner_local_ip: "169.254.20.1"
        inner_remote_ip: "169.254.20.2"
```

### Schema Validation

**Strict validation** enforces correctness before deployment:

- **Type safety:** IPs, CIDRs, ASNs, booleans validated
- **Constraints:** ASN validated (private 64512-65534 recommended; public/extended allowed), /30 subnets, APIPA 169.254.0.0/16
- **Consistency:** BGP mode requires `bgp.enabled: true` and `bgp.remote_asn`; inner IPs must be host addresses within inner_cidr
- **Unknown fields:** Rejects typos like `inner_ciddr` or `remote_ips`

**API versioning:**

- `version: 1` required in all configs
- Future schema changes increment version
- Backwards compatibility maintained

**Validation workflow:**

```bash
# Explicit validation
nebius-vpngw validate-config my-vpn.config.yaml

# Automatic during deployment
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

### Environment Variables

Use `${VAR}` for secrets and environment-specific values:

```yaml
tenant_id: ${TENANT_ID}
project_id: ${PROJECT_ID}
region_id: ${REGION_ID}
psk: ${TUNNEL_1_PSK}
remote_public_ip: ${PEER_IP_1}
```

Missing variables are reported before deployment.

### Template Generation

Generate new config with comprehensive comments:

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

Template embedded in code, always aligned with schema. Files ending in `.config.yaml` are auto-ignored by git.

### Merge Precedence

Settings cascade with specific overriding general:

1. Tunnel-level settings (highest priority)
2. Connection-level settings
3. Peer config imports
4. Global defaults (lowest priority)

## Commands

### Configuration Management

**Create new config:**

```bash
nebius-vpngw create-config <file>
# Use --force to overwrite existing files
```

**Validate config:**

```bash
nebius-vpngw validate-config <file>
# Returns exit code 0 (valid) or 1 (invalid)
```

**Note:** `validate-config` takes the config file as a positional argument, not as `--local-config-file`. This is different from other commands which use the flag syntax.

**Generate from peer config (no deployment):**

```bash
nebius-vpngw create-from-peer-config my-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file aws-peer.xml
```

### Deployment

**Deploy or update:**

```bash
nebius-vpngw apply --local-config-file <file>

# Force VM recreation
nebius-vpngw apply --local-config-file <file> --recreate-gw

# Override project/zone
nebius-vpngw apply --local-config-file <file> --project-id <id> --zone <zone>
```

### Monitoring

**Check status:**

```bash
nebius-vpngw status --local-config-file <file>
```

Shows tunnel status, BGP sessions, service health, routing validation.

**Manage routes:**

```bash
# List local routes (Nebius VPC → Remote)
# Shows route tables for subnets matching gateway.local_prefixes
nebius-vpngw list-routes-local --local-config-file <file>

# Add local routes (Nebius VPC → Remote)
# - BGP mode: Queries BGP-learned routes from gateway VMs via FRR
# - Static mode: Uses remote_prefixes from YAML configuration
# - Creates VPC route table entries with gateway private IP as next-hop
# - Filters out local networks automatically
# - Copies existing routes when creating custom route tables
nebius-vpngw add-routes-local --local-config-file <file>

# List remote routes (Remote → Nebius)
# - BGP mode: Shows BGP-learned routes with whitelist status and XFRM interfaces
# - Static mode: Shows static routes and kernel installation status
# - Filters out locally originated routes (next-hop 0.0.0.0)
nebius-vpngw list-routes-remote --local-config-file <file>
```

**Route Management Concepts:**

- **Local Routes (Nebius → Remote)**: VPC route table entries that direct traffic from Nebius subnets to remote networks via the VPN gateway
  - Destination: Remote networks (BGP-learned or statically configured)
  - Next-hop: VPN gateway private IP
  - Managed via Nebius VPC API

- **Remote Routes (Remote → Nebius)**: Routes on the gateway VMs that direct traffic from remote sites to Nebius networks
  - BGP mode: Dynamically learned via FRR and installed in kernel
  - Static mode: Manually configured in YAML
  - Visible via SSH queries to gateway VMs

**Tunnel Management:**

```bash
# Manually restart a specific tunnel
nebius-vpngw restart-tunnel gcp-ha-tunnel-1 --local-config-file <file>

# Restart all tunnels on all gateway VMs
nebius-vpngw restart-tunnel all --local-config-file <file>
```

**When to use:**

- Recovering from tunnel state desynchronization issues
- After detecting connectivity failures
- During maintenance windows
- Testing failover behavior

**What it does:**

- SSHs to each gateway VM
- Restarts the `nebius-vpngw-agent` service
- Agent teardown and recreates IPsec tunnels
- XFRM interfaces are recreated
- BGP sessions are reset

**Recovery time:** 10-15 seconds (tunnel establishment + BGP convergence)

### Automated Health Monitoring

The gateway includes an automated health monitoring system that detects and recovers from tunnel failures.

**Configuration:**

Add to your `nebius-vpngw.config.yaml`:

```yaml
defaults:
  health_monitoring:
    enabled: true                          # Enable automated monitoring
    check_interval_seconds: 60             # Check every 60 seconds
    max_failures_before_restart: 2         # Restart after 2 consecutive failures
    proactive_refresh_enabled: false       # Reactive mode (detect & fix)
    proactive_refresh_hours: 8             # Unused (proactive mode disabled)
```

**Monitoring Modes:**

| Mode                   | Behavior                                      | Downtime                  | Use Case                    |
|------------------------|-----------------------------------------------|---------------------------|-----------------------------|
| **Reactive (default)** | Detect failures, restart only when broken     | ~65s during failures      | 100% uptime priority        |
| **Proactive**          | Periodic restart every N hours (preventive)   | ~10-15s every N hours     | Prevent stale state buildup |

**Detection Timing:**

With `max_failures_before_restart: 2` and `check_interval_seconds: 60`:

1. **t=0s:** Normal operation
2. **t=60s:** First failure detected → Immediate re-check in 5 seconds
3. **t=65s:** Second failure confirmed → Tunnel restarted immediately
4. **t=85s:** Tunnel re-established, traffic flows

**Total detection time: ~65 seconds** (not 120s)
**Total recovery time: ~85 seconds** (detection + restart)

**Service Management:**

The health monitor runs as a systemd service on each gateway VM:

```bash
# Check monitor status (SSH to gateway VM)
sudo systemctl status nebius-vpngw-health-monitor

# View monitor logs
sudo journalctl -u nebius-vpngw-health-monitor -f

# Restart monitor
sudo systemctl restart nebius-vpngw-health-monitor
```

**Keepalive Strategy:**

The gateway uses three layers of keepalive to maintain tunnel health:

1. **NAT-T Keepalives (20s):** Prevent NAT session timeouts
2. **DPD (30s checks, 120s timeout):** Detect IKE control plane failures
3. **Health Monitor (60s checks):** Detect data plane failures

This multi-layer approach ensures rapid detection and recovery from various failure modes.

## Routing Modes

### Active/Passive HA for Multi-Tunnel Connections

The gateway operates in **Active/Passive mode** to ensure symmetric routing without requiring workload VM configuration changes. When configuring multiple tunnels to the same peer (e.g., GCP HA VPN), **keep only one tunnel active** at a time.

**Tunnel Mode Configuration:**

| Desired Mode | Config Required | Description |
| ------------ | --------------- | ----------- |
| **active** | `ha_role: "active"` **OR** omit the field (default) | Primary tunnel with BGP local-preference 200. Carries all data traffic. |
| **passive** | `ha_role: "passive"` (**must be explicit**) | Standby tunnel with BGP local-preference 100. Hot standby for automatic failover. |
| **disable** | `ha_role: "disable"` (**must be explicit**) | Tunnel completely skipped (no IPsec, no BGP). |

**Important:** If you omit `ha_role` on multiple tunnels, they will all default to `"active"`, creating ECMP load balancing that may cause asymmetric routing and packet loss. Always explicitly set one tunnel to `"passive"` in multi-tunnel configurations.

**Example:**

```yaml
connections:
  - name: "gcp-ha-vpn"
    routing_mode: bgp
    tunnels:
      - name: "tunnel-1"
        ha_role: "active"    # Primary - carries traffic
        # ...
      - name: "tunnel-2"
        ha_role: "passive"   # Standby - automatic failover
        # ...
```

### BGP (Recommended)

**Advantages:**

- Dynamic route learning
- Automatic failover
- Route filtering and policies
- Scales to large networks

**Requirements:**

- `bgp.remote_asn` must be configured
- `bgp.enabled` must be true when `routing_mode: bgp`
- Inner IPs must be /30 APIPA (169.254.0.0/16)
- Peer must support BGP

**Configuration:**

```yaml
defaults:
  routing:
    mode: bgp
    
gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
    
connections:
  - name: peer
    vendor: generic
    routing_mode: bgp
    bgp:
      enabled: true
      remote_asn: 65001
      advertise_local_prefixes: true
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
```

### Static Routing

**Advantages:**

- Simpler configuration
- No BGP knowledge required
- Works with any peer

**Disadvantages:**

- Manual route management
- No automatic failover
- Requires VPC route table updates
- Must enumerate all remote networks

**Configuration:**

```yaml
defaults:
  routing:
    mode: static
    
connections:
  - name: peer
    vendor: generic
    routing_mode: static
    # Required: List all remote networks to route
    remote_prefixes:
      - "192.168.1.0/24"
      - "192.168.2.0/24"
      - "192.168.3.0/24"
    bgp:
      enabled: false
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.20.0/30"
        inner_local_ip: "169.254.20.1"
        inner_remote_ip: "169.254.20.2"
```

**Route management:**

```bash
# Add local routes to VPC route table (Nebius → Remote)
nebius-vpngw add-routes-local --local-config-file <file>

# List local routes in VPC
nebius-vpngw list-routes-local --local-config-file <file>

# List remote static routes on gateway VMs
nebius-vpngw list-routes-remote --local-config-file <file>
```

**Note:** For environments with 100+ remote networks, BGP mode is recommended for automatic route learning instead of manual enumeration.

## BGP Configuration

### APIPA Inner IPs

**Requirements:**

- Must be /30 subnet in 169.254.0.0/16 range
- Each tunnel needs unique /30 subnet
- Use .1 and .2 from each /30 (avoid .0 and .3)

**Examples:**

```yaml
tunnels:
  - name: tunnel-1
    inner_cidr: "169.254.10.0/30"
    inner_local_ip: "169.254.10.1"
    inner_remote_ip: "169.254.10.2"
  - name: tunnel-2
    inner_cidr: "169.254.10.4/30"
    inner_local_ip: "169.254.10.5"
    inner_remote_ip: "169.254.10.6"
```

### BGP Timers

Customize defaults:

```yaml
defaults:
  routing:
    bgp:
      hold_time_seconds: 60
      keepalive_seconds: 20
      graceful_restart: true
```

### BGP Troubleshooting

**Check BGP sessions:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Common issues:**

- **No OPEN messages:** IPsec tunnel not established or XFRM interface down
- **OPEN errors:** ASN mismatch between peers
- **Routes not installed:** FRR version issue (use 10.x, not 8.4.4)
- **Policy errors:** Add `no bgp ebgp-requires-policy` (automatically configured)

**SSH to VM for debugging:**

```bash
ssh ubuntu@<gateway-ip>
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show ip route"
```

## Static Routing Configuration

### VPC Route Management

Add routes to VPC route table (Nebius → Remote):

```bash
nebius-vpngw add-routes-local --local-config-file <file>
```

Creates routes for `connection.remote_prefixes` pointing to gateway VMs.

List routes in VPC:

```bash
nebius-vpngw list-routes-local --local-config-file <file>
```

List routes on gateway VMs (Remote → Nebius):

```bash
# BGP mode: Shows BGP-learned routes with whitelist status
# Static mode: Shows static routes and kernel installation status
nebius-vpngw list-routes-remote --local-config-file <file>
```

## Peer Integration

### Supported Vendors

- **GCP HA VPN:** Cloud Router config exports
- **AWS Site-to-Site VPN:** Downloadable config files
- **Azure VPN Gateway:** Exported configurations
- **Cisco IOS:** IOS config snippets

### Import Workflow

```bash
nebius-vpngw create-from-peer-config nebius-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file aws-peer.xml
```

**Merge behavior:**

- Peer values overwrite template defaults when present
- Topology is taken from the generated config and should be reviewed
- Validate before deployment

### Example: GCP HA VPN

**1. Export GCP Cloud Router config:**

```bash
gcloud compute routers describe my-router \
  --region us-central1 \
  --format yaml > gcp-peer.txt
```

**2. Generate a Nebius config from the peer file:**

```bash
nebius-vpngw create-from-peer-config gcp-ha-vpn.config.yaml \
  --peer-config-file gcp-peer.txt
```

**3. Review and fill in required values (tenant/project/region/PSKs/local prefixes):**

```yaml
connections:
  - name: gcp-ha-vpn
    vendor: gcp
    routing_mode: bgp
    bgp:
      enabled: true
      remote_asn: 65014
      advertise_local_prefixes: true
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${GCP_TUNNEL_1_PSK}
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: tunnel-2
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.2"
        psk: ${GCP_TUNNEL_2_PSK}
        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"
```

**4. Validate and deploy:**

```bash
nebius-vpngw validate-config gcp-ha-vpn.config.yaml
nebius-vpngw apply --local-config-file gcp-ha-vpn.config.yaml
```

Peer import only fills what it can from the vendor file; PSKs and public IPs may still need to be set manually.

## VM Management

### VM Lifecycle

**Create:** Initial provisioning with cloud-init hardening

**Update:** Config push + agent reload (no VM recreation)

**Recreate:** Explicit `--recreate-gw` flag required

### VM Recreation Workflow

```bash
nebius-vpngw apply --local-config-file <file> --recreate-gw
```

**Process:**

1. Detach public IP allocations from old VM
2. Delete old VM
3. Create new VM with same specs
4. Reattach public IP allocations

**Downtime:** Tunnel re-establishment time only (IPs never change)

### Public IP Preservation

`external_ips` is a list per instance; each inner list maps to NICs on that VM. Legacy flat lists are not supported.

**Configuration:**

```yaml
gateway_group:
  external_ips: []  # Auto-allocate
  # OR
  external_ips:
    - ["203.0.113.10"]  # VM 0
    - ["203.0.113.20"]  # VM 1
```

**Behavior:**

- Empty/omitted: Auto-create allocations
- Provided: Use existing allocations
- Preserved across VM recreation

## System Monitoring

### Status Overview

```bash
nebius-vpngw status --local-config-file <file>
```

**Reports:**

- Tunnel status (ESTABLISHED, CONNECTING, DOWN)
- BGP session state and route counts
- Service health (agent, strongSwan, FRR)
- Routing validation (table 220, APIPA routes, orphaned routes)

### Tunnel Status

Per-tunnel information:

- Gateway VM assignment
- Peer IP address
- Encryption algorithm (e.g., AES_GCM_16-256)
- Uptime
- BGP state (for BGP tunnels)

### System Health

Service status per VM:

- `nebius-vpngw-agent`: Agent daemon
- `strongswan-starter`: IPsec daemon
- `frr`: Routing daemon

### Routing Validation

Per-VM checks:

- **Table 220:** Detects policy routes (causes asymmetric routing)
- **Broad APIPA:** Detects 169.254.0.0/16 routes (should be /30 only)
- **BGP peer routes:** Shows APIPA routes over XFRM interfaces
- **Orphaned routes:** Routes without corresponding tunnels

## Security

### Cloud-Init Hardening

Applied at VM creation:

- SSH key-only authentication, root login disabled
- Fail2ban for SSH intrusion prevention
- UFW firewall (allows IPsec UDP 500/4500, ESP)
- auditd for command auditing
- Automated security updates (unattended-upgrades)
- IP forwarding enabled, ICMP redirects disabled

### Dynamic Firewall

Agent synchronizes UFW rules with active tunnels:

- Adds peer IPs when tunnels configured
- Removes stale peer IPs when tunnels deleted
- Keeps local prefix rules in sync with `gateway.local_prefixes`
- XFRM interfaces not filtered by UFW (allows BGP/encrypted traffic to flow)

### Secrets Management

**Best practices:**

- Use `*.config.yaml` naming (auto-ignored by git)
- Store PSKs in environment variables
- Use `${VAR}` placeholders in config
- Rotate PSKs regularly

**Example:**

```bash
export TUNNEL_1_PSK="$(openssl rand -base64 32)"
export TUNNEL_2_PSK="$(openssl rand -base64 32)"
```

### Audit Logging

`auditd` monitors:

- Configuration file changes
- Command execution history
- Service management (systemctl)

**View audit logs:**

```bash
ssh ubuntu@<gateway-ip>
sudo ausearch -f /etc/nebius-vpngw/
```

## Troubleshooting

### Tunnel Issues

**Check tunnel status:**

```bash
nebius-vpngw status --local-config-file <file>
```

**SSH to gateway VM:**

```bash
ssh ubuntu@<gateway-ip>
sudo ipsec status
sudo ipsec statusall
```

**Check logs:**

```bash
sudo journalctl -u strongswan-starter -f
sudo journalctl -u nebius-vpngw-agent -f
```

### BGP Issues

**Check BGP sessions:**

```bash
ssh ubuntu@<gateway-ip>
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show bgp neighbors"
sudo vtysh -c "show ip route bgp"
```

**Common fixes:**

1. **ASN mismatch:** Verify `local_asn` and `bgp.remote_asn` match peer
2. **Inner IPs:** Ensure /30 APIPA subnets unique per tunnel
3. **IPsec down:** Fix tunnel before debugging BGP
4. **FRR version:** Upgrade to 10.x if routes not installing

### Routing / XFRM Issues

**Check routing health:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Manual validation:**

```bash
ssh ubuntu@<gateway-ip>
sudo ip route show table 220  # Should be empty
sudo ip route | grep 169.254  # Should show /30 tunnel routes, /32 peer routes, + metadata (169.254.169.x)
sudo ip link show type xfrm   # xfrm0/xfrm1 should exist and be UP
sudo ip addr show xfrm0       # Should have inner_local_ip/30
sudo ip xfrm policy           # Local selector = inner /30 + gateway.local_prefixes; remote = 0.0.0.0/0
```

**Defense-in-Depth Routing Protection (XFRM):**

The gateway uses a three-layer defense against problematic APIPA routing:

#### Layer 1: Kernel Configuration (Hardening)

Sysctl settings harden the kernel's network behavior for VPN environments:

```bash
# /etc/sysctl.d/99-vpn-gateway.conf (APIPA section)
net.ipv4.conf.all.route_localnet=0      # Disable IPv4 link-local routing
net.ipv4.conf.all.accept_local=0        # Prevent local address acceptance
net.ipv4.conf.all.arp_announce=2        # Strict ARP source selection
net.ipv4.conf.all.arp_ignore=1          # Strict ARP target matching
```

These settings:

- Prevent kernel from auto-generating link-local routes on interface events
- Improve ARP security for VPN tunnel operations
- Provide baseline hardening following VPN appliance best practices

**Note:** These settings do NOT prevent DHCP from adding routes (DHCP client explicitly adds them), but they reduce the attack surface and prevent other link-local route issues.

#### Layer 2: Routing Guard (Reactive - Primary Defense)

Agent's `routing_guard.py` runs AFTER all config changes:

- Removes table 220 policy routes (policy routing not used with XFRM)
- Removes broad APIPA routes (169.254.0.0/16) added by DHCP, preserving metadata routes (169.254.169.x)
- Runs on every agent start/reload
- Ensures clean state after netplan/systemd-networkd operations

This is the **primary defense** against DHCP-added broad APIPA routes.

#### Layer 3: Timer Service (Backup)

`nebius-vpngw-fix-routes.timer` runs every 5 minutes:

- Catches routes added by periodic DHCP renewals
- Independent of agent lifecycle
- Provides continuous enforcement between agent operations

**Why This Three-Layer Approach?**

Nebius cloud DHCP server provides gateway `169.254.169.1`, which causes systemd-networkd to add a `169.254.0.0/16` route. This broad route conflicts with VPN tunnel inner IPs (also in 169.254.0.0/16 APIPA range).

We cannot:

- Disable DHCP routes entirely (breaks default gateway and DNS)
- Prevent DHCP client from adding routes (systemd-networkd behavior)

We can:

1. Harden kernel with sysctl (reduce attack surface)
2. Reactively remove bad routes after DHCP adds them (routing_guard)
3. Periodically enforce cleanup (timer)

This follows the same pattern as AWS/Azure/Juniper/Cisco routers when building strongSwan customer gateways in cloud environments.

**If routes persist:**

```bash
# Manual cleanup
ssh ubuntu@<gateway-ip>
sudo systemctl start nebius-vpngw-fix-routes.service

# Or trigger agent reload
sudo systemctl reload nebius-vpngw-agent

# Check sysctl settings
sudo sysctl -a | grep -E "route_localnet|accept_local|arp_announce|arp_ignore"
```

### Agent Issues

**Reload agent:**

```bash
ssh ubuntu@<gateway-ip>
sudo systemctl reload nebius-vpngw-agent
```

**Check agent status:**

```bash
sudo systemctl status nebius-vpngw-agent
sudo journalctl -u nebius-vpngw-agent --since "10 minutes ago"
```

**Trigger config reapply:**

```bash
nebius-vpngw apply --local-config-file <file>
```

### Viewing Logs

**Agent Logs (routing guard, config changes, service health):**

```bash
ssh ubuntu@<gateway-ip>

# Real-time agent logs (routing guard, BGP peer routes, config rendering)
sudo journalctl -u nebius-vpngw-agent -f

# Recent agent logs (last 50 lines)
sudo journalctl -u nebius-vpngw-agent -n 50 --no-pager

# Agent logs since specific time
sudo journalctl -u nebius-vpngw-agent --since "10 minutes ago"

# Search for routing guard operations
sudo journalctl -u nebius-vpngw-agent | grep RoutingGuard

# View clean state confirmations
sudo journalctl -u nebius-vpngw-agent | grep "No orphan routes"

# View route cleanup operations
sudo journalctl -u nebius-vpngw-agent | grep -E "(Removed|orphan|Table 220)"
```

**Route Fix Timer Logs (periodic cleanup every 5 minutes):**

```bash
# Timer service logs
sudo journalctl -u nebius-vpngw-fix-routes.timer -n 20

# Route fix script execution logs
sudo journalctl -u nebius-vpngw-fix-routes.service -n 20

# Check systemd timer status
sudo systemctl status nebius-vpngw-fix-routes.timer
```

**strongSwan IPsec Logs (tunnel establishment, encryption):**

```bash
# Real-time strongSwan logs
sudo journalctl -u strongswan-starter -f

# Recent strongSwan logs
sudo journalctl -u strongswan-starter -n 50

# IKE negotiation logs
sudo journalctl -u strongswan-starter | grep -E "(IKE_SA|CHILD_SA|established)"

# Tunnel errors
sudo journalctl -u strongswan-starter | grep -i error
```

**FRR BGP Logs (routing protocol):**

```bash
# FRR service logs
sudo journalctl -u frr -n 50

# BGP-specific logs (if available)
sudo journalctl | grep bgpd

# Check BGP daemon directly
sudo vtysh -c "show logging" | tail -20
```

**Linux Networking Logs (netplan, systemd-networkd, DHCP):**

```bash
# systemd-networkd logs (DHCP, interface configuration)
sudo journalctl -u systemd-networkd -n 50

# Search for DHCP lease renewals
sudo journalctl -u systemd-networkd | grep -i dhcp

# Search for route additions by DHCP
sudo journalctl -u systemd-networkd | grep -E "(route|169.254)"

# netplan operations
sudo journalctl | grep netplan

# Kernel network messages
sudo dmesg | grep -E "(eth0|xfrm|route)" | tail -20
```

**Firewall Logs (UFW rule changes):**

```bash
# UFW operations from agent
sudo journalctl -u nebius-vpngw-agent | grep -i firewall

# System firewall logs
sudo journalctl | grep ufw | tail -20

# Check current UFW rules
sudo ufw status verbose
# Expected posture (no management CIDRs):
# - SSH allowed from anywhere (or from management CIDRs if configured)
# - ICMP allowed from anywhere on eth0
# - IPsec (UDP 500/4500, ESP) allowed from peers (or anywhere until peers known)
# - TCP 179 not exposed on eth0 (BGP runs over xfrm*)
# - All traffic allowed on xfrm* tunnel interfaces
# - Default deny inbound on eth0 for other ports
```

**Combined View (all critical services):**

```bash
# Follow all VPN-related logs in real-time
sudo journalctl -f -u nebius-vpngw-agent \
                    -u strongswan-starter \
                    -u frr \
                    -u systemd-networkd \
                    -u nebius-vpngw-fix-routes.service

# Recent activity across all services
sudo journalctl --since "5 minutes ago" \
                -u nebius-vpngw-agent \
                -u strongswan-starter \
                -u frr \
                -u systemd-networkd \
                --no-pager
```

**Log Patterns to Watch:**

```bash
# Successful routing guard execution (clean state)
[RoutingGuard] ✓ All invariants OK. BGP peer routes: 2

# Routing issues detected and fixed
[RoutingGuard] Found 1 unexpected APIPA route(s) to remove
[RoutingGuard] Removed orphan APIPA route: 169.254.99.0/24 dev eth0
[RoutingGuard] Removed policy rule: pref 220 from all lookup 220
[RoutingGuard] ✓ Table 220 completely removed
[RoutingGuard] Summary: sysctls_fixed=0 table_220_removed=True broad_apipa_removed=True scope_link_routes_removed=0 orphaned_apipa_removed=1 bgp_peer_routes_ensured=2
[RoutingGuard] ✓ Routing invariants enforced

# BGP sessions established
[RoutingGuard] Ensured route 169.254.18.225/32 via xfrm0
[RoutingGuard] Ensured route 169.254.5.153/32 via xfrm1

# Config changes applied
[Agent] Received signal 1; reloading
[Agent] No changes detected; skipping config render
```

**Troubleshooting Specific Issues:**

```bash
# Issue: BGP not establishing
sudo journalctl -u nebius-vpngw-agent | grep RoutingGuard  # Check routing
sudo journalctl -u strongswan-starter | grep ESTABLISHED   # Check IPsec
sudo vtysh -c "show bgp summary"                           # Check BGP state

# Issue: Routes keep getting added back
sudo journalctl -u systemd-networkd --since "1 hour ago" | grep 169.254
sudo journalctl -u nebius-vpngw-fix-routes.service -n 20

# Issue: Tunnel won't establish
sudo journalctl -u strongswan-starter -n 100 | grep -E "(error|fail|timeout)"
sudo ipsec statusall

# Issue: Agent not responding
sudo systemctl status nebius-vpngw-agent
sudo journalctl -u nebius-vpngw-agent --since "1 hour ago" | tail -50
ps aux | grep "python3 -m nebius_vpngw.agent.main"
```

## Development

### Agent Development

**Modify agent code:**

```bash
# Edit files in src/nebius_vpngw/agent/
vim src/nebius_vpngw/agent/main.py
```

**Rebuild and deploy:**

```bash
python -m build --wheel
nebius-vpngw apply --local-config-file <file>
```

Agent wheel uploaded automatically to VMs.

### Testing Changes

**Schema validation:**

```bash
nebius-vpngw validate-config test.config.yaml
```

**Deploy to test environment:**

```bash
nebius-vpngw apply \
  --local-config-file test.config.yaml \
  --project-id test-project \
  --zone eu-north1-c
```

**Check results:**

```bash
nebius-vpngw status --local-config-file test.config.yaml
```

### Dependency Updates

**Update pyproject.toml:**

```toml
[project]
dependencies = [
  "pydantic>=2.12.0,<3.0.0",
  # ...
]
```

**Refresh your editable install and rebuild when needed:**

```bash
pip install -e ".[dev]"
python -m build --wheel
```

### linting the codes

```bash
python -m ruff check src --fix
```

## Release & Versioning

- Versions are derived from annotated Git tags (`vMAJOR.MINOR.PATCH`) via `setuptools-scm`; no manual edits to `pyproject.toml` are needed. The generated version is written to `src/nebius_vpngw/_version.py` during build and surfaced via `nebius-vpngw --version`.
- Semantic Versioning policy:
  - **MAJOR:** breaking changes (CLI flags removed/changed, behavior changes that could break scripts).
  - **MINOR:** backward-compatible features (new options, new Nebius resources supported).
  - **PATCH:** bug fixes only (no breaking behavior, no new major capability).
- Keep `CHANGELOG.md` updated before tagging; the changelog is the human-friendly record of what changed.
- If you build without a tag, `setuptools-scm` will fall back to `0.0.0`; create a proper `vX.Y.Z` tag before shipping artifacts.

### Choosing the next SemVer

Bump **MAJOR** if there’s a breaking change (CLI flags or behavior changes that can break scripts).
Bump **MINOR** for backward-compatible features.
Bump **PATCH** for fixes only.
**Current working version (including dev distance):** `python -m setuptools_scm`

### How to create a release for this project

1. Prepare on your working branch: `./release.sh --prep vX.Y.Z`
2. Open a PR and merge it to `main`.
3. On `main`, publish the release: `./release.sh --publish vX.Y.Z`

Note: `--publish` requires `main` to be clean and up to date with `origin/main`.

### Optional: build a single-file binary (PyInstaller)

- Use when you need a standalone executable instead of a wheel/pipx install.
- Install the tool once in your environment: `pip install pyinstaller`.
- Build the binary: `python -m nebius_vpngw.build` (or run the `build-binary` console script).
- Output lands at `dist/nebius-vpngw`; ship or copy that file directly.

## Project Structure

```text
├── LICENSE
├── README.md
├── pyproject.toml
├── *.config.yaml                         # User configs (git-ignored)
├── release.sh                            # One-shot release helper (commit/tag/build/publish with gh)
├── doc/
│   └── design.md                         # Detailed design document
├── image/
│   ├── vpngw-architecture.dot            # Architecture diagrams
│   └── vpngw-conn-diagram.dot
├── src/nebius_vpngw/
│   ├── __init__.py
│   ├── __main__.py                       # Entry point
│   ├── cli.py                            # CLI orchestrator
│   ├── config_loader.py                  # YAML parser and merger
│   ├── schema.py                         # Pydantic validation schema
│   ├── config_template.py                # Embedded YAML template (source of truth)
│   ├── build.py                          # Binary build utilities
│   ├── vpngw_sa.py                       # Service account management
│   ├── agent/                            # On-VM agent
│   │   ├── main.py                       # Agent daemon
│   │   ├── frr_renderer.py               # BGP config renderer
│   │   ├── strongswan_renderer.py        # IPsec config renderer
│   │   ├── xfrm_manager.py               # XFRM interface lifecycle (create, address, route)
│   │   ├── routing_guard.py              # Route validation
│   │   ├── firewall_manager.py           # UFW rule sync
│   │   ├── tunnel_iterator.py            # Tunnel enumeration
│   │   ├── state_store.py                # State persistence
│   │   ├── status_check.py               # Health checks
│   │   ├── sanity_check.py               # Routing validation tool
│   │   └── tunnel_health_monitor.py      # Automated tunnel health monitoring
│   ├── deploy/                           # Deployment orchestration
│   │   ├── vm_manager.py                 # VM lifecycle
│   │   ├── vm_diff.py                    # VM change detection
│   │   ├── route_manager.py              # VPC route management
│   │   └── ssh_push.py                   # SSH deployment
│   └── release.sh                        # One-shot release helper (commit/tag/build/publish GitHub release)
│   ├── peer_parsers/                     # Vendor config parsers
│   │   ├── __init__.py
│   │   ├── gcp.py
│   │   ├── aws.py
│   │   ├── azure.py
│   │   └── cisco.py
│   └── systemd/                          # Systemd units/scripts
│       ├── nebius-vpngw-agent.service          # Agent service unit
│       ├── nebius-vpngw-health-monitor.service # Tunnel health monitor service unit
│       ├── nebius-vpngw-fix-routes.service     # Service wrapper for route cleanup
│       ├── nebius-vpngw-fix-routes.timer       # Timer to enforce route cleanup periodically
│       └── setup-vpngw-firewall.sh             # UFW firewall initialization script
```

### Key Modules

**Orchestrator (local):**

- `cli.py`: Command-line interface and workflow orchestration
- `config_loader.py`: YAML parsing, peer-config import/merging, env var expansion, schema validation
- `schema.py`: Pydantic models for strict validation with types and constraints
- `config_template.py`: Embedded YAML template, source of truth, always aligned with schema
- `build.py`: PyInstaller utilities for standalone binary builds

**Agent (on VM):**

- `main.py`: Daemon with idempotent config rendering and SIGHUP reload
- `frr_renderer.py`: Generates FRR BGP configuration with Active/Passive HA (local-preference inbound, MED outbound), prefix filtering, and route-maps
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration with XFRM interfaces
- `xfrm_manager.py`: Manages XFRM tunnel interface lifecycle (create, IP config, MTU)
- `routing_guard.py`: Enforces routing invariants, prevents local_prefix routes that break forwarding, cleans problematic routes
- `fix_routes.py`: Standalone utility invoked by systemd timer to periodically enforce routing invariants
- `firewall_manager.py`: Synchronizes UFW rules with active tunnels
- `tunnel_iterator.py`: Centralized tunnel enumeration for consistent indexing
- `state_store.py`: Persists last-applied state for idempotency
- `status_check.py`: Health checks for tunnels, BGP, and routes
- `sanity_check.py`: Routing validation troubleshooting tool
- `tunnel_health_monitor.py`: Automated tunnel health monitoring with 65s failure detection (immediate re-check), supports reactive/proactive modes

**Deployment:**

- `vm_manager.py`: VM lifecycle via Nebius SDK
- `ssh_push.py`: Package and config deployment over SSH/SFTP
- `route_manager.py`: VPC static route management (static mode only)

**Peer Parsers:**

- `gcp.py`, `aws.py`, `azure.py`, `cisco.py`: Vendor-specific config normalization

---

For detailed design, workflows, and troubleshooting, see [doc/design.md](doc/design.md).
