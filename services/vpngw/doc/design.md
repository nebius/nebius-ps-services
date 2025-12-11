# Nebius VPN Gateway (VM-Based) — Design Document

Version: v0.2

## 1. Purpose & Scope

- Deliver a VM-based site-to-site VPN gateway for Nebius AI Cloud using IPsec (strongSwan) and routing (FRR for BGP, static as fallback).
- Provide a CLI orchestrator plus per-VM agent with idempotent applies from a single YAML configuration, optionally merged with vendor peer configs.
- Support common cloud/on-prem peers (GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco/others).

## 2. Goals & Non-Goals

- **Goals:** IKEv2 (default, optional IKEv1), PSK auth, AES-256, SHA-256/384/512, DH 14/20/24, configurable lifetimes; BGP preferred, static supported; repeatable applies; minimal operator state; stable public IPs.
- **Non-goals (current):** ECMP in VPC route tables, external NAT/LB, full multi-NIC support (future-ready but platform currently single-NIC).

## 3. Architecture Overview

- **Orchestrator CLI (`nebius-vpngw`):** runs on operator laptop/CI. Reads YAML (+ optional peer configs), resolves a per-VM plan, ensures VMs and IP allocations, pushes configs over SSH, triggers agent reload.
- **Gateway VM:** Ubuntu LTS, packages strongSwan/FRR/Python; runs one `nebius-vpngw-agent` systemd service.
- **Agent:** single daemon per VM; renders configs, applies idempotently, and persists `/etc/nebius-vpngw/last-applied.json`. Reload via SIGHUP.
- **Optional HA Route Controller:** updates VPC route next-hops to flip between gateway VMs for HA.
- **Deployment modes:** (a) single VM with multiple tunnels (active/active tunnels, VM is SPOF), (b) gateway group (N VMs) with per-tunnel pinning and route switching for VM-level HA.

## 4. Nebius Networking Model

- One VPC network chosen via `network_id` (optional; defaults to environment’s default VPC).
- One dedicated subnet `vpngw-subnet` (/27) created if missing under the selected network; workload subnets remain separate.
- Platform constraint (current): 1 NIC per VM, each with 1 public IP; all tunnels share that IP and are differentiated by IKE/IPsec IDs.
- Future-ready: `vm_spec.num_nics` accepts >1 when platform allows; code and config are structured for multi-NIC.
- Dedicated subnet rationale:
  - Isolation and blast-radius control for firewall/NACL rules and logging; limits exposure of workloads to gateway misconfig.
  - Routing clarity for next-hops, failover, and asymmetric-path avoidance; easier to reason about HA route flips.
  - IP hygiene: small, controlled CIDR for gateway NICs/allocations; avoids overlap or churn with app addresses.
  - Policy separation: distinct egress controls/inspection for gateway traffic without touching app subnets.
  - Operations: safer redeploys/recreates and reduced ARP/ND noise near workloads.

## 5. Public IP Allocations (`external_ips`)

- Config shape: `external_ips[instance_index][nic_index]` → allocation IP string.
- Behavior: omitted/empty ⇒ create allocations; provided ⇒ use existing; insufficient ⇒ create missing. Auto names: `{instance}-eth{N}-ip`.
- Preservation on recreation: allocations are kept and reattached; recreation does not delete allocations. Empty slots trigger new allocation creation.
- Examples:
  - Single VM, single NIC:

    ```yaml
    external_ips: [["1.2.3.4"]]
    ```

  - Two VMs, single NIC each:

    ```yaml
    external_ips: [["1.2.3.4"], ["5.6.7.8"]]
    ```

  - Future multi-NIC:

    ```yaml
    external_ips: [["1.2.3.4", "1.2.3.5"]]
    ```

- Limits: platform only supports 1 NIC and no public IP aliases (aliases are private-only).

## 6. Configuration Model (YAML)

- Single file `nebius-vpngw.config.yaml` with sections: `gateway_group`, `defaults`, `gateway`, `connections[*].tunnels[*]` (supports `ha_role`, `routing_mode`, crypto, APIPA inner IPs).
- Key fields: `network_id` (optional), implicit `vpngw-subnet`, `external_ips` as above, `gateway.local_prefixes`.
- Merge precedence when resolving: tunnel overrides connection overrides peer-config overrides defaults; missing mandatory fields error out.
- Environment placeholders `${VAR}` expand; all missing vars reported together.
- First run convenience: if no local config is provided/found, CLI copies a template to CWD then exits.

### 6.1. Schema Validation

**Strict, versioned schema validation** (Pydantic-based) protects against configuration errors:

**Purpose:**

- Catches typos before deployment (e.g., `inner_ciddr` → caught as unknown field)
- Enforces type safety (numbers, IPs, CIDRs validated)
- Validates constraints (ASN ranges, /30 subnets, APIPA ranges)
- Checks logical consistency (BGP requires `remote_asn`, static forbids it)
- Verifies resource quotas (connections/tunnels within limits)

**API Versioning:**

- `version: 1` field required in config
- Future schema changes → new version number
- Backwards compatibility maintained through version detection

**Validation Features:**

- `extra="forbid"` on all models → rejects unknown fields
- Field validators: IP addresses, CIDR ranges, ASN ranges (64512-65534)
- Cross-field validators: inner IPs must be within inner_cidr, not network/broadcast
- Quota validators: total connections/tunnels within max limits
- Routing mode consistency: BGP mode requires BGP config, static forbids it

**CLI Integration:**

```bash
# Validate configuration without deployment (recommended first step)
nebius-vpngw validate-config nebius-vpngw.config.yaml

# Validation runs automatically during apply
nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml
```

