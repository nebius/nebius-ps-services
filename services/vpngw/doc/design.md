# Nebius VPN Gateway (VM-Based) — Design Document

> Version: v0.4
> Designed by: Reza Bahmanzadeh, Nebius Professional Services, CX Org.
> Copyright 2025 Nebius B.V.
> Licensed under the Apache License, Version 2.0

## Table of Contents

- [XFRM Mode Summary (current, required)](#xfrm-mode-summary-current-required)
- [Purpose & Scope](#purpose--scope)
- [Goals & Non-Goals](#goals--non-goals)
- [Architecture Overview](#architecture-overview)
- [Nebius Networking Model](#nebius-networking-model)
- [Configuration Model](#configuration-model)
- [Workflows & CLI](#workflows--cli)
- [Routing Modes & Local Prefixes](#routing-modes--local-prefixes)
- [IPsec Configuration](#ipsec-configuration)
- [BGP Configuration](#bgp-configuration)
- [Static Routes Configuration](#static-routes-configuration)
- [XFRM Routing Stack](#xfrm-routing-stack)
- [Security Hardening](#security-hardening)
- [Agent State Management](#agent-state-management)
- [Monitoring & Status](#monitoring--status)
- [Peer Config Import](#peer-config-import)
- [VM Management](#vm-management)
- [Development Workflow](#development-workflow)
- [Project Structure](#project-structure)
- [Tips & Troubleshooting](#tips--troubleshooting)

> Note: Legacy VTI support has been removed. XFRM interfaces are the only > supported mode going forward.

## XFRM Mode Summary (current, required)

- XFRM netdevices (`xfrm0`, `xfrm1`, …) bound via `if_id` in strongSwan; no marks or updown scripts.
- Traffic selectors: local side is scoped to the tunnel’s inner /30 plus `gateway.local_prefixes`; remote stays `0.0.0.0/0`. This keeps SSH/ping to the public IP off the tunnel while allowing any remote prefixes to traverse.
- Routing hygiene: table 220 is removed; the broad `169.254.0.0/16` DHCP route is removed while preserving metadata routes (`169.254.169.x`). Prevents policy routing and APIPA from stealing tunnel/management traffic.
- Sysctl: `rp_filter=0` on all/default/eth0 (required for XFRM), IP forwarding enabled, redirects off, ARP hardened.
- Firewall: UFW allows SSH from management CIDRs (or anywhere if not configured), IPsec (UDP 500/4500, ESP) from peer IPs, traffic from local VPC subnets for forwarding, ICMP for troubleshooting, and permits all traffic on tunnel interfaces (xfrm*). BGP (TCP 179) is reachable only over xfrm* between APIPA peers (169.254.x.x), which only exist after IPsec decryption; no TCP/179 on eth0. Everything else inbound on eth0 is denied.

> Public (eth0):   IKE / ESP only, SSH, ICMP
> Tunnel (xfrm*):  BGP (tcp/179), ICMP, routed traffic

- Interfaces must exist before IPsec brings up CHILD_SAs; agent creates XFRM devices and assigns inner IPs/routes before FRR reload.

## Purpose & Scope

Deliver a VM-based site-to-site VPN gateway for Nebius AI Cloud using IPsec (strongSwan) and routing (FRR for BGP, static as fallback). Provide a CLI orchestrator plus per-VM agent with idempotent configuration from a single YAML file, with optional peer-config import to generate that YAML. Support common cloud and on-premises peers (GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS).

## Goals & Non-Goals

**Goals:**

- IKEv2 (default) with IKEv1 fallback, PSK authentication
- Strong cryptography: AES-256, SHA-256/384/512, DH groups 14/20/24
- BGP routing (preferred) with static routing fallback
- Repeatable, idempotent deployments with minimal operator state
- Stable public IP preservation across VM recreation

**Non-goals:**

These features are not currently implemented but may be considered for future enhancements:

- **ECMP (Equal-Cost Multi-Path) in VPC route tables:**
  - *What it does:* Allows load balancing traffic across multiple gateway VMs for the same destination prefix
  - *Current limitation:* VPC routes point to a single next-hop (one gateway VM per route)
  - *Benefit:* Would enable automatic traffic distribution and higher aggregate throughput for high-bandwidth workloads
  - *Status:* Nebius VPC platform does not currently support ECMP routing

- **External NAT/Load balancing:**
  - *What it does:* Single public IP distributed across multiple gateway VMs for incoming VPN connections
  - *Current limitation:* Each gateway has its own public IP; peers must configure multiple tunnels
  - *Benefit:* Would simplify peer configuration and enable transparent gateway VM scaling
  - *Status:* Requires platform-level load balancer integration for IPsec traffic

- **Multi-NIC support:**
  - *What it does:* Multiple network interfaces per gateway VM for traffic separation (management, tunnel, internal)
  - *Current limitation:* Nebius platform currently limits VMs to 1 NIC with 1 public IP
  - *Benefit:* Would improve security isolation and enable dedicated high-throughput tunnel interfaces
  - *Status:* Configuration is future-ready (accepts `num_nics > 1`), awaiting platform support

## Architecture Overview

### Components

**Orchestrator CLI (`nebius-vpngw`):**

- Runs on operator laptop or CI/CD pipeline
- Reads YAML configuration; peer configs can be imported to generate YAML
- Manages VM lifecycle and IP allocations via Nebius SDK
- Pushes configuration to VMs over SSH
- Triggers agent reloads

**Gateway VM:**

- Ubuntu LTS with strongSwan, FRR, and Python
- Runs `nebius-vpngw-agent` systemd service
- Dedicated subnet (`vpngw-subnet`) for isolation

**Agent:**

- Single daemon per VM
- Renders strongSwan and FRR configurations
- Applies changes idempotently
- Persists state in `/etc/nebius-vpngw/last-applied.json`
- Reloads via SIGHUP

**Deployment Modes:**

- Single VM with multiple tunnels (active/active, VM is SPOF)
- Gateway group (N VMs) with per-tunnel pinning for VM-level HA

### Architecture Diagram

![HA VPN Topology](../image/ha-vpn-gcp-to-on-prem.svg)

**HA VPN gateway connecting to a peer VPN gateway with one external IP address.** The HA VPN gateway uses two tunnels, both connecting to the single external IP address on the peer VPN gateway.

*Diagram courtesy of Google Cloud. Source: [VPN Topologies](https://cloud.google.com/network-connectivity/docs/vpn/concepts/topologies)*

## Nebius Networking Model

### VPC and Subnets

- One VPC network selected via `network_id` (optional; see resolution logic below)
- Dedicated `vpngw-subnet` (/24 CIDR) created automatically if missing
- Dedicated route table (`vpngw-subnet-routing-table`) with default egress route
- Workload subnets remain separate for security isolation

**Network Resolution Logic:**

When `network_id` is not specified in the YAML config, the system auto-discovers the network using this priority order:

1. **Default network:** Looks for a network named `default-network` in the project
2. **Single custom network:** If no default network exists and exactly ONE custom network is found, uses that network
3. **Multiple networks:** If multiple custom networks exist (rare scenario), the deployment **fails** with an error asking the user to explicitly specify `network_id` in the YAML

This intelligent resolution handles the common case (default network or single VPC) while preventing ambiguity when multiple networks exist.

**Platform Constraint:** Currently 1 NIC per VM with 1 public IP. All tunnels share the same IP, differentiated by IKE/IPsec identifiers.

**Future-ready:** Configuration accepts `num_nics > 1` for when platform supports multi-NIC.

### Dedicated Subnet Rationale

- **Security isolation:** Limits blast radius of firewall misconfigurations
- **Routing clarity:** Simplifies HA failover and prevents asymmetric routing
- **IP hygiene:** Controlled CIDR for gateway infrastructure, separate from workloads
- **Policy separation:** Distinct egress controls without affecting application subnets
- **Operational safety:** Safer VM recreations with reduced ARP/ND noise
- **Capacity:** Orchestrator auto-creates `vpngw-subnet` as the first free /24 carved from the target VPC’s private pool. If no /24 is available, deployment fails with guidance to add more IP space. This supports multi-VM gateway groups.

### Public IP Allocations

Configuration shape: `external_ips[instance_index][nic_index]` → IP string (flat lists are not supported)

**Behavior:**

- Omitted/empty: Auto-create IP allocations
- Provided: Use existing allocations
- Insufficient: Create missing allocations
- Auto naming: `{instance}-eth{N}-ip`

**Preservation:** Allocations are kept and reattached during VM recreation. No downtime for IP addresses, only for tunnel establishment.

**Subnet constraint:** Nebius does not allow changing `subnet_id` on an existing public allocation. If you supply `external_ips` and the found allocation belongs to a different subnet than the target gateway subnet, we fail fast with guidance:

- Deploy in the original subnet/network so the allocation matches, **or**
- Remove the IP from `external_ips` to get a new allocation in the gateway subnet, **or**
- (Best effort) Manually release the old allocation and let the deployer request the same IP in the new subnet. If the pool allows it and the address is still free, it is reclaimed; otherwise the request fails or yields a different IP.

**Examples:**

```yaml
# Single VM, single NIC (auto-allocate)
external_ips: []

# Single VM, existing IP
external_ips: [["203.0.113.10"]]

# Two VMs, existing IPs
external_ips: [["203.0.113.10"], ["203.0.113.20"]]
```

## Configuration Model

### YAML Structure

Single file `*.config.yaml` with four main sections:

1. **gateway_group:** VM infrastructure (instance count, specs, networking, IPs)
2. **gateway:** Routing identity (ASN, local prefixes, quotas)
3. **defaults:** Global VPN behavior (crypto, DPD, BGP settings)
4. **connections:** Peer gateways with tunnel definitions

### Merge Precedence

Tunnel settings override connection settings, which override peer-config, which override defaults.

### Environment Variables

Use `${VAR}` placeholders for secrets and environment-specific values. Missing variables are reported together before deployment.

### Template Generation

**Embedded template** in `config_template.py` is the source of truth, always aligned with schema:

```bash
# Create new config from embedded template
nebius-vpngw create-config my-vpn.config.yaml
```

Template includes comprehensive comments and examples. Files with `.config.yaml` extension are automatically git-ignored for security.

### Schema Validation

**Strict Pydantic-based validation** enforces configuration correctness:

**Features:**

- Rejects unknown fields (catches typos like `inner_ciddr`)
- Validates types (IPs, CIDRs, numbers, booleans)
- Enforces constraints (ASN ranges 64512-65534, /30 subnets, APIPA ranges)
- Checks logical consistency (BGP mode requires `bgp.remote_asn`)
- Verifies resource quotas

**API Versioning:**

- `version: 1` field required in all configs
- Future schema changes increment version number
- Backwards compatibility maintained

**CLI Integration:**

```bash
# Validate before deployment
nebius-vpngw validate-config my-vpn.config.yaml

# Validation runs automatically during apply
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

**Note:** The `validate-config` command takes the config file as a positional argument, not as `--local-config-file`. This is different from other commands like `apply` which use the flag syntax.

**Implementation:**

- Schema: `src/nebius_vpngw/schema.py` (Pydantic models)
- Validation: `src/nebius_vpngw/config_loader.py` (after env expansion)
- CLI command: `src/nebius_vpngw/cli.py` (`validate_config()`)

## Workflows & CLI

### Commands

**Configuration Creation:**

```bash
nebius-vpngw create-config <config-file>
```

Creates new configuration file from embedded template with comprehensive comments. Warns if filename doesn't end with `.config.yaml` (security best practice). Use `--force` to overwrite existing files.

**Configuration Validation:**

```bash
nebius-vpngw validate-config <config-file>
```

Validates configuration against schema without deployment. Performs full validation including types, constraints, and logical consistency. Returns exit code 0 (valid) or 1 (invalid). Use before deployment to catch errors early.

**Deployment:**

```bash
nebius-vpngw apply --local-config-file <file>
```

Deploy or update gateway. Automatically validates schema before deployment. Typical flow: parse args → load YAML → validate schema → ensure network/subnet → ensure VMs + allocations → push config via SSH → reload agent → reconcile routes (static mode).

Flags: `--recreate-gw`, `--project-id`, `--zone`

**Peer Config Import (generate YAML only):**

```bash
nebius-vpngw create-from-peer-config <output-config-file> \
  --peer-config-file ./gcp-ha-vpn.txt \
  --peer-config-file ./aws-vpn.xml
```

Creates a new YAML config by merging vendor peer configs into the embedded template.
No deployment is performed; review and validate before running `apply`.

**Status & Monitoring:**

```bash
nebius-vpngw status --local-config-file <file>
nebius-vpngw list-routes-local --local-config-file <file>
nebius-vpngw list-routes-remote --local-config-file <file>
nebius-vpngw add-routes-local --local-config-file <file>
```

**Default Behavior:**

- With config present: shows status
- No config: creates template from embedded source

### Peer Import & Merging

Vendor parsers (GCP/AWS/Azure/Cisco) normalize peer templates. Peer import overlays parsed values onto the template while keeping topology intact. Peer values replace template defaults when present; missing fields remain for manual review.

## Routing Modes & Local Prefixes

### Modes

- **BGP (preferred):** Dynamic routing with FRR, automatic route learning
- **Static:** Manual route configuration, simpler but less flexible

Global default under `defaults.routing.mode`; override per connection/tunnel.

### Local Prefixes

`gateway.local_prefixes` is the **single source of truth** for Nebius-side networks.

**BGP mode:** Advertised to peers when `advertise_local_prefixes: true`

**Static mode:** Used for VPC route management and included in leftsubnet selectors

### Remote Prefixes

`connection.remote_prefixes` has different semantics depending on routing mode:

| Routing Mode | remote_prefixes Usage |
| ------------ | --------------------- |
| **BGP** | Optional - acts as inbound filter/whitelist. If omitted, accepts all BGP routes. |
| **Static** | Required - used for kernel route installation via XFRM interfaces. |

**BGP mode:**

- **Optional** - BGP learns routes dynamically from peer
- If specified: Acts as **inbound filter** (prefix-list) - only listed prefixes accepted
- If omitted: All routes advertised by peer are accepted
- Routes installed automatically by FRR BGP daemon
- No manual enumeration needed for 100+ remote networks

**Static mode:**

- **Required** (or in `tunnel.static_routes.remote_prefixes`)
- Used for kernel route installation via XFRM interfaces (rightsubnet stays 0.0.0.0/0)
- Each remote network must be explicitly listed
- No dynamic learning

## IPsec Configuration

### strongSwan

- Route-based VPN using XFRM interfaces (default)
- IKEv2 default, IKEv1 fallback configurable
- PSK authentication
- DPD (Dead Peer Detection) for tunnel liveness

### IPsec Interface Modes

The gateway supports two interface modes for IPsec tunnels:

#### XFRM Interface Mode (Default, Recommended)

Modern kernel XFRM netdevs bound to strongSwan CHILD_SAs via `if_id`:

- Creates `xfrm0`, `xfrm1`, etc. interfaces
- Each tunnel bound via `if_id_in/if_id_out` parameters (e.g., 100, 101)
- No marks or updown scripts required
- **Eliminates packet duplication** issue with 0.0.0.0/0 traffic selectors
- BGP sessions run over XFRM interfaces using APIPA inner IPs
- Cleaner architecture, better performance

**Configuration:**

```yaml
gateway:
  local_asn: 65010
  local_prefixes:
    - "10.0.0.0/16"
  ipsec_mode: xfrm-interface  # Default (can omit)
```

**XFRM Setup:**

- strongSwan config uses `if_id_in=100, if_id_out=100` (no marks)
- Agent creates XFRM devices: `ip link add xfrm0 type xfrm dev eth0 if_id 100`
- Inner APIPA addresses assigned to XFRM interfaces for BGP peering
- MTU set to 1387 (GCP MTU 1460 - IPsec overhead 73 bytes)

**Why XFRM:**

- Modern Linux kernel interface (5.4+)
- No packet duplication with 0.0.0.0/0 traffic selectors
- Cleaner separation: if_id binding vs mark-based routing
- Better performance and maintainability

#### VTI Mode (Removed)

Legacy Virtual Tunnel Interface support has been removed due to packet duplication issues and operational complexity. All deployments must use XFRM interfaces.

### Route-Based VPN Architecture

#### IPsec Traffic Selectors vs Routing

`leftsubnet` and `rightsubnet` in strongSwan define **only the IPsec Traffic Selectors (TS)** exchanged during IKE negotiation. They do **NOT install routes** and do **NOT control which networks are routed through the tunnel**.

**For route-based VPN (XFRM), use:**

```text
leftsubnet=<inner /30 + gateway.local_prefixes>
rightsubnet=0.0.0.0/0
```

leftsubnet is a comma-separated list of the tunnel inner /30 plus `gateway.local_prefixes`.

This configuration:

- Allows any remote prefix for the configured local selectors (inner /30 + `gateway.local_prefixes`)
- Permits the tunnel to carry:
  - BGP APIPA traffic (169.254.x.x)
  - All dynamically learned remote prefixes
  - All local prefixes advertised through BGP
  - Any number of enterprise networks (scalable)
- Does NOT create policy-based routing restrictions
- Eliminates the need for hundreds of traffic selectors

**Routing is controlled exclusively by:**

1. **Linux routing table:** `ip route add <prefix> dev xfrm0`
2. **BGP daemon (FRR):** Learned routes installed dynamically
3. **Static routes:** Manual kernel routes (in static mode)
4. **XFRM interfaces:** Bound via `if_id` from strongSwan

**What determines which traffic enters the tunnel:**

- **The routing table** → what prefixes point to the XFRM interface
- **BGP daemon** → which routes FRR installs dynamically
- **NOT** leftsubnet/rightsubnet → these are IPsec selector allow-lists, not route selection

**Why this matters:**

- **BGP scalability:** No need to reconfigure IPsec when remote networks change
- **Dynamic routing:** BGP can learn/install 100+ prefixes without IPsec restarts
- **APIPA support:** BGP peering IPs (169.254.x.x) work seamlessly
- **Simplified config:** No tunnel-level prefix enumeration required
- **Peer compatibility:** GCP HA VPN and AWS VPN require 0.0.0.0/0 selectors

**Encryption decision:**

- strongSwan binds CHILD_SAs to XFRM interfaces via `if_id`
- `ip link add xfrm0 type xfrm dev eth0 if_id <id>`
- Any packet routed through xfrmX gets encrypted by strongSwan
- No policy database (SPD) restrictions on prefixes

### local_prefixes vs remote_prefixes

The configuration fields `local_prefixes` and `remote_prefixes` have different meanings depending on the VPN mode:

| Mode   | local_prefixes → Remote Peer                               | remote_prefixes → Nebius VM                             |
|--------|------------------------------------------------------------|---------------------------------------------------------|
| BGP    | Advertised by FRR to peer via `network` statements         | Learned dynamically from peer BGP; no YAML required     |
| Static | Installed as static routes to XFRM interfaces + VPC routes | Installed as static kernel routes to XFRM interfaces    |

**BGP Mode:**

- `local_prefixes`: Networks advertised to the remote peer via BGP `network` statements in FRR
- `remote_prefixes`: Optional filter list; actual routes learned dynamically from BGP peer
- FRR installs all learned routes automatically

**Static Mode:**

- `local_prefixes`: Networks reachable via Nebius VPC; installed as kernel routes and VPC routes
- `remote_prefixes`: Networks behind the remote peer; installed as kernel routes to XFRM interfaces
- Agent installs routes explicitly: `ip route replace <prefix> dev xfrm0`

**Key Differences:**

- BGP: Remote prefixes are **dynamic** (no YAML config needed)
- Static: Remote prefixes must be **explicitly configured** in YAML
- Both modes: rightsubnet is 0.0.0.0/0; leftsubnet includes the inner /30 plus `gateway.local_prefixes`

### Crypto Proposals

**IKE (Phase 1):**

- `aes256gcm16-prfsha256-modp2048` (modern AEAD)
- `aes256-sha256-modp2048` (compatible)
- `aes256-sha1-modp1024` (legacy fallback)

**ESP (Phase 2):**

- `aes256gcm16-modp2048` (modern AEAD)
- `aes256-sha256-modp2048` (compatible)
- `aes256-sha1-modp1024` (legacy fallback)

## BGP Configuration

### FRR Setup

- `bgpd` daemon for BGP routing
- Runs over XFRM interfaces using APIPA inner IPs
- Configurable timers: hold time (60s), keepalive (20s)
- Graceful restart enabled by default

### APIPA Inner IPs

- Must use /30 subnet in 169.254.0.0/16 range
- Example: `169.254.10.0/30` → usable IPs are `.1` and `.2`
- `.0` is network address, `.3` is broadcast (unusable)
- Each tunnel requires unique /30 subnet

### BGP Session Requirements

1. IPsec tunnels must be ESTABLISHED first
2. XFRM interfaces must be up with assigned inner IPs
3. BGP peer must be reachable via inner_remote_ip
4. ASN configuration must match on both sides
5. FRR 10.x recommended (8.4.4 has route installation bugs)

### Common BGP Issues

- **No OPEN messages:** IPsec tunnel not established or XFRM interface down
- **OPEN errors:** ASN mismatch between peers
- **Routes not installed:** FRR 8.4.4 bug, upgrade to 10.x
- **Policy errors:** Add `no bgp ebgp-requires-policy` to config

## Static Routes Configuration

### VPC Route Management

Three route management commands with distinct purposes:

**1. Add local routes (Nebius VPC → Remote):**

```bash
nebius-vpngw add-routes-local --local-config-file <file>
```

Creates VPC route table entries for remote networks pointing to gateway VMs.

**Implementation Details:**

- **BGP mode**: Queries BGP-learned routes from gateway VMs via SSH (`vtysh -c 'show bgp ipv4 unicast json'`)
  - Filters by `remote_prefixes` whitelist if configured
  - Filters out locally originated routes (next-hop 0.0.0.0)
  - Filters out overlapping local networks (from `gateway.local_prefixes`)
- **Static mode**: Uses `remote_prefixes` from YAML configuration
- Finds subnets matching `gateway.local_prefixes`
- Resolves gateway VM private IP allocation via Compute API
- Creates/reuses custom route tables for matching subnets
  - If subnet uses default route table: Creates custom RT and copies existing routes
  - Warns user about route table separation
- Creates route entries: destination = remote prefix, next-hop = gateway private IP
- Implements idempotency (skips existing routes)

**2. List local routes (Nebius VPC → Remote):**

```bash
nebius-vpngw list-routes-local --local-config-file <file>
```

Lists VPC route table entries for subnets matching `gateway.local_prefixes`.

**Implementation Details:**

- Queries VPC API for subnets matching `gateway.local_prefixes`
- Displays route table ID and routes for each subnet
- Shows destination CIDR and next-hop (resolves allocation IDs to IP addresses)
- Uses Rich tables for formatted output

**3. List remote routes (Remote → Nebius):**

```bash
nebius-vpngw list-routes-remote --local-config-file <file>
```

Lists routes on gateway VMs that direct traffic from remote sites to Nebius networks.

**Implementation Details:**

- **BGP mode**:
  - SSHs to gateway VMs and queries FRR: `vtysh -c 'show bgp ipv4 unicast json'`
  - Extracts routes with next-hop IPs, AS paths, and status
  - Queries `ip route get <next-hop>` to determine outgoing XFRM interface
  - Filters out locally originated routes (next-hop 0.0.0.0)
  - Checks against `remote_prefixes` whitelist (shows allowed/not-allowed status)
  - Displays: Prefix, Next-Hop, Via (XFRM interface), AS Path, Status
- **Static mode**:
  - Agent installs kernel routes: `ip route replace <prefix> dev xfrmX` for each `remote_prefixes`
  - Compares YAML `remote_prefixes` with kernel routing table via `ip route show`
  - Shows installation status (installed/missing)
  - Routes installed automatically by strongSwan renderer after tunnel establishment

**Static Mode Route Installation:**

In static mode, the agent automatically installs kernel routes for all `remote_prefixes`:

```bash
# Example: For remote_prefixes: ["10.10.0.0/24", "10.11.0.0/16"]
ip route replace 10.10.0.0/24 dev xfrm0
ip route replace 10.11.0.0/16 dev xfrm0
```

This happens in `strongswan_renderer.py` after tunnel establishment.

**BGP vs Static Routing:**

| Aspect | BGP Mode | Static Mode |
| ------ | -------- | ----------- |
| **Traffic Selectors** | `0.0.0.0/0` (both sides) | `0.0.0.0/0` (both sides) |
| **Kernel Routes** | Installed by FRR BGP | Installed by agent from YAML |
| **remote_prefixes** | Optional filter/whitelist | Required, installed as routes |
| **Dynamic Learning** | Yes (via BGP) | No (manual YAML updates) |
| **Scalability** | 100+ networks, no config changes | Must enumerate each network |

## XFRM Routing Stack

### Architecture Overview

The VPN gateway uses a **multi-layer defense** strategy to ensure routing stability for XFRM (IPsec) tunnels:

1. **Dedicated Sysctl Configuration** (`/etc/sysctl.d/99-zzz-vpngw.conf`)
2. **Systemd Service Ordering** (UFW → strongSwan → FRR → agent)
3. **Self-Healing Routing Guard** (automatic sysctl enforcement)

This design **decouples routing correctness from UFW status**, making the gateway resilient to service failures and configuration changes.

### Critical Sysctl Settings

File: `/etc/sysctl.d/99-zzz-vpngw.conf`

```bash
# 1. IP Forwarding - Gateway must route packets
net.ipv4.ip_forward = 1

# 2. Reverse Path Filtering - MUST BE DISABLED
# XFRM tunnels create asymmetric routing that strict rp_filter blocks
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
net.ipv4.conf.eth0.rp_filter = 0
net.ipv4.conf.lo.rp_filter = 0

# 3. ICMP Redirects - Disabled (cloud fabric shouldn't redirect)
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# 4. Source Routing - Disabled for security
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# 5. Martian Logging - Enabled for debugging
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1

# 6. IPv6 Hygiene
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
```

**Why `99-zzz-vpngw.conf`?**

- The `zzz` prefix ensures it loads **after** `/etc/sysctl.conf` (via `99-sysctl.conf` symlink)
- This prevents the default Ubuntu sysctl settings from overriding our XFRM-specific values
- Cloud-init also comments out conflicting lines in `/etc/sysctl.conf`

### Systemd Service Ordering

**Boot Sequence:**

```text
network-online.target
    ↓
cloud-init.service
    ↓
sysctl --system (loads 99-zzz-vpngw.conf)
    ↓
ufw.service (firewall)
    ↓
strongswan.service (IPsec)
    ↓
frr.service (BGP)
    ↓
nebius-vpngw-agent.service (routing guard + agent)
```

**Configuration Files:**

Each service has a systemd override in `/etc/systemd/system/<service>.d/override.conf`:

```ini
# /etc/systemd/system/ufw.service.d/override.conf
[Unit]
After=network-online.target cloud-init.service
Wants=network-online.target

# /etc/systemd/system/strongswan.service.d/override.conf
[Unit]
After=ufw.service network-online.target
Wants=ufw.service

# /etc/systemd/system/frr.service.d/override.conf
[Unit]
After=strongswan.service
Wants=strongswan.service

# /etc/systemd/system/nebius-vpngw-agent.service.d/override.conf
[Unit]
After=strongswan.service frr.service
Wants=strongswan.service frr.service
```

**Why This Ordering Matters:**

- **UFW after cloud-init**: Prevents cloud-init network changes from racing with UFW
- **strongSwan after UFW**: Ensures netfilter framework is initialized before IPsec tunnels
- **FRR after strongSwan**: BGP needs XFRM interfaces created by strongSwan
- **Agent after FRR**: Routing guard validates routes installed by FRR

### Self-Healing Routing Guard

The `routing_guard.py` module enforces routing invariants on **every agent startup/reload**:

#### INVARIANT 0: Sysctl Enforcement

```python
# Automatically fixes sysctls if they get reset
net.ipv4.ip_forward = 1 (if currently 0)
net.ipv4.conf.all.rp_filter = 0 (if currently 1 or 2)
net.ipv4.conf.xfrm*.rp_filter = 0 (all XFRM interfaces)
```

#### INVARIANT 1: No Policy Routing

- Removes table 220 rules (cloud platforms sometimes add these)
- Flushes table 220 routes

#### INVARIANT 2: No Broad APIPA Routes

- Removes `169.254.0.0/16` route if present
- Keeps metadata-specific routes (`169.254.169.0/24`)

#### INVARIANT 3: No Scope Link Routes

- Removes `scope link` routes for local prefixes
- These mark prefixes as "directly connected" which breaks forwarding

#### INVARIANT 4: Clean Orphaned Routes

- Removes APIPA routes not defined in config
- Prevents leftover routes from old tunnels

#### INVARIANT 5: BGP Peer Routes

- Ensures `/32` routes for BGP peers via XFRM interfaces
- Required for correct source IP selection

**Logs Example:**

```text
[RoutingGuard] ✓ All invariants OK. BGP peer routes: 2
[RoutingGuard] Fixed 2 sysctls: net.ipv4.ip_forward, net.ipv4.conf.all.rp_filter
```

### Verification Commands

**Check Sysctl Settings:**

```bash
sysctl net.ipv4.ip_forward  # Must be 1
sysctl net.ipv4.conf.all.rp_filter  # Must be 0
sysctl net.ipv4.conf.eth0.rp_filter  # Must be 0
```

**Check Service Order:**

```bash
systemctl list-dependencies nebius-vpngw-agent.service | grep -E "ufw|strongswan|frr"
```

**Check Routing Guard Logs:**

```bash
sudo journalctl -u nebius-vpngw-agent -n 50 | grep RoutingGuard
```

**Check for Problematic Routes:**

```bash
# Should NOT exist:
ip route show table 220  # Empty
ip route show 169.254.0.0/16  # Empty or metadata-specific
ip rule show | grep 220  # Empty

# Should exist:
ip route show 169.254.169.0/24  # Metadata service OK
```

### Troubleshooting

#### Symptom: VMs can't reach remote networks

1. Check sysctl settings:

   ```bash
   sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter
   ```

   - If `ip_forward=0` or `rp_filter≠0`, routing won't work

2. Check UFW status:

   ```bash
   sudo ufw status
   ```

   - Must show `Status: active`

3. Check service ordering:

   ```bash
   systemctl status ufw strongswan frr nebius-vpngw-agent
   ```

   - All should be `active (running)` or `active (exited)` for UFW

4. Restart agent to enforce invariants:

   ```bash
   sudo systemctl restart nebius-vpngw-agent
   sudo journalctl -u nebius-vpngw-agent -n 30 | grep -E "RoutingGuard|sysctl"
   ```

#### Symptom: Sysctls reset after reboot

- Check `/etc/sysctl.d/99-zzz-vpngw.conf` exists and has correct settings
- Check `/etc/sysctl.conf` doesn't have conflicting `ip_forward` or `rp_filter` (should be commented)
- Run `sudo sysctl --system` to reload all sysctl files

#### Symptom: Services start in wrong order

- Check systemd overrides exist:

  ```bash
  ls -la /etc/systemd/system/{ufw,strongswan,frr,nebius-vpngw-agent}.service.d/
  ```

- Run `sudo systemctl daemon-reload` after creating overrides

## Security Hardening

### Applied via cloud-init at VM Creation

- SSH key-only authentication, root login disabled
- Fail2ban for SSH intrusion prevention
- UFW firewall (allows IPsec UDP 500/4500, ESP)
- auditd for command and config file monitoring
- Automated security updates (unattended-upgrades)
- IP forwarding enabled, ICMP redirects disabled

### CRITICAL: UFW Must Be Active

**UFW (Uncomplicated Firewall) MUST be active and enabled for the VPN gateway to function correctly.**

**Why UFW is Required:**

1. **Netfilter Framework Initialization**: UFW activates the Linux netfilter framework, which is essential for proper packet forwarding through XFRM (IPsec) tunnels.

2. **VPC Fabric Integration**: Without UFW active, packets from the Nebius VPC fabric may not be correctly routed through the VPN gateway to remote networks, even with `net.ipv4.ip_forward=1` enabled.

3. **XFRM Tunnel Forwarding**: UFW's FORWARD chain rules are necessary for the kernel to properly handle packets destined for XFRM interfaces (xfrm0, xfrm1, etc.).

**Verification After Deployment:**

```bash
# Check UFW is active (REQUIRED)
sudo ufw status verbose

# Should show: Status: active
# If inactive, the VPN gateway will NOT forward traffic correctly
```

**Symptoms of Inactive UFW:**

- VMs in local subnets cannot reach remote networks via VPN
- Packets never reach the gateway VM (zero iptables counters)
- XFRM encryption counters don't increment
- BGP and IPsec tunnels work, but data plane fails

**Recovery if UFW is Inactive:**

```bash
# Re-apply firewall config via agent (preferred)
sudo systemctl restart nebius-vpngw-agent

# If UFW was disabled manually:
sudo ufw enable
```

### Firewall Management

**Default Firewall:** UFW (Uncomplicated Firewall) is the default and required firewall solution.

**Automatic Configuration:** The gateway VM automatically configures and enables UFW during deployment via cloud-init with the following rules:

**Required Ports:**

- **UDP 500** - IKE (Internet Key Exchange) for IPsec tunnel establishment
- **UDP 4500** - IPsec NAT-T (NAT Traversal) for ESP over UDP when behind NAT
- **ESP (IP Protocol 50)** - Encapsulating Security Payload for encrypted VPN data
- **TCP 179** - BGP for dynamic routing (over xfrm* only; not exposed on public interface)
- **TCP 22** - SSH for management access (can be restricted to management CIDRs)
- **ICMP** - For path MTU discovery and troubleshooting

**Traffic Rules:**

- **Default policy:** Deny incoming, allow outgoing
- **Loopback:** Unrestricted (localhost communication)
- **SSH access:** Restricted to management CIDRs when configured, otherwise from anywhere (protected by fail2ban)
- **IPsec protocols:** Allowed from peer gateway public IPs (UDP 500, 4500, ESP)
- **BGP:** Allowed only on tunnel interfaces (xfrm*); no TCP/179 on public interface
- **Local VPC subnets:** Traffic from `gateway.local_prefixes` allowed for forwarding through the gateway
- **Tunnel interfaces (xfrm*):** Unrestricted traffic allowed (BGP runs over these encrypted channels)
- **ICMP:** Allowed on public interface for troubleshooting

**BGP (TCP/179) scope:**

- BGP peers use APIPA inner IPs (e.g., `169.254.18.226 ↔ 169.254.18.225`)
- These APIPA addresses are assigned to xfrm interfaces (xfrm0, xfrm1, ...)
- They are reachable only after IPsec decryption; TCP/179 is never opened on eth0

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

**Dynamic Updates:** The agent (`firewall_manager.py`) synchronizes UFW rules with active tunnels:

- Adds peer IPs dynamically as tunnels are configured
- Removes stale peer IPs when tunnels are removed
- Updates local prefix rules when configuration changes
- Maintains firewall state in `/etc/vpngw_peer_ips`, `/etc/vpngw_mgmt_cidrs`, and `/etc/vpngw_local_prefixes`

**Security Benefits:**

- Limits attack surface by denying all non-essential inbound traffic
- Protects against unauthorized access while allowing legitimate VPN traffic
- Prevents accidental exposure of management interfaces
- Enables traffic forwarding for VPC workloads without compromising security

### Routing Guard

Production-grade validation:

- Removes table 220 policy routes (causes asymmetric routing)
- Detects broad APIPA routes (169.254.0.0/16)
- Identifies orphaned routes (routes without active tunnels)
- Structured logging with metrics

## Agent State Management

### Idempotent Applies

Agent compares desired state with `/etc/nebius-vpngw/last-applied.json`:

- Only renders configs if state changed
- Only reloads services if configs changed
- Atomic file updates with temp files

### Service Management

- `nebius-vpngw-agent.service`: Main agent daemon
- `strongswan-starter.service`: IPsec daemon
- `frr.service`: Routing daemon

Reload triggers: `systemctl reload` (SIGHUP)

## Monitoring & Status

### Status Command

```bash
nebius-vpngw status --local-config-file <file>
```

**Reports:**

- Tunnel status (ESTABLISHED, CONNECTING, etc.)
- BGP session state and prefix counts
- Service health (agent, strongSwan, FRR)
- Routing table health (table 220, APIPA routes over XFRM interfaces, orphaned routes)

### Tunnel Status

Per-tunnel information:

- Gateway VM name
- Peer IP address
- Encryption algorithm
- Uptime
- BGP state (for BGP tunnels)

### System Health

Service status for each gateway VM:

- `nebius-vpngw-agent`: active/failed
- `strongswan-starter`: active/failed
- `frr`: active/failed

### Routing Health

Per-VM routing validation:

- Table 220 check: OK/WARNING (policy routes cause asymmetric routing)
- Broad APIPA detection: OK/WARNING (should be /30 subnets only)
- BGP peer routes: Shows APIPA routes over XFRM interfaces
- Orphaned routes count
- Overall health: Healthy/Degraded

## Peer Config Import

### Supported Vendors

- **GCP HA VPN:** Parses Cloud Router config exports
- **AWS Site-to-Site VPN:** Parses downloadable config files
- **Azure VPN Gateway:** Parses exported configurations
- **Cisco IOS:** Parses IOS config snippets

### Usage

```bash
nebius-vpngw create-from-peer-config ./nebius-vpngw.config.yaml \
  --peer-config-file ./gcp-ha-vpn.txt \
  --peer-config-file ./aws-vpn.xml
```

### Merge Behavior

Peer configs populate the template where values are available:

- PSKs (pre-shared keys)
- Remote public IPs
- Crypto proposals
- ASNs
- Inner IPs (for BGP)

Any fields not present in the peer export remain for manual review and editing.
Note: Cloud Router exports do not include PSKs or public IPs; those must be set manually.

## VM Management

### VM Lifecycle

- **Create:** Full provisioning with cloud-init
- **Update:** Config push + agent reload (no recreation)
- **Recreate:** Explicit `--recreate-gw` flag required

### VM Diff Detection

Agent compares desired vs actual VM specs:

- Platform, preset, disk size/type
- NIC count
- Public IPs (preserved across recreation)

### Public IP Preservation

During recreation:

1. Detach allocations from old VM
2. Delete old VM
3. Create new VM
4. Reattach allocations to new VM

**Downtime:** Tunnel establishment time only, IPs never change.

## Development Workflow

### Agent Development

1. Modify agent code in `src/nebius_vpngw/agent/`
2. Rebuild wheel: `python -m build --wheel`
3. Deploy: `nebius-vpngw apply` (uploads new wheel automatically)

Agent is installed on remote VMs, not in local virtualenv.

### Testing Changes

```bash
# Validate schema
nebius-vpngw validate-config test.config.yaml

# Dry-run (hidden flag)
nebius-vpngw apply --local-config-file test.config.yaml --dry-run

# Deploy to test environment
nebius-vpngw apply --local-config-file test.config.yaml
```

### Dependency Upgrades

```bash
# Update pyproject.toml version constraints
# Rebuild wheel (cleans old ones automatically)
python -m build --wheel

# Deploy with new dependencies
nebius-vpngw apply --local-config-file test.config.yaml
```

## Project Structure

```text
├── nebius-vpngw.config.yaml              # User configuration (git-ignored)
├── release.sh                            # One-shot release helper (commit/tag/build/publish with gh)
├── src/nebius_vpngw/
│   ├── __main__.py                       # Python module entry point
│   ├── cli.py                            # CLI orchestrator (nebius-vpngw command)
│   ├── config_loader.py                  # YAML parser and peer config merger
│   ├── schema.py                         # Pydantic schema for YAML validation
│   ├── config_template.py                # Embedded YAML template (source of truth)
│   ├── build.py                          # Binary build utilities
│   ├── vpngw_sa.py                       # Service account management
│   ├── agent/
│   │   ├── main.py                       # On-VM agent daemon
│   │   ├── frr_renderer.py               # FRR/BGP config renderer
│   │   ├── strongswan_renderer.py        # strongSwan/IPsec config renderer
│   │   ├── xfrm_manager.py               # XFRM interface lifecycle (create, address, route)
│   │   ├── routing_guard.py              # Declarative route management & cleanup
│   │   ├── firewall_manager.py           # UFW firewall rule synchronization
│   │   ├── tunnel_iterator.py            # Centralized tunnel enumeration
│   │   ├── state_store.py                # Agent state persistence
│   │   ├── status_check.py               # Tunnel/BGP/service health checks
│   │   └── sanity_check.py               # Routing invariant validation tool
│   ├── deploy/
│   │   ├── vm_manager.py                 # VM lifecycle (create/delete/recreate)
│   │   ├── vm_diff.py                    # VM configuration change detection
│   │   ├── route_manager.py              # VPC route management (static mode)
│   │   └── ssh_push.py                   # Package/config deployment over SSH
│   ├── peer_parsers/
│   │   ├── gcp.py                        # GCP HA VPN config parser
│   │   ├── aws.py                        # AWS Site-to-Site VPN config parser
│   │   ├── azure.py                      # Azure VPN Gateway config parser
│   │   └── cisco.py                      # Cisco IOS config parser
│   └── systemd/
│       ├── nebius-vpngw-agent.service    # Agent systemd unit
│       ├── fix-routes.sh                 # Route cleanup helper (table 220/APIPA)
│       ├── nebius-vpngw-fix-routes.service  # Service wrapper for route cleanup
│       └── nebius-vpngw-fix-routes.timer    # Timer to enforce route cleanup periodically
```

### Module Descriptions

**Orchestrator (runs on operator machine):**

- `cli.py`: Main CLI entry point, orchestrates VM provisioning and config deployment
- `config_loader.py`: Parses YAML, merges peer configs for generated configs, expands env vars, validates schema
- `schema.py`: Pydantic models for strict validation with type checking and constraints
- `config_template.py`: Embedded YAML template, source of truth, always aligned with schema
- `vpngw_sa.py`: Service account lifecycle for API authentication
- `build.py`: Utilities for building standalone binaries (PyInstaller)

**Agent (runs on gateway VM):**

- `main.py`: Agent daemon, renders configs, applies idempotently, handles SIGHUP reload
- `frr_renderer.py`: Generates FRR BGP configuration from YAML
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration
- `routing_guard.py`: Enforces routing invariants, removes problematic routes
- `firewall_manager.py`: Synchronizes UFW rules with active tunnels
- `tunnel_iterator.py`: Centralized tunnel enumeration for consistent indexing
- `state_store.py`: Persists last-applied state for idempotency
- `status_check.py`: Collects health metrics for status command
- `sanity_check.py`: Standalone routing validation tool

**Deployment:**

- `vm_manager.py`: VM lifecycle using Nebius SDK
- `vm_diff.py`: Detects VM changes requiring recreation
- `route_manager.py`: Manages VPC static routes (static mode only)
- `ssh_push.py`: Deploys agent package and config via SSH/SFTP

**Peer Config Parsers:**

- `gcp.py`, `aws.py`, `azure.py`, `cisco.py`: Parse vendor-specific configs

## Tips & Troubleshooting

### UFW Must Be Active for VPN to Work

**Problem:** VMs in local subnets cannot reach remote networks via VPN, even though IPsec tunnels are ESTABLISHED and BGP sessions are UP.

**Symptoms:**

- Tunnels show ESTABLISHED status
- BGP peers are connected and exchanging routes
- Routes appear in routing tables
- But: VMs cannot ping or connect to remote networks
- tcpdump on gateway shows zero packets from local VMs
- XFRM encryption counters don't increment for data traffic

**Root Cause:** UFW (firewall) is inactive. **UFW MUST be active for the VPN gateway to forward traffic correctly.**

**Why This Happens:**

- UFW activates the Linux netfilter framework
- Without netfilter active, the kernel doesn't properly integrate with XFRM (IPsec) tunnels for packet forwarding
- Even with `net.ipv4.ip_forward=1` set, packets from the VPC fabric won't be forwarded through XFRM without netfilter
- This is not about blocking traffic - it's about netfilter initialization being required for XFRM forwarding

**Diagnosis:**

```bash
# Check UFW status
sudo ufw status

# If it shows "Status: inactive", that's the problem!
```

**Solution:**

```bash
# Re-apply firewall config via agent (preferred)
sudo systemctl restart nebius-vpngw-agent

# If UFW was disabled manually:
sudo ufw enable

# Verify it's active
sudo ufw status verbose
# Should show: Status: active
```

**After Fix:**

- Test connectivity from VMs immediately - it should work instantly
- UFW is now enabled on boot, so this won't happen again after reboots

**Prevention:**

- Firewall setup runs automatically during VM creation (cloud-init) and is re-applied by the agent
- Always verify UFW is active after deploying a new gateway
- Include UFW check in monitoring/health checks

### Subnet CIDR Issues

**Problem:** When creating the `vpngw-subnet`, it may show the network's parent CIDR (e.g., `/13`) instead of the intended `/24` in the console, even though the code calculates and requests the correct CIDR.

**Root Cause:** The Nebius VPC API field `use_network_pools` defaults to `true`. When `true`, the subnet inherits the network's address pool instead of using the explicitly specified CIDR. The issue was caused by a subtle bug in subnet creation:

1. **Initial Creation:** Subnet is created correctly with `IPv4PrivateSubnetPools` containing the pools array and `use_network_pools=False`
2. **Route Table Attachment:** When attaching a route table via `UpdateSubnetRequest`, the code was creating a new `SubnetSpec` with only `network_id` and `route_table_id`
3. **Field Reset:** The missing `ipv4_private_pools` field in the update request caused the API to reset the subnet to default settings (`use_network_pools=true`)

**Solution:** When updating a subnet (e.g., to attach a route table), always preserve the existing `ipv4_private_pools` and `ipv4_public_pools` fields from the original subnet spec:

```python
# Get existing pool configuration
existing_ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
existing_ipv4_public_pools = getattr(subnet_spec, "ipv4_public_pools", None)

# Include in update request
update_req = UpdateSubnetRequest(
    metadata=ResourceMetadata(...),
    spec=SubnetSpec(
        network_id=subnet_network_id,
        route_table_id=rt_id,
        ipv4_private_pools=existing_ipv4_private_pools,  # Preserve!
        ipv4_public_pools=existing_ipv4_public_pools,    # Preserve!
    ),
)
```

**Verification:** After subnet creation, check that:

- `spec.ipv4_private_pools.use_network_pools` is `false` (or field is absent)
- `spec.ipv4_private_pools.pools[0].cidrs[0].cidr` shows the expected `/24` CIDR
- `status.ipv4_private_cidrs` contains the expected `/24` CIDR (what the console displays)

### Packet Duplication Issue (Historical - Resolved)

**Problem (VTI mode only - now removed):** When using legacy VTI (Virtual Tunnel Interface) mode, pinging the VPN gateway from VMs in other subnets would show 60+ duplicate ICMP packets (marked as `DUP!` in ping output).

**Root Cause:** strongSwan with VTI mode intercepted all packets for IPsec policy evaluation. When packets were destined for the gateway's own IP (not through the tunnels), VTI processing would duplicate packets during policy lookups or tunnel path evaluation. With 2 active tunnels to GCP, each packet was evaluated against multiple tunnel policies, creating duplicates.

**Resolution:**
**Switching to XFRM interfaces completely eliminated this issue.** XFRM's `if_id` binding mechanism provides clean separation between tunnel interfaces and avoids the packet duplication problem that occurred with VTI mode. The current implementation uses XFRM interfaces exclusively and does not experience packet duplication.
