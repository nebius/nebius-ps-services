# Nebius VPN Gateway

Site-to-site IPsec/BGP VPN gateway for Nebius AI Cloud. Supports GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS, and custom peers.

This project is an open source, self-service, VM-based Nebius VPN gateway. It is not a managed Nebius VPN service.

## Table of Contents

- [Quick Start Guide](#quick-start-guide)
- [Security Notice](#security-notice)
- [Features](#features)
- [Installation (Detailed)](#installation-detailed)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Commands](#commands)
- [Routing Modes](#routing-modes)
- [BGP Configuration](#bgp-configuration)
- [Static Routing Configuration](#static-routing-configuration)
- [Peer Integration](#peer-integration)
- [VM Management](#vm-management)
- [System Monitoring](#system-monitoring)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Release & Versioning](#release--versioning)
- [Project Structure](#project-structure)

## Quick Start Guide

Use this sequence for first-time deployment. It is optimized for end users and keeps advanced topics in later sections.

### Prerequisites

- Nebius AI Cloud account and project
- A VPC where the VPN gateway subnet can be created
- Python 3.10-3.12 and `pipx`

### 1. Install the CLI from a release wheel

Download the latest wheel from [Releases](https://github.com/nebius/nebius-ps-services/releases) and install with `pipx`.

Example for `v0.4.9`:

```bash
wget https://github.com/nebius/nebius-ps-services/releases/download/nebius-vpngw-v0.4.9/nebius_vpngw-0.4.9-py3-none-any.whl
pipx install ./nebius_vpngw-0.4.9-py3-none-any.whl
```

Verify:

```bash
nebius-vpngw --version
nebius-vpngw --help
```

### 2. Create a starter config

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

This generates a full schema-aligned YAML template (including defaults, BGP/static examples, and HA roles).

### 3. Prepare Nebius network and reserve public IPs

Before peer tunnel setup, prepare the Nebius side and reserve public IPs for the gateway.

Workflow:

1. Fill minimal fields in `my-vpn.config.yaml`: `tenant_id`, `project_id`, `region_id`, `gateway_group` (leave `connections` for later).
   `project_id` must be set to a real value (or resolved via `${PROJECT_ID}` env var) before `prep-network`.
   Set `gateway_group.network_id` if you want a custom Nebius VPC instead of the auto-resolved `default-network`.
2. Run network preparation: `nebius-vpngw prep-network --local-config-file my-vpn.config.yaml`

3. Share the allocated Nebius public IP(s) with the peer network team.
4. The peer team creates their VPN gateway and points tunnels to those Nebius public IPs.
5. After you receive peer tunnel details, complete the config and apply.

### 4. Complete peer gateway/tunnel details in YAML

After peer-side creation, fill `connections` and `tunnels` (peer public IPs, PSKs, inner `/30` CIDRs, BGP ASN for BGP mode).

Generated template notes:

- `inner_cidr` must be APIPA `/30` (`169.254.0.0/16`)
- For multi-tunnel HA, use explicit roles (`ha_role: "active"` / `ha_role: "passive"`)
- Keep secrets as `${VAR}` placeholders and export env vars before `apply`

### 5. Apply the configuration

```bash
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

### 6. Configure local routes

```bash
nebius-vpngw add-routes-local --local-config-file my-vpn.config.yaml
nebius-vpngw list-routes-local --local-config-file my-vpn.config.yaml
```

### 7. Validate status and connectivity

```bash
nebius-vpngw status --local-config-file my-vpn.config.yaml
```

Optional data-plane check: from a VM in one of `gateway.local_prefixes`, test reachability to a remote private IP over the VPN.

### Firewall Requirements

Open any ports and protocols required by your application on your network so traffic from the Nebius side can reach the intended services.

For example, if you want to test connectivity with `ping`, create an ingress firewall rule for the `icmp` protocol that allows traffic from the source `local_prefixes` (the Nebius source subnets) to your network.

### 8. Optional: manual failover/failback (BGP active/passive)

```bash
nebius-vpngw failover --tunnel-failover tunnel-2 --local-config-file my-vpn.config.yaml
nebius-vpngw failback --tunnel-failback tunnel-1 --local-config-file my-vpn.config.yaml
```

This is useful for planned maintenance, peer changes, or operational testing.

For advanced setup, continue with [Configuration](#configuration), [Commands](#commands), and [Routing Modes](#routing-modes).

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
- **HA options:** Tunnel-level active/passive HA on a single gateway VM
- **Gateway groups:** Multiple independent gateway VMs with per-tunnel pinning; `gateway_group` is orchestration, not a clustered gateway service
- **Current limit:** Multi-VM HA for one routed prefix is not supported today

## Installation (Detailed)

### End users (pipx + GitHub release wheel)

- Requirements: Python 3.10–3.12 (e.g., `brew install python@3.12` on macOS, `sudo apt-get install python3.12 python3.12-venv` on Ubuntu).
- Install pipx (preferred via package manager to avoid PEP 668 errors):
  - macOS (Homebrew): `brew install pipx && pipx ensurepath`
  - Ubuntu/Debian: `sudo apt-get install pipx && pipx ensurepath`
  - If your distro has no pipx package: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
  - If pip blocks with "externally managed environment" (PEP 668), rerun with `--break-system-packages` only if you accept the risk:
    `python3 -m pip install --user pipx --break-system-packages && python3 -m pipx ensurepath`
- Download the latest `nebius_vpngw-<version>-py3-none-any.whl` from this repository’s GitHub Release assets (version comes from the Git tag).
  - macOS/Linux (wget):

    ```bash
    mkdir -p nebius-vpngw-release
    cd nebius-vpngw-release
    wget <release-wheel-url>
    ```

  - Windows:
    - Download the latest wheel from the GitHub Releases page and copy it into `nebius-vpngw-release`:
      - `https://github.com/nebius/nebius-ps-services/releases`
    - Create a folder and copy the file there.

- Install with pipx:

  ```bash
  pipx install ./nebius_vpngw-<version>-py3-none-any.whl
  ```

If pipx reports that its bin dir is not on PATH (e.g., `~/.local/bin`), run:

```bash
pipx ensurepath
# then restart your shell, or:
exec $SHELL
```

- Upgrade when a new tag is released (release wheels, not PyPI):

  ```bash
  pipx install --force ./nebius_vpngw-<version>-py3-none-any.whl
  ```

- Verify: `nebius-vpngw --version`.

### Developers (editable install)

- Create a virtual environment (Python 3.10–3.12) and activate it:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

- Install in editable mode with developer tools:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

- Confirm the CLI is reachable: `nebius-vpngw --help`. Developer extras include linting, tests, PyInstaller, and build tooling.

## Architecture

**Components:**

- **Orchestrator CLI:** Runs locally, manages VM lifecycle and config deployment
- **Gateway VM(s):** Ubuntu LTS with strongSwan (IPsec), FRR (BGP), agent daemon
- **Agent:** On-VM service that renders and applies configs idempotently

**Deployment modes:**

- Single VM: Multiple tunnels, VM is single point of failure
- Gateway group: Multiple independent VMs with per-tunnel pinning

**Current HA Boundary:**

- Active/passive HA is supported only at the tunnel level inside a single gateway VM
- Each gateway VM must keep exactly one active tunnel per connection
- If the same site/prefixes are made active on more than one gateway VM, you create multiple active paths for the same prefix and reintroduce the ECMP/asymmetric-routing problem described later in this document
- `gateway_group` is an orchestration grouping for provisioning and config distribution, not a clustered gateway service with shared control plane or shared dataplane ownership
- Multi-VM HA for one prefix is not supported in current releases

**Networking:**

- Dedicated gateway subnet for gateway isolation (default name: `vpngw-subnet`)
- One NIC per VM (platform constraint), future-ready for multi-NIC
- Public IP allocations preserved across VM recreation

For detailed architecture, see [design document](doc/design.md).

## Configuration

### File Structure

This is the template structure generated by:

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

```yaml
# Nebius VPN Gateway config (schema v1)
# Notes:
# - Override order: tunnel > connection > defaults
# - gateway.local_prefixes is the source of truth
# - Use ${VAR} for secrets; keep *.config.yaml out of git
# - Set values directly in YAML OR via ${VAR} envs (do not mix for the same field)

version: 1

# Project context (required)
tenant_id: "${TENANT_ID}"
project_id: "${PROJECT_ID}"
region_id: "${REGION_ID}"  # e.g., eu-north1

gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 1
  external_ips: []  # []=auto
  # Example (list per VM, inner list per NIC):
  # external_ips:
  #   - ["203.0.113.10"]  # VM0 NIC0
  #   - ["203.0.113.20"]  # VM1 NIC0
  # network_id: "vpcnetwork-abc123def456"
  subnet:
    name: "vpngw-subnet"
    cidr: null         # null=auto-carve from parent network private CIDRs; or set e.g. "172.16.30.0/24"
    prefix_length: 24  # used only when cidr is null; valid /28 through parent network CIDR

  vm_spec:
    platform: "cpu-d3"          # cpu-e2|cpu-d3
    preset: "4vcpu-16gb"
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 100
    disk_type: "network_ssd"
    disk_block_bytes: 4096
    num_nics: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
    ssh_private_key_path: "~/.ssh/id_ed25519"

gateway:
  local_asn: 65010
  local_prefixes:
    - "10.0.0.0/16"
  ipsec_mode: "xfrm-interface"
  quotas:
    max_connections: 16
    max_tunnels: 32
    max_total_bandwidth_mbps: null

defaults:
  vpn_type: "ipsec"
  ike_version: 2
  allow_ikev1: false
  auth:
    method: "psk"

  crypto:
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
      - "aes256-sha256-modp2048"
    ike_lifetime_seconds: 28800
    esp_proposals:
      - "aes256gcm16-modp2048"
      - "aes256-sha256-modp2048"
    esp_lifetime_seconds: 3600
    dh_groups:
      - 14
      - 19
      - 20

  dpd:
    interval_seconds: 5
    timeout_seconds: 15  # timeout > interval

  health_monitoring:
    enabled: true
    check_interval_seconds: 10
    max_failures_before_restart: 2
    proactive_refresh_enabled: false
    proactive_refresh_hours: 8
    ping_enabled: false

  ha_mode: "active-passive"  # one active tunnel per connection per VM

  routing:
    mode: "bgp"  # bgp|static
    bgp:
      router_id: null
      hold_time_seconds: 6
      keepalive_seconds: 2
      graceful_restart: false
      max_prefixes: 1000
      bfd:
        enabled: false  # enable only if peer supports BFD
        transmit_interval_ms: 300
        receive_interval_ms: 300
        detect_multiplier: 3

connections:
  - name: "gcp-ha-vpn"
    vendor: "gcp"
    routing_mode: "bgp"
    # remote_prefixes: ["10.0.0.0/8"]  # optional allowlist for BGP
    bgp:
      enabled: true
      remote_asn: 64514
      advertise_local_prefixes: true
    tunnels:
      - name: "gcp-ha-tunnel-1"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "active"  # exactly one active per connection per VM
        remote_public_ip: "203.0.113.1"
        psk: "${GCP_TUNNEL_1_PSK}"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: "gcp-ha-tunnel-2"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "passive"
        remote_public_ip: "203.0.113.2"
        psk: "${GCP_TUNNEL_2_PSK}"
        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"

  # Static routing example (remote_prefixes required when routing_mode=static)
  # - name: "onprem-static"
  #   vendor: "cisco"
  #   routing_mode: "static"
  #   remote_prefixes:
  #     - "192.168.0.0/16"
  #   bgp:
  #     enabled: false
  #   tunnels:
  #     - name: "onprem-tunnel-1"
  #       gateway_instance_index: 0
  #       remote_public_ip: "203.0.113.5"
  #       psk: "${ONPREM_PSK}"
  #       inner_cidr: "169.254.30.0/30"
  #       inner_local_ip: "169.254.30.1"
  #       inner_remote_ip: "169.254.30.2"
```

### Remote Prefixes: Static vs BGP Mode

**The `remote_prefixes` field has different semantics depending on routing mode:**

**BGP Mode (default):**

- `remote_prefixes` is **optional**
- If omitted: BGP learns ALL routes advertised by peer dynamically
- If specified: Acts as an inbound **whitelist filter** - only listed prefixes are accepted from BGP
- The route you want must be advertised by the peer
- `remote_prefixes` only allowlists advertised routes
- It does not carve a smaller route out of a larger one
- Routes are installed automatically by BGP, not manually
- Example: Peer advertises 300 networks → you don't need to list all 300 in YAML

```yaml
connections:
  - name: gcp-vpn
    vendor: gcp
    routing_mode: bgp
    # Optional: Whitelist specific prefixes (filter)
    # The peer must advertise these exact routes
    remote_prefixes:
      - "10.10.10.0/24"
      - "172.16.0.0/24"
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

Example: if the peer advertises `10.10.0.0/16` but you configure only `10.10.10.0/24`, the `/16` is rejected and the `/24` is only usable if the peer also advertises `10.10.10.0/24` explicitly.

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

If the target file already contains the current embedded template, rerunning the
same command is a no-op and exits successfully.

**Validate config:**

```bash
nebius-vpngw validate-config <file>
# Returns exit code 0 (valid) or 1 (invalid)
```

**Note:** `validate-config` takes the config file as a positional argument, not as `--local-config-file`. This is different from other commands which use the flag syntax.

**Prepare network and reserve public IPs:**

```bash
nebius-vpngw prep-network --local-config-file <file>
```

Ensures the dedicated gateway subnet and its route table exist.
Safe to rerun.

- `gateway_group.network_id` optionally pins deployment to a specific existing Nebius VPC network
- `gateway_group.subnet.name` defaults to `vpngw-subnet`
- `gateway_group.subnet.cidr` pins an exact private CIDR, including an extended RFC1918 range outside the default-network CIDR
- If `gateway_group.subnet.cidr` is omitted, the CLI auto-carves the first free subnet using `gateway_group.subnet.prefix_length`
- If an explicit CIDR is outside the current network pool, the CLI extends the network pool automatically when the target network has exactly one private pool

- If `gateway_group.external_ips` is empty, it reserves public IPs, prints them, and writes them into the YAML.
- If `gateway_group.external_ips` is set, it verifies those IPs and creates allocations for them if needed.
- If an IP was just released, it will wait briefly (up to ~10s) and retry before giving up.

**Generate from peer config (no deployment):**

```bash
nebius-vpngw create-from-peer-config my-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file branch-office.csv
```

Or use the output-path flag form:

```bash
nebius-vpngw create-from-peer-config \
  --peer-config-file gcp-peer.txt \
  --local-config-file my-vpn.config.yaml
```

If the generated output already matches the existing file, rerunning the command
is a no-op and exits successfully.

Supported peer input formats:

- `.txt`: free-form text, exported vendor configs, and config snippets
- `.csv`: one row per tunnel; rows with the same `connection_name` + `vendor` are grouped
- `.json`
- `.yaml` / `.yml`

### Deployment

**Deploy or update:**

```bash
nebius-vpngw apply --local-config-file <file>

# Force VM recreation
nebius-vpngw apply --local-config-file <file> --recreate-gw

# Override project/zone
nebius-vpngw apply --local-config-file <file> --project-id <id> --zone <zone>
```

Safe to rerun. Matching subnet, route table, VM, and allocation state is reused.

### Monitoring

**Check status:**

```bash
nebius-vpngw status --local-config-file <file>
```

Shows tunnel status (including active/passive role), carrying-traffic indicator, BGP sessions, service health, routing validation.

For multi-connection configs, `Carrying Traffic` is evaluated per connection, not once for the whole VM. If live BGP multipath is detected for the same prefix across different active connections, `status` prints an `ECMP Warning` panel that lists the overlapping prefix and the active tunnel names currently carrying it.

**Manage routes:**

```bash
# List local routes (Nebius VPC → Remote)
# Shows route tables for explicit-pool workload subnets selected by gateway.local_prefixes
# BGP advertised routes include tunnel role (active/passive)
nebius-vpngw list-routes-local --local-config-file <file>

# Add local routes (Nebius VPC → Remote)
# Safe to rerun: only missing routes are added
# - BGP mode: Queries BGP-learned routes from gateway VMs via FRR
# - Static mode: Uses remote_prefixes from YAML configuration
# - Creates VPC route table entries with gateway private IP as next-hop
# - Filters out local networks automatically
# - Targets only subnets with explicit private pools; inherited parent-network subnets are skipped for safety
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

# Manual failover to passive tunnel
# - If exactly two tunnels exist, passive is auto-selected
# - If more than two tunnels exist, specify the passive tunnel
nebius-vpngw failover --local-config-file <file>
nebius-vpngw failover --tunnel-failover gcp-ha-tunnel-2 --local-config-file <file>

# Manual failback to restore the active tunnel (does not disable passive)
# - If multiple active tunnels exist, specify the active tunnel
nebius-vpngw failback --local-config-file <file>
nebius-vpngw failback --tunnel-failback gcp-ha-tunnel-1 --local-config-file <file>
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

**What it checks:**

- IPsec state (CHILD_SA installed)
- BGP neighbor state (Established for BGP tunnels)
- XFRM interface error counters
- Optional ICMP ping to the BGP peer (`ping_enabled`)

**Configuration:**

Add to your `nebius-vpngw.config.yaml`:

```yaml
defaults:
  health_monitoring:
    enabled: true                          # Enable automated monitoring
    check_interval_seconds: 10             # Check every 10 seconds
    max_failures_before_restart: 2         # Restart after 2 consecutive failures
    proactive_refresh_enabled: false       # Reactive mode (detect & fix)
    proactive_refresh_hours: 8             # Unused (proactive mode disabled)
    ping_enabled: false                    # Enable only if peer allows ICMP to APIPA
```

**Monitoring Modes:**

| Mode                   | Behavior                                      | Downtime                  | Use Case                    |
|------------------------|-----------------------------------------------|---------------------------|-----------------------------|
| **Reactive (default)** | Detect failures, restart only when broken     | ~35s during failures      | 100% uptime priority        |
| **Proactive**          | Periodic restart every N hours (preventive)   | ~10-15s every N hours     | Prevent stale state buildup |

**Detection Timing:**

With `max_failures_before_restart: 2` and `check_interval_seconds: 10`:

1. **t=0s:** Normal operation
2. **t=10s:** First failure detected → Immediate re-check in 5 seconds
3. **t=15s:** Second failure confirmed → Tunnel restarted immediately
4. **t=35s:** Tunnel re-established, traffic flows

**Total detection time: ~15 seconds** (not 20s)
**Total recovery time: ~35 seconds** (detection + restart)

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
2. **DPD (5s checks, 15s timeout):** Detect IKE control plane failures
3. **Health Monitor (10s checks):** Detect data plane failures

This multi-layer approach ensures rapid detection and recovery from various failure modes.

## Routing Modes

### Active/Passive HA for Multi-Tunnel Connections

The gateway operates in **Active/Passive mode** to ensure symmetric routing without requiring workload VM configuration changes. When configuring multiple tunnels to the same peer (e.g., GCP HA VPN), **keep only one tunnel active** at a time.
`defaults.ha_mode` is required in the YAML config and must be set to `active-passive` in current releases.

**Tunnel Mode Configuration:**

| Desired Mode | Config Required | Description |
| ------------ | --------------- | ----------- |
| **active** | `ha_role: "active"` **OR** omit the field (default) | Primary tunnel with BGP local-preference 200. Carries all data traffic. |
| **passive** | `ha_role: "passive"` (**must be explicit**) | Standby tunnel with BGP local-preference 100. Hot standby for automatic failover. |
| **disable** | `ha_role: "disable"` (**must be explicit**) | Tunnel completely skipped (no IPsec, no BGP). |

**Important:** If you omit `ha_role` on multiple tunnels, they will all default to `"active"`, creating ECMP load balancing that may cause asymmetric routing and packet loss. Always explicitly set one tunnel to `"passive"` in multi-tunnel configurations.

**Scope boundary:** This active/passive model is enforced per connection per gateway VM. If you create two gateway VMs and make the same site's prefixes active on both of them, each VM still has its own active tunnel and you end up with two active paths for the same prefix. That is outside the supported design and can reintroduce the ECMP/asymmetric-routing problem. `gateway_group` does not make those VMs a clustered gateway service; it only groups them for orchestration. Multi-VM HA for one prefix is not supported today.

Multiple `connections` on the same gateway are supported for multi-site designs. Keep the Active/Passive rule inside each connection, and prefer distinct site prefixes per connection. If different active connections learn the same prefix, FRR can install multipath for that prefix and `status` will warn with the overlapping prefix and active tunnel names.

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

Customize defaults (baseline 3:1 ratio; enable BFD for sub-second detection):

```yaml
defaults:
  routing:
    bgp:
      hold_time_seconds: 6
      keepalive_seconds: 2
      graceful_restart: false
      bfd:
        enabled: false
        transmit_interval_ms: 300
        receive_interval_ms: 300
        detect_multiplier: 3
```

**BFD behavior:** If the peer does not support BFD, the BFD session stays down and BGP continues with normal timers. Enable BFD only when both sides support it.

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

### Supported Peer Inputs

- **Text (`.txt`)**: vendor exports, router snippets, and key/value documents
- **CSV (`.csv`)**: one row per tunnel; rows with the same `connection_name` + `vendor` are grouped
- **JSON**
- **YAML (`.yaml` / `.yml`)**

Vendor detection is best-effort and currently recognizes **GCP**, **AWS**, **Azure**, and **Cisco**. If nothing matches, the importer falls back to `vendor: generic`.

### Import Workflow

```bash
nebius-vpngw create-from-peer-config nebius-vpn.config.yaml \
  --peer-config-file gcp-peer.txt
```

If you omit both `CONFIG_FILE` and `--local-config-file`, the command writes `./nebius-vpngw.config.yaml`.
`--local-config-file` is accepted as an output-path alias on this command.

`create-from-peer-config` now builds `connections:` from parsed peer specs instead of reusing the template's fixed sample topology. The generated file is validated against the schema before it is written.

### Keyword Matching

The importer normalizes input keys and matches them against a keyword list. These aliases work across CSV headers, JSON/YAML keys, and `key: value` / `key = value` text.

| Target field | Accepted keywords |
| --- | --- |
| `connection.name` | `connection_name`, `vpn_name`, `peer_name`, `gateway_name`, `router_name`, `name` |
| `connection.vendor` | `vendor`, `provider`, `cloud`, `platform` |
| `connection.routing_mode` | `routing_mode`, `route_mode`, `routing_protocol`, `mode`, `protocol` |
| `connection.bgp.remote_asn` | `remote_asn`, `peer_asn`, `bgp_asn`, `neighbor_asn`, `cloud_router_asn`, `vgw_asn`, `asn` |
| `connection.remote_prefixes` | `remote_prefixes`, `remote_networks`, `destination_prefixes`, `destination_cidrs`, `routes`, `networks`, `subnets` |
| `tunnel.name` | `tunnel_name`, `vpn_tunnel_name`, `interface_name`, `interface`, `name` |
| `tunnel.remote_public_ip` | `remote_public_ip`, `peer_public_ip`, `remote_gateway_ip`, `vpn_gateway_ip`, `peer_ip`, `endpoint_ip`, `remote_ip`, `outside_ip` |
| `tunnel.psk` | `psk`, `pre_shared_key`, `shared_secret`, `shared_key`, `ipsec_shared_secret`, `secret` |
| `tunnel.inner_cidr` | `inner_cidr`, `inside_cidr`, `tunnel_cidr`, `link_cidr`, `vti_cidr`, `apipa_cidr`, `inside_ip_addresses` |
| `tunnel.inner_local_ip` | `inner_local_ip`, `local_inside_ip`, `customer_inside_ip`, `customer_gateway_inside_address`, `peer_ip_address`, `apipa_local` |
| `tunnel.inner_remote_ip` | `inner_remote_ip`, `remote_inside_ip`, `vpn_gateway_inside_address`, `cloud_inside_ip`, `bgp_peer_ip`, `ip_address`, `apipa_remote` |
| `tunnel.gateway_instance_index` | `gateway_instance_index`, `instance_index`, `vm_index` |
| `tunnel.local_public_ip_index` | `local_public_ip_index`, `public_ip_index`, `nic_index`, `interface_index` |
| `tunnel.ha_role` | `ha_role`, `role`, `state`, `active_standby_role` |

### Defaulting Rules

If a field cannot be matched, the importer keeps going and fills schema-safe defaults:

- `vendor`: `generic`
- `routing_mode`: `static` when remote prefixes exist, `bgp` when a remote ASN exists, otherwise `static` for `cisco/generic` and `bgp` for cloud vendors
- BGP `remote_asn`: `65014` when BGP is selected but no ASN is found
- Static `remote_prefixes`: `192.0.2.0/24`
- Tunnel defaults:
  - `gateway_instance_index: 0`
  - `local_public_ip_index: 0`
  - first tunnel `ha_role: active`, later tunnels `ha_role: passive`
  - generated APIPA `/30` ranges when inner addresses are missing
  - generated PSK placeholders such as `${GCP_HA_VPN_TUNNEL_1_PSK}`

Review the generated YAML before deployment, especially placeholder PSKs, remote prefixes, public IPs, and any inferred routing mode.

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

Equivalent flag form:

```bash
nebius-vpngw create-from-peer-config \
  --peer-config-file gcp-peer.txt \
  --local-config-file gcp-ha-vpn.config.yaml
```

**3. Generated BGP target shape (review and adjust values before deploy):**

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
      - name: nebius-204-12-170-147-tunnel-1
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: active
        remote_public_ip: "34.157.15.187"
        psk: "${GCP_HA_VPN_TUNNEL_1_PSK}"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: nebius-204-12-170-147-tunnel-2
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: passive
        remote_public_ip: "34.157.140.153"
        psk: "${GCP_HA_VPN_TUNNEL_2_PSK}"
        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"
```

### Example: Static Routing Import

If the input contains remote prefixes but no BGP ASN, the generator emits a static connection:

```yaml
connections:
  - name: onprem-static
    vendor: cisco
    routing_mode: static
    remote_prefixes:
      - "192.168.0.0/16"
    bgp:
      enabled: false
      remote_asn: null
      advertise_local_prefixes: false
    tunnels:
      - name: onprem-static-tunnel-1
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: active
        remote_public_ip: "203.0.113.5"
        psk: "${ONPREM_STATIC_TUNNEL_1_PSK}"
        inner_cidr: "169.254.30.0/30"
        inner_local_ip: "169.254.30.1"
        inner_remote_ip: "169.254.30.2"
```

**4. Validate and deploy:**

```bash
nebius-vpngw validate-config gcp-ha-vpn.config.yaml
nebius-vpngw apply --local-config-file gcp-ha-vpn.config.yaml
```

Peer import only fills what it can from the input. Any placeholder or inferred value should be treated as a review item, not as final deployment intent.

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
  # OR (future multi-NIC example)
  external_ips:
    - ["66.201.0.131", "66.201.0.132"]  # VM 0: NIC0, NIC1
    - ["66.201.0.133", "66.201.0.134"]  # VM 1: NIC0, NIC1
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
- Carrying traffic indicator (runtime active tunnel)
- BGP session state and route counts
- Service health (agent, strongSwan, FRR)
- Routing validation (table 220, APIPA routes, orphaned routes)

### Tunnel Status

Per-tunnel information:

- Gateway VM assignment
- Carrying traffic (runtime active tunnel)
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

- Versions are derived from annotated Git tags (`nebius-vpngw-vMAJOR.MINOR.PATCH`) via `setuptools-scm`; no manual edits to `pyproject.toml` are needed. The generated version is written to `src/nebius_vpngw/_version.py` during build and surfaced via `nebius-vpngw --version`.
- Semantic Versioning policy:
  - **MAJOR:** breaking changes (CLI flags removed/changed, behavior changes that could break scripts).
  - **MINOR:** backward-compatible features (new options, new Nebius resources supported).
  - **PATCH:** bug fixes only (no breaking behavior, no new major capability).
- Keep `CHANGELOG.md` updated before tagging; the changelog is the human-friendly record of what changed.
- If you build without a tag, `setuptools-scm` will fall back to `0.0.0`; create a proper `nebius-vpngw-vX.Y.Z` tag before shipping artifacts.

### Release model

- `publish-release.sh` is the local release helper for this service.
- `vpngw-ci.yml` is for PR validation and manual CI only; it does not run from `nebius-vpngw-v*` tags.
- `vpngw-release.yml` is the dedicated release workflow for this service and is triggered only by `nebius-vpngw-v*` tags.
- The release workflow checks out the tagged commit from `services/vpngw`, runs lint and tests, builds the wheel, verifies the wheel version matches the tag, and publishes the GitHub Release asset.

### Choosing the next SemVer

Bump **MAJOR** if there’s a breaking change (CLI flags or behavior changes that can break scripts).
Bump **MINOR** for backward-compatible features.
Bump **PATCH** for fixes only.
**Current working version (including dev distance):** `python -m setuptools_scm`

### How to create a release for this project

1. On your feature branch (or dedicated release-prep branch), run: `./publish-release.sh --prep X.Y.Z`
2. Open a PR from that branch and merge it to `main`.
3. After the PR is merged, switch to `main`, pull the merged commit, and run: `./publish-release.sh --publish X.Y.Z`
4. GitHub Actions workflow [`vpngw-release.yml`](/Users/rezab/repos/nebius-ps-services/.github/workflows/vpngw-release.yml) runs from that tag and publishes the GitHub Release.

Notes:

- `publish-release.sh --publish` only creates and pushes the annotated tag. It does not build or publish artifacts locally.
- `--publish` is intended to run only from a clean local `main` that is up to date with `origin/main`.
- `--prep` is idempotent. You can run it multiple times for the same tag; it keeps `## [Unreleased]` empty and merges any new Unreleased entries into the target tag section without duplication.
- The script accepts either `X.Y.Z` or `nebius-vpngw-vX.Y.Z`.

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
├── publish-release.sh                    # Release helper (prep changelog commit, then create/push release tag)
├── .github/workflows/
│   ├── vpngw-ci.yml                      # PR/manual CI for this service
│   └── vpngw-release.yml                 # Tag-driven GitHub Release workflow for nebius-vpngw-v*
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
│   ├── peer_parsers/                     # Keyword-based peer config importer
│   │   ├── __init__.py
│   │   ├── common.py
│   │   └── importer.py
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
- `tunnel_health_monitor.py`: Automated tunnel health monitoring with ~15s failure detection (immediate re-check), supports reactive/proactive modes

**Deployment:**

- `vm_manager.py`: VM lifecycle via Nebius SDK
- `ssh_push.py`: Package and config deployment over SSH/SFTP
- `route_manager.py`: VPC static route management (static mode only)

**Peer Parsers:**

- `importer.py`: Keyword-based peer config import for `.txt`, `.csv`, `.json`, `.yaml`, `.yml`
- `common.py`: Shared key normalization, vendor inference, and importer helpers

---

For detailed design, workflows, and troubleshooting, see [doc/design.md](doc/design.md).