**Important:** The `validate-config` command takes the config file as a **positional argument**, not as `--local-config-file`. This is different from other commands like `apply` which use the flag syntax.

**Example Validation Errors:**

```text
Configuration validation failed:
  • connections -> 0 -> tunnels -> 0 -> inner_cidr: inner_cidr '192.168.1.0/30' must be in APIPA range 169.254.0.0/16
  • connections -> 1 -> bgp -> remote_asn: remote_asn is required when BGP is enabled
  • gateway -> local_asn: ASN 65535 is invalid. Use private ASN (64512-65534)
  • connections -> 0 -> tunnels -> 1 -> inner_ciddr: Extra inputs are not permitted
```

**Security Benefits:**

- No blind concatenation of arbitrary fields into shell commands
- Only validated, typed values used in templates (strongSwan, FRR)
- Prevents injection-like problems via malformed YAML
- Clear error messages guide users to fix issues

**Implementation:**

- `src/nebius_vpngw/schema.py`: Pydantic models for entire config structure
- `src/nebius_vpngw/config_loader.py`: Validates after env expansion, before processing
- `src/nebius_vpngw/cli.py`: `validate-config` command for standalone validation

## 7. Workflows & CLI

### 7.1. Commands

**Configuration Validation:**

- `nebius-vpngw validate-config <config-file>`: Validates configuration against schema without deployment
  - Takes config file as positional argument (not `--local-config-file`)
  - Performs full schema validation (types, constraints, logical consistency)
  - Returns exit code 0 (valid) or 1 (invalid)
  - Displays rich formatted output with summary or detailed errors
  - Use before deployment to catch errors early

**Deployment:**

- `nebius-vpngw apply --local-config-file <file>`: Deploy or update gateway
  - Runs schema validation automatically before deployment
  - Flags: `--local-config-file`, `--peer-config-file` (repeatable), `--recreate-gw`, `--project-id`, `--zone`
  - Typical flow: parse args → load YAML → validate schema → merge peer configs → ensure network/subnet → ensure VMs + allocations → push per-VM config over SSH → `systemctl reload nebius-vpngw-agent` → reconcile static routes (if static mode)

**Status & Monitoring:**

- `nebius-vpngw status --local-config-file <file>`: Show tunnel status and gateway health
- `nebius-vpngw list-routes --local-config-file <file>`: List VPC routes
- `nebius-vpngw add-routes --local-config-file <file>`: Add static routes to VPC

**Default Behavior:**

- Running `nebius-vpngw` alone (with config present): shows status
- Running `nebius-vpngw` alone (no config): creates template from embedded template

### 7.2. Peer Import & Merging

- Peer import: vendor parsers (GCP/AWS/Azure/Cisco) normalize templates; merger fills only missing fields—YAML topology is never overridden.
- Merge precedence: tunnel overrides connection overrides peer-config overrides defaults

## 8. Routing Modes & Local Prefixes

- Modes: global default under `defaults.routing.mode` (bgp|static); override per connection/tunnel.
- BGP: FRR neighbors per active tunnel; advertises `gateway.local_prefixes` when `connection.bgp.advertise_local_prefixes` is true. No tunnel-level override.
- Static: strongSwan leftsubnet from `gateway.local_prefixes` unless `tunnel.static_routes.local_prefixes` overrides (supports subnet isolation/migration).
- Use cases: selective BGP advertisement, BGP peer without ads, per-tunnel static prefixes, mixed BGP/static across connections.

## 9. HA & SLA

- Single VM (`instance_count=1`): multiple active tunnels; ECMP within gateway and/or peer side; VM is SPOF.
- Multi-VM (`instance_count>1`): assign tunnels via `gateway_instance_index`; `ha_role: active|disable` to control live tunnels per VM; optional route controller flips VPC routes to standby on failure.

## 10. VM Build & Services

- OS: Ubuntu 24.04 LTS. Packages: strongSwan ≥5.9, FRR 10.5.0+, Python 3.12+.
- Agent: systemd service (`nebius-vpngw-agent`), reload via SIGHUP; renders strongSwan/FRR configs and persists last applied state.
- Bootstrap via cloud-init installs packages, unit file, directories; tunnel config applied only at runtime by the agent.

### 10.1 Cloud-init Bootstrap Structure

The cloud-init configuration properly separates foundation infrastructure from mutable configurations to enable safe VM recreation and dynamic tunnel management:

**Foundation (Bootstrapped at VM creation):**

- Package installation: strongSwan, `libcharon-extra-plugins`, Python 3, pip, YAML libraries
- FRR 10.5.0 installation from official FRRouting APT repository
- systemd service unit for `nebius-vpngw-agent.service`
- Kernel parameters: IP forwarding (`net.ipv4.ip_forward=1`, `net.ipv6.conf.all.forwarding=1`), RP filter settings (`net.ipv4.conf.all.rp_filter=0`, `net.ipv4.conf.default.rp_filter=0`)
- Directory structure: `/etc/nebius-vpngw/`, `/var/lib/nebius-vpngw/`
- FRR daemons configuration enabling BGP, Zebra, and static daemons

**Mutable Configurations (Handled by agent at runtime):**

- IPsec tunnel configurations (strongSwan connection configs in `/etc/swanctl/conf.d/`)
- BGP neighbor configurations (FRR `frr.conf` per-tunnel neighbors)
- Route additions/updates via VPC SDK or FRR
- Tunnel IP addresses (XFRM interface addresses, APIPA inner IPs)
- PSK secrets and IKE identifiers

This separation ensures:

