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

## 7. Workflows & CLI

- Command: `nebius-vpngw` (or `python -m nebius_vpngw`).
- Typical apply: parse args → load YAML and peer configs → merge and validate → ensure network/subnet → ensure VMs + allocations → push per-VM config over SSH → `systemctl reload nebius-vpngw-agent` → reconcile static routes (if static mode).
- Flags: `--local-config-file`, repeatable `--peer-config-file`, `--recreate-gw`, `--project-id`, `--zone`, `--dry-run`.
- Peer import: vendor parsers (GCP/AWS/Azure/Cisco) normalize templates; merger fills only missing fields—YAML topology is never overridden.

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

## 17. References

- Diagrams: `image/vpngw-architecture.dot`, `image/vpngw-conn-diagram.dot` (render with Graphviz).
- README: quick start, packaging, build instructions (root `README.md`).
