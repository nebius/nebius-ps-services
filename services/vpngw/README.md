# Nebius VPN Gateway (VM-Based)

**Version:** v0.3

VM-based site-to-site IPsec/BGP VPN gateway for Nebius AI Cloud. Supports GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS, and custom peers.

## Table of Contents

- [Security Notice](#security-notice)
- [Features](#features)
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

## Quick Start

### Prerequisites

- Nebius AI Cloud project with VPC network
- Python 3.11+ with Poetry
- Service account with Compute permissions

### Installation

```bash
cd /path/to/nebius-ps-services/services/vpngw
poetry install
```

### First Deployment

**1. Create configuration from template:**

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

**2. Edit configuration:**

```yaml
version: 1

gateway_group:
  project_id: ${NEBIUS_PROJECT_ID}
  zone: eu-north1-c
  instance_count: 1
  platform_id: standard-v3
  
gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
    
connections:
  - name: gcp-ha-vpn
    remote_public_ips:
      - "203.0.113.1"
    tunnels:
      - name: tunnel-1
        psk: ${GCP_TUNNEL_1_PSK}
```

**3. Set environment variables:**

```bash
export NEBIUS_PROJECT_ID="my-project-id"
export GCP_TUNNEL_1_PSK="your-pre-shared-key"
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

- Dedicated `vpngw-subnet` (/27 CIDR) for gateway isolation
- One NIC per VM (platform constraint), future-ready for multi-NIC
- Public IP allocations preserved across VM recreation

For detailed architecture, see [design document](doc/design.md).

## Configuration

### File Structure

```yaml
version: 1

gateway_group:
  project_id: ${PROJECT_ID}
  zone: eu-north1-c
  instance_count: 2
  platform_id: standard-v3
  cores: 4
  memory: 8
  disk_size: 30
  external_ips: []  # Auto-allocate

gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
    - "10.1.0.0/16"

defaults:
  crypto:
    ike_version: 2
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
    esp_proposals:
      - "aes256gcm16-modp2048"
  routing:
    mode: bgp
    advertise_local_prefixes: true
  dpd:
    delay: 30
    timeout: 120

connections:
  - name: peer-vpn
    remote_public_ips:
      - "203.0.113.1"
    remote_asn: 65001
    remote_prefixes:
      - "192.168.0.0/16"
    tunnels:
      - name: tunnel-1
        psk: ${TUNNEL_1_PSK}
        inner_local_ip: "169.254.10.1/30"
        inner_remote_ip: "169.254.10.2"
      - name: tunnel-2
        psk: ${TUNNEL_2_PSK}
        inner_local_ip: "169.254.10.5/30"
        inner_remote_ip: "169.254.10.6"
```

### Schema Validation

**Strict validation** enforces correctness before deployment:

- **Type safety:** IPs, CIDRs, ASNs, booleans validated
- **Constraints:** ASN 64512-65534, /30 subnets, APIPA 169.254.0.0/16
- **Consistency:** BGP mode requires `remote_asn`, tunnel IPs must be unique
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
project_id: ${NEBIUS_PROJECT_ID}
psk: ${TUNNEL_1_PSK}
remote_public_ips:
  - ${PEER_IP_1}
  - ${PEER_IP_2}
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

### Deployment

**Deploy or update:**

```bash
nebius-vpngw apply --local-config-file <file>

# With peer configs
nebius-vpngw apply \
  --local-config-file my-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file aws-peer.xml

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

**Manage routes (static mode):**

```bash
# List routes
nebius-vpngw list-routes --local-config-file <file>

# Add routes
nebius-vpngw add-routes --local-config-file <file>
```

## Routing Modes

### BGP (Recommended)

**Advantages:**

- Dynamic route learning
- Automatic failover
- Route filtering and policies
- Scales to large networks

**Requirements:**

- `remote_asn` must be configured
- Inner IPs must be /30 APIPA (169.254.0.0/16)
- Peer must support BGP

**Configuration:**

```yaml
defaults:
  routing:
    mode: bgp
    advertise_local_prefixes: true
    
gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"
    
connections:
  - name: peer
    remote_asn: 65001
    tunnels:
      - name: tunnel-1
        inner_local_ip: "169.254.10.1/30"
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

**Configuration:**

```yaml
defaults:
  routing:
    mode: static
    
connections:
  - name: peer
    remote_prefixes:
      - "192.168.0.0/16"
```

**Route management:**

```bash
# Add routes to VPC route table
nebius-vpngw add-routes --local-config-file <file>
```

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
    inner_local_ip: "169.254.10.1/30"
    inner_remote_ip: "169.254.10.2"
  - name: tunnel-2
    inner_local_ip: "169.254.10.5/30"
    inner_remote_ip: "169.254.10.6"
```

### BGP Timers

Customize per connection or tunnel:

```yaml
defaults:
  bgp:
    hold_time: 60
    keepalive_interval: 20
    graceful_restart: true
```

### BGP Troubleshooting

**Check BGP sessions:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Common issues:**

- **No OPEN messages:** IPsec tunnel not established or VTI interface down
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

### Per-Tunnel Overrides

Override global `local_prefixes` for specific tunnels:

```yaml
gateway:
  local_prefixes:
    - "10.0.0.0/16"
    - "10.1.0.0/16"
    
connections:
  - name: peer
    tunnels:
      - name: tunnel-1
        static_routes:
          local_prefixes:
            - "10.0.0.0/16"  # Only advertise this subnet
```

### VPC Route Management

For static mode, add routes to VPC route table:

```bash
nebius-vpngw add-routes --local-config-file <file>
```

Creates routes for `connection.remote_prefixes` pointing to gateway VMs.

## Peer Integration

### Supported Vendors

- **GCP HA VPN:** Cloud Router config exports
- **AWS Site-to-Site VPN:** Downloadable config files
- **Azure VPN Gateway:** Exported configurations
- **Cisco IOS:** IOS config snippets

### Import Workflow

```bash
nebius-vpngw apply \
  --local-config-file nebius-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file aws-peer.xml
```

**Merge behavior:**

- Fills only **missing** fields (PSKs, remote IPs, ASNs, inner IPs)
- Never overrides explicit YAML values
- Your topology is the source of truth

### Example: GCP HA VPN

**1. Export GCP Cloud Router config:**

```bash
gcloud compute routers describe my-router \
  --region us-central1 \
  --format yaml > gcp-peer.txt
```

**2. Create minimal Nebius config:**

```yaml
version: 1

gateway_group:
  project_id: ${NEBIUS_PROJECT_ID}
  zone: eu-north1-c
  instance_count: 1

gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"

connections:
  - name: gcp-ha-vpn
    tunnels:
      - name: tunnel-1
      - name: tunnel-2
```

**3. Deploy with peer config:**

```bash
nebius-vpngw apply \
  --local-config-file nebius-vpn.config.yaml \
  --peer-config-file gcp-peer.txt
```

Merger fills PSKs, remote IPs, remote ASN, and inner IPs from GCP config.

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
- VTI interfaces not filtered (internal encrypted traffic)

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

1. **ASN mismatch:** Verify `local_asn` and `remote_asn` match peer
2. **Inner IPs:** Ensure /30 APIPA subnets unique per tunnel
3. **IPsec down:** Fix tunnel before debugging BGP
4. **FRR version:** Upgrade to 10.x if routes not installing

### Routing Issues

**Check routing health:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Manual validation:**

```bash
ssh ubuntu@<gateway-ip>
sudo ip route show table 220  # Should be empty
sudo ip route | grep 169.254  # Should show /30 routes only
```

**Routing guard:**

Agent automatically:

- Removes table 220 policy routes
- Warns about broad APIPA routes
- Reports orphaned routes

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

## Development

### Agent Development

**Modify agent code:**

```bash
# Edit files in src/nebius_vpngw/agent/
vim src/nebius_vpngw/agent/main.py
```

**Rebuild and deploy:**

```bash
poetry build
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
[tool.poetry.dependencies]
pydantic = "^2.10.0"
```

**Rebuild:**

```bash
poetry lock
poetry build -f wheel
```

**Deploy:**

```bash
nebius-vpngw apply --local-config-file <file>
```

## Project Structure

```text
├── LICENSE
├── README.md
├── pyproject.toml
├── *.config.yaml                         # User configs (git-ignored)
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
│   │   ├── routing_guard.py              # Route validation
│   │   ├── firewall_manager.py           # UFW rule sync
│   │   ├── tunnel_iterator.py            # Tunnel enumeration
│   │   ├── state_store.py                # State persistence
│   │   ├── status_check.py               # Health checks
│   │   └── sanity_check.py               # Routing validation tool
│   ├── deploy/                           # Deployment orchestration
│   │   ├── vm_manager.py                 # VM lifecycle
│   │   ├── vm_diff.py                    # VM change detection
│   │   ├── route_manager.py              # VPC route management
│   │   └── ssh_push.py                   # SSH deployment
│   ├── peer_parsers/                     # Vendor config parsers
│   │   ├── __init__.py
│   │   ├── gcp.py
│   │   ├── aws.py
│   │   ├── azure.py
│   │   └── cisco.py
│   └── systemd/                          # Systemd units
│       └── nebius-vpngw-agent.service
```

### Key Modules

**Orchestrator (local):**

- `cli.py`: Command-line interface and workflow orchestration
- `config_loader.py`: YAML parsing, peer config merging, env var expansion, schema validation
- `schema.py`: Pydantic models for strict validation with types and constraints
- `config_template.py`: Embedded YAML template, source of truth, always aligned with schema
- `build.py`: PyInstaller utilities for standalone binary builds

**Agent (on VM):**

- `main.py`: Daemon with idempotent config rendering and SIGHUP reload
- `frr_renderer.py`: Generates FRR BGP configuration
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration
- `routing_guard.py`: Enforces routing invariants, removes problematic routes
- `firewall_manager.py`: Synchronizes UFW rules with active tunnels
- `state_store.py`: Persists last-applied state for idempotency

**Deployment:**

- `vm_manager.py`: VM lifecycle via Nebius SDK
- `ssh_push.py`: Package and config deployment over SSH/SFTP
- `route_manager.py`: VPC static route management (static mode only)

**Peer Parsers:**

- `gcp.py`, `aws.py`, `azure.py`, `cisco.py`: Vendor-specific config normalization

---

For detailed design, workflows, and troubleshooting, see [doc/design.md](doc/design.md).