- VM recreation preserves public IPs and can rebuild identical foundation
- Tunnel configurations can be updated without VM replacement
- Agent can safely apply config changes via reload without cloud-init re-execution
- Foundation components (packages, kernel params) are immutable post-creation
- Dynamic elements (tunnels, routes, BGP sessions) remain under agent control

## 11. Idempotency & State

- Orchestrator is effectively stateless: derives desired state each run from YAML + live discovery by deterministic names.
- Agent keeps minimal VM-local state (`last-applied.json`) to avoid unnecessary reloads and to be restart-safe.
- Re-runs reconcile actual vs desired; `--recreate-gw` forces VM replacement while preserving allocations.

## 12. Implementation Status (repo)

- CLI and config merger functional, including env expansion and peer merge heuristics.
- Agent renders scaffold configs and writes `last-applied.json`.
- Managers under `deploy/` (VM/SSH/route) are scaffolds logging intended actions; they need Nebius SDK + SSH integration to be operational.
- Entry points in `pyproject.toml`: `nebius-vpngw`, `nebius-vpngw-agent`, `build-binary`. Template creation on first run is implemented.

## 13. Allocation Preservation Algorithm (Summary)

- On recreation: capture attached allocations, delete VM (allocations stay), recreate VM, reattach preserved allocations; create new ones for any gaps. Error if YAML-specified allocation is busy/missing; fall back to creation only when YAML omitted/insufficient.

## 14. Deployment & Packaging Workflow

- Local dev: `poetry install` or `pip install -e .` for orchestrator use.
- Agent deployment: orchestrator builds/uses wheel from `dist/`, uploads via SSH/SFTP, installs on VM (`pip install --force-reinstall`), places config under `/etc/nebius-vpngw/config-resolved.yaml`, reloads agent.
- Always rebuild wheel after agent code changes (clean `dist/ build/ *.egg-info` then `poetry build` or `python -m build --wheel`); cached wheels otherwise hide changes on VMs.
- Verification on VM: `systemctl status nebius-vpngw-agent`, `journalctl -u nebius-vpngw-agent -n 50`, inspect `/etc/ipsec.conf` and `/etc/frr/bgpd.conf`.

## 15. Known Issues & Root Cause Analysis

### Ubuntu 22.04/24.04 VTI Plugin Not Available

**Issue:** The strongSwan VTI kernel plugin (`kernel-vti`) is not included in the `libcharon-extra-plugins` package on Ubuntu 22.04 and 24.04. Attempting to use VTI mode with `conn ... vti=yes` results in error:

```text
plugin feature VTI support not available
```

This prevents automatic VTI interface creation via strongSwan's built-in mechanism.

**Root Cause:** Ubuntu's strongSwan packaging excludes the VTI plugin. While the plugin is available in strongSwan source code, Ubuntu maintainers don't compile or ship it in the binary packages.

**Solution:** Use Google's production-tested approach with a **custom updown script** that creates VTI interfaces manually:

1. **Custom updown script** (`/var/lib/strongswan/ipsec-vti.sh`):
   - Reads `PLUTO_MARK_OUT` and `PLUTO_MARK_IN` environment variables set by strongSwan
   - Creates VTI interfaces using kernel's native support: `ip link add type vti`
   - Configures interface IPs, MTU, and RP filter settings
   - Script deployed to VM via agent's `ssh_push.py`

2. **strongSwan configuration** uses `mark=%unique` instead of `vti=yes`:

   ```text
   conn gcp-tunnel-0
       mark=%unique              # Assigns unique marks, sets PLUTO_MARK_OUT/IN
       leftsubnet=0.0.0.0/0
       rightsubnet=0.0.0.0/0
       leftupdown="/var/lib/strongswan/ipsec-vti.sh 0 169.254.18.225/30 169.254.18.226/30"
   ```

3. **Agent code** (`strongswan_renderer.py` lines 98, 147-159):
   - Uses `mark=%unique` for route-based VPN (BGP mode)
   - Configures custom `leftupdown` script with tunnel parameters
   - No VTI plugin dependency

**Benefits of Custom Updown Script Approach:**

- Works on Ubuntu 22.04, 24.04, Debian, and other distributions without VTI plugin
- Based on Google's production approach (verified in fast-onprem-lab repository)
- Uses kernel's native VTI support (available since Linux 3.6)
- More control over interface naming, MTU, and configuration
- Portable across different strongSwan versions and Linux distributions

**Reference Implementation:**

- Google's example: <https://github.com/sruffilli/fast-onprem-lab/blob/main/gcp-lab/ipsec-vti.sh>
- Agent implementation: `src/nebius_vpngw/systemd/ipsec-vti.sh` (68 lines)
- Deployment code: `src/nebius_vpngw/deploy/ssh_push.py` (lines 213-218, 272-274)

### BGP Policy Requirement in FRR 8.4+

**Issue:** FRR 8.4+ enforces eBGP policy requirements by default. BGP sessions establish but exchange zero routes with message "Inbound/Outbound updates discarded due to missing policy".

**Solution:** Added `no bgp ebgp-requires-policy` command to BGP router configuration in `frr_renderer.py` to disable policy requirement for simple deployments. For production, consider implementing explicit route-maps for import/export control.

### GCP HA VPN ASN Configuration

**Issue:** BGP OPEN message errors showing "Bad Peer AS" when Cloud Router ASN doesn't match configured `remote_asn`.

**Solution:** Verify actual GCP Cloud Router ASN (visible in GCP Console or in BGP OPEN messages via `sudo vtysh -c "show ip bgp neighbor <ip>"`) and update YAML config to match. Common mistake: using default template ASN (64514) when Cloud Router uses different ASN (e.g., 65014).

### BGP Session Establishment Failures with VTI Interfaces

**Issue:** BGP sessions remain in "Connect" state despite VTI interfaces being created, IPsec tunnels ESTABLISHED, and successful ping to BGP peer IPs. Symptoms include:

- VTI interfaces (vti0, vti1) created successfully with correct IPs
- `swanctl --list-sas`: Shows IPsec tunnels ESTABLISHED with bytes flowing
- `ping` to BGP peer IPs (169.254.x.x) works with 0% loss
- `sudo vtysh -c "show bgp summary"`: BGP State = Connect or Active
- `tcpdump -i vti0 port 179`: Shows incoming SYN packets from GCP but no replies sent

**Root Cause:** This issue is **NOT** related to the VTI plugin or the custom updown script solution. The problem is caused by **policy routing rules** and **missing route configuration** that prevent BGP reply packets from using the correct VTI interface:

1. **Policy Routing Table Override**: Routing policy rule for table 220 (priority 220) forces traffic via eth0 default route, consulted before main table (priority 32766). This causes BGP SYN-ACK replies to exit eth0 instead of vti0.

2. **Missing /32 Host Routes**: BGP peer IPs lack explicit /32 host routes via VTI interfaces, causing kernel to fall back to default route for replies.

3. **FRR Source IP Misconfiguration**: FRR not configured to use VTI interfaces as source, causing connections from primary interface IP (10.x.x.x) instead of APIPA inner IPs (169.254.x.x).

**Evidence of the Issue:**

```bash
# Packet flow analysis shows asymmetric routing:
$ sudo tcpdump -i any -n "tcp port 179 and host 169.254.18.225" -c 10
vti0  In  IP 169.254.18.225.43604 > 169.254.18.226.179: Flags [S]   # SYN arrives on vti0
eth0  Out IP 169.254.18.226.179 > 169.254.18.225.43604: Flags [S.]  # SYN-ACK exits eth0 ❌

# Policy routing rule forcing traffic to eth0:
$ ip rule show
0:      from all lookup local
220:    from all lookup 220          # ← This overrides main table!
32766:  from all lookup main

$ ip route show table 220
default via 169.254.169.1 dev eth0 proto static  # ← Forces traffic via eth0

# BGP connecting from wrong source IP:
$ sudo ss -tn | grep ":179"
SYN-SENT 0  1  10.48.0.27:34392 169.254.18.225:179  # ← Using eth0 IP, not VTI IP ❌
```

**Solution (Multi-Part Fix):**

1. **Remove Policy Routing Rule for Table 220:**

   ```bash
   sudo ip rule del table 220
   ```

   This prevents the override of the main routing table and allows BGP replies to use VTI interfaces.

2. **Add /32 Host Routes for BGP Peers:**

   ```bash
   sudo ip route add 169.254.18.225/32 dev vti0
   sudo ip route add 169.254.5.153/32 dev vti1
   ```

   Ensures kernel routing uses VTI interfaces for BGP peer traffic.

3. **Configure FRR with update-source:**

   ```bash
   sudo vtysh -c "conf t" \
            -c "router bgp 65010" \
            -c "neighbor 169.254.18.225 update-source vti0" \
            -c "neighbor 169.254.5.153 update-source vti1" \
            -c "end" \
            -c "wr"
   ```

   Forces FRR to use VTI interface IPs as source for BGP connections.

**Automated Fix in Agent Code:**

The agent now automatically handles these issues in `strongswan_renderer.py`:

1. **Policy Rule Removal** (`_protect_critical_routes()` method, lines 277-284):

   ```python
   result = subprocess.run(["ip", "rule", "list"], capture_output=True, text=True)
   if "lookup 220" in result.stdout:
       print("[StrongSwan] Removing policy rule for table 220")
       subprocess.run(["ip", "rule", "del", "table", "220"], check=False)
   ```

2. **BGP Peer /32 Routes** (lines 255-265):

   ```python
   for vti in vti_endpoints:
       remote_inner = vti["remote_inner_ip"]
       subprocess.run(["ip", "route", "replace", f"{remote_inner}/32", "dev", name], check=False)
   ```

3. **FRR update-source Configuration** (to be implemented in `frr_renderer.py`):
   Will automatically add `neighbor X.X.X.X update-source vtiN` commands.

**Verification After Fix:**

```bash
# Confirm routing rules (table 220 removed):
$ ip rule show
0:      from all lookup local
32766:  from all lookup main
32767:  from all lookup default

# Verify BGP using correct source IP:
$ sudo ss -tn | grep ":179"
ESTAB 0  0  169.254.18.226:33469 169.254.18.225:179  # ← Using VTI IP ✅

# Confirm BGP sessions established:
$ sudo vtysh -c "show bgp summary"
Neighbor        V    AS   MsgRcvd MsgSent  Up/Down State/PfxRcd PfxSnt
169.254.5.153   4  65014     5       5    00:00:23      1         1
169.254.18.225  4  65014     5       5    00:00:24      1         1
```

**Important Note:** This issue affects **both** the VTI plugin-based approach and the custom updown script approach. The custom updown script successfully creates VTI interfaces without the plugin, but BGP still requires proper routing configuration regardless of how VTI interfaces are created.

### FRR 8.4.4 Route Installation Bug (Ubuntu 24.04)

**Issue:** BGP sessions establish successfully and routes are learned/advertised, but learned routes remain marked "inactive" and are never installed into the kernel routing table. Symptoms:

- `sudo vtysh -c "show ip bgp"`: Shows routes with `*>` (valid, best) but no FIB marker
- `sudo vtysh -c "show ip route"`: Shows BGP routes marked "inactive"
- `ip route get <remote-ip>`: Falls back to default route instead of using BGP-learned route
- Traffic to remote networks fails or routes via wrong path

**Root Cause:** Known bug in FRR 8.4.4 packaged with Ubuntu 24.04 LTS. The zebra routing daemon fails to install certain BGP routes into the kernel FIB, particularly routes learned via multiple paths (ECMP) or routes with specific next-hop resolution patterns.

**Workaround (Temporary):** Manually install routes as they're learned:

```bash
# Monitor FRR for new routes and install manually
sudo vtysh -c "show ip bgp" | grep "^*>" | awk '{print $2,$3}' | \
  while read prefix nexthop; do
    sudo ip route replace $prefix via $nexthop
  done
```

**Permanent Solution:** Upgrade to FRR 10.x from official FRR repository:

```bash
# Add FRR official repository (Ubuntu 24.04)
curl -s https://deb.frrouting.org/frr/keys.asc | sudo tee /usr/share/keyrings/frrouting.asc
echo "deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr noble frr-stable" | \
  sudo tee /etc/apt/sources.list.d/frr.list
sudo apt update
sudo apt install frr=10.5.0-0~ubuntu24.04.1

# Verify version
sudo vtysh -c "show version" | grep "FRRouting"
```

**Status:** FRR 10.5.0 resolves this issue. Consider updating VM deployment scripts to install FRR from official repo instead of Ubuntu packages. Route installation has been verified working in FRR 10.x with identical configuration.

## 16. Limitations & Risks

- Platform: single NIC per VM, no public-IP aliases; no ECMP in route tables (load sharing happens within gateway/peer).
- Requires stable public allocations—document and monitor unassigned allocations (30-day grace before provider GC).
- SSH/SDK integrations are scaffolds; production requires completing those components and adding tests.

## 17. Security Hardening & Production Improvements

### 17.1 Security Hardening Stack

The VPN Gateway VM incorporates comprehensive security hardening applied via cloud-init during VM creation. This provides defense-in-depth for production deployments:

**SSH Hardening (`/etc/ssh/sshd_config.d/50-vpngw.conf`):**

- Key-only authentication (passwords disabled)
- Root login disabled
- Maximum 3 authentication attempts
- Verbose logging for audit trails
- Modern cryptographic algorithms only

**Fail2ban Protection:**

- Monitors SSH authentication logs
- 3 failed attempts trigger 1-hour ban
- Protects against brute-force attacks
- Automatic IP address blocking/unblocking

**UFW Firewall (VPN-Safe Configuration):**

- Default deny incoming on eth0 (management interface)
- Explicit allow rules for IPsec protocols:
  - UDP 500 (IKE)
  - UDP 4500 (NAT-T)
  - ESP protocol (IP protocol 50)
- SSH access restricted to management CIDRs (if configured)
- **Critical:** VTI/XFRM interfaces are NOT filtered by UFW
- BGP traffic (TCP 179) flows freely over tunnel interfaces
- ICMP allowed for troubleshooting (ping, traceroute)

**Dynamic Firewall Management:**

- `firewall_manager.py` module synchronizes UFW rules with config changes
- Updates peer IP whitelist when tunnels are added/removed/modified
- Maintains `/etc/vpngw_peer_ips` and `/etc/vpngw_mgmt_cidrs`
- Idempotent script execution (only reloads if changes detected)
- Integrated into agent reload cycle (non-blocking, logs warnings on failure)

**Auditd Security Monitoring:**

- Logs all command executions
- Monitors critical configuration files:
  - `/etc/nebius-vpngw/`
  - `/etc/swanctl/`
  - `/etc/frr/`
  - `/etc/ssh/sshd_config`
- Provides tamper detection and forensics capability

**System Hardening (sysctl):**

- IP forwarding enabled for VPN functionality
- ICMP redirects disabled (prevents routing attacks)
- Martian packet logging enabled (detects spoofing)
- SYN cookies enabled (SYN flood protection)
- Source validation disabled on tunnel interfaces (required for VPN)

**Unattended Security Updates:**

- Automatic installation of security patches
- Configurable to minimize or eliminate reboot frequency
- Ensures VM stays protected against known vulnerabilities

**Restart Monitoring:**

- System timer alerts when reboot is required for updates
- Operator can schedule maintenance windows appropriately

### 17.2 Routing Guard with Production Hardening

The agent includes an enhanced routing guard (`routing_guard.py`) that enforces routing table invariants with production-grade observability:

**Explicit APIPA Scoping:**

- **Tunnel APIPA routes** (169.254.x.x/32): Routes we own and manage
  - Tracked in `expected_tunnel_cidrs` and `expected_tunnel_peers` dictionaries
  - BGP peer /32 routes explicitly mapped to VTI interfaces
- **Cloud metadata routes** (169.254.169.254/32): Routes the platform owns
  - Explicitly excluded from cleanup via `CLOUD_METADATA_PREFIX`
- **Broad APIPA routes** (169.254.0.0/16): Never allowed (indicates misconfiguration)

**Structured Logging with Metrics:**

- All routing guard functions return metrics (bool or int counts)
- Summary statistics logged after each enforcement cycle:
  - `table_220_removed=X` - Policy routing rules cleaned up
  - `broad_apipa_removed=Y` - Misconfigured broad routes removed
  - `orphaned_apipa_removed=Z` - Stale tunnel routes cleaned
  - `bgp_peer_routes_ensured=N` - BGP peer /32 routes verified
- Enables monitoring, alerting, and trend analysis
- Facilitates debugging without verbose per-route logging

**Benefits:**

- Prevents routing loops and asymmetric routing
- Protects against platform policy routing overrides (table 220)
- Ensures BGP connectivity by maintaining peer /32 routes
- Safe coexistence with cloud metadata service
- Production-ready observability for SRE teams

### 17.3 Routing Health Checks in Status Command

The `nebius-vpngw status` command now includes integrated routing health validation:

**Health Checks Performed:**

- **Table 220 Presence:** Detects policy routing rules that override main table (should not exist)
- **Broad APIPA Routes:** Checks for 169.254.0.0/16 routes (indicates misconfiguration)
- **Orphaned Routes:** Counts APIPA routes to detect stale configurations
- **Overall Health:** Aggregates checks into single status indicator

**Output Format:**

```text
Routing Table Health:
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Gateway VM      ┃ Table 220 ┃ Broad APIPA ┃ Orphaned Routes ┃ Overall ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ nebius-vpn-gw-0 │ OK        │ OK          │ 5 routes        │ Healthy │
└─────────────────┴───────────┴─────────────┴─────────────────┴─────────┘
```

**Implementation:**

- Lightweight remote Python check (no dependencies)
- Runs in parallel with service health checks
- Color-coded: green (OK), red (EXISTS/ERROR), yellow (warnings)
- Zero impact on existing tunnel operations

**Benefits:**

- Proactive detection of routing issues before they cause outages
- Immediate visibility into configuration drift
- Reduces MTTR (Mean Time To Recovery) by surfacing issues early
- No additional flags or commands required (integrated into existing workflow)

### 17.4 Architecture Benefits

**Defense-in-Depth:**

- Multiple security layers protect against different attack vectors
- Compromise of one layer doesn't compromise entire system
- Hardening applied at VM creation (immutable foundation)

**Production Readiness:**

- Meets common enterprise security requirements
- Audit logging enables compliance verification
- Automated updates reduce operational burden
- Monitoring hooks for integration with SIEM/alerting systems

**VPN-Safe Design:**

- Security hardening does NOT interfere with IPsec or BGP
- Firewall rules explicitly allow VPN protocols
- Tunnel interfaces bypass firewall completely
- Dynamic management adapts to configuration changes

**Operational Excellence:**

- Structured logging provides actionable metrics
- Health checks enable proactive monitoring
- Non-blocking integrations prevent cascade failures
- Idempotent operations support safe retries

### 17.5 Deployment Considerations

**New VM Deployments:**

- All security hardening applies automatically via cloud-init
- Firewall setup script created at: `/usr/local/bin/setup-vpngw-firewall.sh`
- Management CIDRs configured from YAML (optional)

**Existing VM Updates:**

- Structured logging and routing health checks work immediately after agent update
- Firewall manager is integrated but requires firewall setup script (only on new VMs)
- To apply full hardening: recreate VM with `nebius-vpngw apply --recreate-gw`

**Rollback Considerations:**

- Security hardening is immutable (applied at creation)
- Firewall rules can be manually adjusted via SSH if needed
- Agent features can be disabled by reverting to previous package version

### 17.6 Declarative Route Management & Orphan Cleanup

The routing guard implements **declarative APIPA route management** to ensure the kernel routing table always matches YAML configuration. This prevents state drift and routing bugs caused by leftover routes.

#### What Are "Orphaned Routes"?

**Orphaned routes** are routes in the kernel routing table that are NOT declared in the YAML configuration. These are automatically detected and removed by the routing guard.

**Common Sources of Orphaned Routes:**

1. **Old VTI connected routes** from previously-active tunnels:
   - When a tunnel is deleted from YAML, the kernel may retain `169.254.X.Y/30 dev vti2`
   - strongSwan or the kernel doesn't always clean up automatically

2. **Old /32 host routes** pointing at the wrong VTI:
   - Example: `169.254.5.153 dev vti0` when it should be `dev vti1` per YAML
   - Occurs when VTI numbering changes or tunnel layout is reconfigured

3. **Kernel artifacts** from tunnel up/down events:
   - strongSwan sometimes briefly installs: `0.0.0.0/0 dev vtiX table 220`
   - Kernel may keep: `169.254.* routes on eth0` unless explicitly deleted

4. **Cloud-init/DHCP residuals**:
   - Ubuntu's networking stack may add: `169.254.0.0/16 dev eth0 scope link`
   - Must be removed unless it's the metadata route

**Why Orphaned Routes Are Dangerous:**

If left untouched, orphaned routes can:

- Misroute BGP replies (causing session failures)
- Override intended routing paths
- Create hairpin loops inside the VM
- Silently bypass IPsec for APIPA traffic
- Cause FRR BGP to choose the wrong interface/source
- Route tunnel traffic out eth0 instead of vtiX

**Bottom Line:** Orphans = state drift = bugs. The routing guard prevents this automatically.

#### Implementation: Explicit Scoping

The routing guard (`routing_guard.py`) implements explicit ownership boundaries for APIPA routes:

**We Own (Tunnel APIPA):**

- Tunnel CIDR routes: `169.254.x.x/30` connected routes from VTI IP assignments (`inner_cidr` in YAML)
- Tunnel peer routes: `169.254.x.x/32` BGP peer routes (`inner_remote_ip` in YAML)
- Tracked in `expected_tunnel_cidrs` and `expected_tunnel_peers` dictionaries
- Only routes matching YAML + correct VTI interface are preserved

**Cloud Owns (Metadata APIPA):**

- Metadata routes: `169.254.169.0/24` for cloud platform APIs
- Examples: `169.254.169.1` (DHCP gateway), `169.254.169.254` (metadata service)
- Explicitly whitelisted via `CLOUD_METADATA_PREFIX = "169.254.169."`
- NEVER touched by routing guard

**Everything Else Gets Deleted:**

- Any APIPA route in `169.254.0.0/16` that doesn't match the above categories
- Wrong VTI interface (route exists but uses wrong device)
- APIPA prefixes not defined in YAML
- Routes on eth0 instead of VTI (except metadata)

#### Cleanup Algorithm

The `_cleanup_unexpected_apipa_routes()` function implements the following logic:

```python
# 1. Build expected route sets from YAML
for each active tunnel in YAML:
    expected_tunnel_cidrs[inner_cidr] = vti_name
    expected_tunnel_peers[inner_remote_ip] = vti_name

# 2. Scan all kernel routes
for each route in kernel routing table:
    if route contains "169.254.":
        # Skip metadata (cloud-owned)
        if route.startswith("169.254.169."):
            continue
        
        # Check if expected (YAML-defined + correct VTI)
        if route matches expected_tunnel_cidrs OR expected_tunnel_peers:
            continue  # Keep it
        
        # Unexpected route found - mark for deletion
        routes_to_remove.append(route)

# 3. Remove all unexpected routes
for each unexpected route:
    ip route del <prefix> dev <interface>
```

**Key Features:**

- **Idempotent:** Safe to run multiple times (uses `ip route replace` for additions)
- **Deterministic:** Same YAML always produces same routing table
- **Self-healing:** Automatically corrects drift on every agent startup/reload
- **Safe:** Whitelist protection prevents deleting cloud metadata routes

#### When Cleanup Runs

The routing guard enforces invariants automatically:

1. **On agent startup** (system boot)
2. **On agent reload** (SIGHUP signal after config push)
3. **After every configuration change**
4. **Optionally via timer** (future enhancement for continuous enforcement)

**Result:** The routing table always matches the YAML configuration.

#### Examples of Routes That Get Deleted

**1. Wrong VTI interface:**

```bash
# Route exists but uses wrong device per YAML
169.254.18.225 dev vti1  # Should be vti0 per YAML → DELETED
```

**2. Leftover from deleted tunnel:**

```bash
# Tunnel removed from YAML but route remains
169.254.5.152/30 dev vti2  # Tunnel no longer in YAML → DELETED
```

**3. Routes on eth0 instead of VTI:**

```bash
# APIPA route on management interface (not metadata)
169.254.18.224/30 dev eth0  # Should be dev vti0 → DELETED
```

**4. APIPA prefixes not in config:**

```bash
# Arbitrary APIPA route not defined in YAML
169.254.99.0/24 dev vti0  # Not in YAML → DELETED
```

#### Examples of Routes That Are Preserved

**1. Metadata routes (cloud-owned):**

```bash
169.254.169.1 dev eth0                      # DHCP gateway → PRESERVED
169.254.169.254 via 169.254.169.1 dev eth0  # Metadata API → PRESERVED
default via 169.254.169.1 dev eth0          # DHCP default → PRESERVED
```

**2. Tunnel CIDR routes (YAML-defined):**

```bash
# YAML: inner_cidr: "169.254.18.224/30"
169.254.18.224/30 dev vti0 proto kernel  # Connected route → PRESERVED
```

**3. Tunnel peer routes (YAML-defined):**

```bash
# YAML: inner_remote_ip: "169.254.18.225/30"
169.254.18.225/32 dev vti0  # BGP peer route → PRESERVED
```

#### Structured Logging & Observability

After each enforcement cycle, the routing guard logs comprehensive metrics:

```text
[RoutingGuard] Summary: table_220_removed=False broad_apipa_removed=False 
  orphaned_apipa_removed=0 bgp_peer_routes_ensured=2
```

**Metrics Tracked:**

- `table_220_removed` (bool): Policy routing rule cleaned up
- `broad_apipa_removed` (bool): Misconfigured broad routes removed
- `orphaned_apipa_removed` (int): Count of stale tunnel routes deleted
- `bgp_peer_routes_ensured` (int): BGP peer /32 routes verified/added

**Benefits:**

- Enables monitoring and alerting (track orphan rate over time)
- Facilitates debugging without verbose per-route logging
- Provides actionable metrics for SRE teams
- Detects configuration drift patterns

#### Integration with Status Command

The `nebius-vpngw status` command displays routing health alongside tunnel/BGP status:

```text
Routing Table Health:
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Gateway VM      ┃ Table 220 ┃ Broad APIPA ┃ Tunnel Routes   ┃ Overall ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ nebius-vpn-gw-0 │ OK        │ OK          │ 5 routes        │ Healthy │
└─────────────────┴───────────┴─────────────┴─────────────────┴─────────┘
```

**Health Indicators:**

- **Table 220:** Should show `OK` (green); `EXISTS` (red) indicates policy routing issue
- **Broad APIPA:** Should show `OK` (green); `EXISTS` (red) indicates 169.254.0.0/16 misconfiguration
- **Tunnel Routes:** Count of APIPA routes (informational, not pass/fail)
- **Overall:** Aggregated health (`Healthy`, `Warning`, or `Issues Found`)

The "Tunnel Routes" count includes:

- Tunnel subnet routes (2× `/30` connected routes for 2 tunnels)
- BGP peer routes (2× `/32` routes for 2 peers)
- DHCP default route (1× `default via 169.254.169.1`)

**Example for HA setup with 2 tunnels:** 5 routes = 2×(subnet + peer) + 1×default = expected/legitimate.

#### Sanity Check Script

A standalone verification tool (`agent/sanity_check.py`) can be run manually on the VM:

```bash
# On gateway VM
python3 -m nebius_vpngw.agent.sanity_check

# Output
🔍 Checking for table 220...
✅ PASS: Table 220 not found

🔍 Checking BGP peer routes...
✅ PASS: 169.254.18.225 → vti0
✅ PASS: 169.254.5.153 → vti1

🔍 Checking for orphaned routes...
✅ PASS: No orphaned routes found

============================================================
✅ All routing invariants satisfied!
```

**Use Cases:**

- Pre-deployment validation
- Manual troubleshooting
- CI/CD integration for config validation
- Compliance verification

#### Production Benefits

This declarative route management provides:

✅ **Deterministic:** Routing table always matches YAML configuration
✅ **Idempotent:** Safe to run multiple times without side effects
✅ **Self-healing:** Automatically fixes drift from desired state
✅ **Observable:** Structured logging enables monitoring/alerting
✅ **Safe:** Whitelist protection for cloud metadata routes
✅ **Aggressive:** Removes any unexpected routes that could cause bugs
✅ **Production-grade:** Meets enterprise reliability requirements

**Summary:** The routing guard makes your VPN gateway infrastructure-as-code compliant, ensuring the actual routing state always reflects the declared configuration.

## 18. Project Structure

The codebase is organized into distinct modules with clear separation of concerns:

```text
├── nebius-vpngw.config.yaml              # Main user configuration
├── src/nebius_vpngw/
│   ├── __main__.py                       # Python module entry point
│   ├── cli.py                            # CLI orchestrator (nebius-vpngw command)
│   ├── config_loader.py                  # YAML parser and peer config merger
│   ├── schema.py                         # Pydantic schema for YAML config validation
│   ├── build.py                          # Binary build utilities
│   ├── vpngw_sa.py                       # Service account management
│   ├── agent/
│   │   ├── main.py                       # On-VM agent daemon
│   │   ├── frr_renderer.py               # FRR/BGP config renderer
│   │   ├── strongswan_renderer.py        # strongSwan/IPsec config renderer
│   │   ├── routing_guard.py              # Declarative route management & cleanup
│   │   ├── firewall_manager.py           # UFW firewall rule synchronization
│   │   ├── tunnel_iterator.py            # Centralized tunnel enumeration
│   │   ├── state_store.py                # Agent state persistence
│   │   ├── status_check.py               # Tunnel/BGP/service health checks
│   │   └── sanity_check.py               # Routing invariant validation tool
│   ├── deploy/
│   │   ├── vm_manager.py                 # VM lifecycle management (create/delete/recreate)
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
│       ├── ipsec-vti.sh                  # VTI interface creation script (strongSwan updown)
│       ├── fix-routes.sh                 # Route cleanup utility script
│       ├── nebius-vpngw-fix-routes.service  # Route fix systemd service
│       └── nebius-vpngw-fix-routes.timer    # Route fix systemd timer
```

### Module Descriptions

**Orchestrator (runs on operator machine):**

- `cli.py`: Main entry point for the `nebius-vpngw` command. Orchestrates VM provisioning, config deployment, and status checks.
- `config_loader.py`: Parses YAML configuration, merges peer configs, expands environment variables, validates schema.
- `schema.py`: Pydantic models for strict YAML config validation with type checking, field constraints, and logical consistency verification.
- `vpngw_sa.py`: Manages Nebius service account lifecycle for API authentication.
- `build.py`: Utilities for building standalone binaries (PyInstaller).

**Agent (runs on gateway VM):**

- `main.py`: Agent daemon that renders and applies strongSwan/FRR configurations, handles SIGHUP reload signals.
- `frr_renderer.py`: Generates FRR BGP configuration (`bgpd.conf`) from YAML tunnel definitions.
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration (`ipsec.conf`, `ipsec.secrets`) and manages VTI interfaces.
- `routing_guard.py`: Enforces routing table invariants (removes table 220, cleans orphaned routes, ensures BGP peer /32 routes).
- `firewall_manager.py`: Synchronizes UFW firewall rules with active tunnel peer IPs.
- `tunnel_iterator.py`: Centralized iterator for active tunnels ensuring consistent VTI index mapping across all modules.
- `state_store.py`: Persists last-applied configuration to `/etc/nebius-vpngw/last-applied.json` for idempotency.
- `status_check.py`: Collects tunnel/BGP/service health metrics for status command.
- `sanity_check.py`: Standalone validation tool for verifying routing invariants (table 220, BGP routes, orphaned routes).

**Deployment:**

- `vm_manager.py`: Manages VM lifecycle using Nebius SDK (create, delete, recreate with IP preservation).
- `vm_diff.py`: Detects VM configuration changes requiring recreation vs reload.
- `route_manager.py`: Manages VPC static routes (used in static routing mode, not needed for BGP).
- `ssh_push.py`: Deploys agent package and configuration to VMs via SSH/SFTP, triggers agent reload.

**Peer Config Parsers:**

- `gcp.py`: Parses GCP HA VPN configuration exports (peer IPs, ASNs, shared secrets).
- `aws.py`: Parses AWS Site-to-Site VPN configuration downloads.
- `azure.py`: Parses Azure VPN Gateway configuration exports.
- `cisco.py`: Parses Cisco IOS configuration snippets.

**Systemd Resources:**

- `nebius-vpngw-agent.service`: Systemd unit for agent daemon (runs on VM boot and handles SIGHUP reload).
- `ipsec-vti.sh`: strongSwan updown script that creates VTI interfaces using kernel's native support (workaround for missing VTI plugin in Ubuntu).
- `fix-routes.sh`: Utility script for manual route cleanup (usually not needed as routing_guard handles this automatically).
- `nebius-vpngw-fix-routes.{service,timer}`: Optional systemd timer for periodic route enforcement (future enhancement).

## 19. References

- Diagrams: `image/vpngw-architecture.dot`, `image/vpngw-conn-diagram.dot` (render with Graphviz).
- README: quick start, packaging, build instructions (root `README.md`).
