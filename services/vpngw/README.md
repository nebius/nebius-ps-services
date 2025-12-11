# Nebius VPN Gateway (VM-based)

A modular Python-based orchestrator and agent to provision Nebius VMs as Site-to-Site IPsec VPN gateways (compatible with GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, and on-premises routers).

## Features

- **IPsec (strongSwan)**: IKEv2/IKEv1, AES-256, SHA-256/384/512, DH groups 14/20/24
- **Routing modes**: BGP (FRR) and static routing
- **High availability**: Single-VM or multi-VM gateway groups
- **Configuration**: YAML-driven with optional peer config import from cloud providers
- **Automation**: Idempotent agent automatically applies and maintains configurations
- **Security hardening**: SSH hardening, fail2ban, UFW firewall, auditd, automated security updates
- **Production monitoring**: Routing health checks, structured logging with metrics, service status

## Table of Contents

- [Quick Start](#quick-start)
- [Security](#security)
- [Configuration](#configuration)
  - [Schema Validation](#schema-validation)
  - [Configuration File Structure](#configuration-file-structure)
- [CLI Usage](#cli-usage)
- [Monitoring and Troubleshooting](#monitoring-and-troubleshooting)
- [Advanced Options](#advanced-options)
- [Development](#development)
- [Project Structure](#project-structure)
- [License](#license)

## Quick Start

### Prerequisites

- Python 3.10–3.12
- Nebius account with API access

### Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/nebius/nebius-ps-services.git
   cd nebius-ps-services/services/vpngw
   ```

2. **Install using pip (recommended):**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -U pip wheel
   pip install -e .
   ```

3. **Verify installation:**

   ```bash
   nebius-vpngw --help
   ```

### Authentication

Ensure you're authenticated with Nebius CLI:

```bash
# If not already logged in
nebius login

# Set environment variables
export PROJECT_ID="your-project-id"
export REGION_ID="eu-north1"
```

The CLI automatically uses your Nebius CLI authentication token.

### First Deployment

1. **Generate configuration template:**

   ```bash
   nebius-vpngw
   # Creates ./nebius-vpngw.config.yaml from template
   ```

2. **Edit configuration file:**

   ```yaml
   # nebius-vpngw.config.yaml (minimal example)
   gateway_group:
     name: vpngw
     instance_count: 1
     vm_spec:
       ssh_public_key_path: "~/.ssh/id_ed25519.pub"
   ```

3. **Deploy gateway:**

   ```bash
   nebius-vpngw apply \
     --local-config-file ./nebius-vpngw.config.yaml \
     --project-id "$PROJECT_ID" \
     --zone "${REGION_ID}-a"
   ```

4. **Check tunnel status:**

   ```bash
   nebius-vpngw status --local-config-file ./nebius-vpngw.config.yaml
   ```

   Or simply (if config exists in current directory):

   ```bash
   nebius-vpngw status
   # Or just: nebius-vpngw (status is the default action)
   ```

## Security

Gateway VMs include comprehensive security hardening applied automatically during VM creation:

### Security Features

- **SSH Hardening:**
  - Key-only authentication (passwords disabled)
  - Root login disabled
  - Maximum 3 authentication attempts
  - Verbose logging for security audits

- **Intrusion Prevention:**
  - Fail2ban monitoring SSH authentication
  - 3 failed attempts trigger 1-hour IP ban
  - Automatic blocking/unblocking

- **UFW Firewall (VPN-Safe):**
  - Default deny incoming on management interface (eth0)
  - Explicit allow for IPsec: UDP 500, 4500, ESP protocol
  - SSH restricted to management CIDRs (optional)
  - **VTI/XFRM interfaces NOT filtered** - BGP flows freely
  - Dynamic firewall updates synchronized with config changes

- **Audit Logging (auditd):**
  - All command executions logged
  - Configuration file monitoring (`/etc/nebius-vpngw/`, `/etc/swanctl/`, `/etc/frr/`)
  - Tamper detection and forensics capability

- **System Hardening:**
  - IP forwarding enabled for VPN
  - ICMP redirects disabled (routing attack prevention)
  - Martian packet logging (spoofing detection)
  - SYN cookies enabled (SYN flood protection)

- **Automated Security Updates:**
  - Unattended security patches
  - Minimized reboot frequency
  - Restart monitoring alerts

### Routing Health Monitoring

The agent includes production-grade routing guard with:

- **Explicit APIPA scoping:** Distinguishes tunnel routes from cloud metadata routes
- **Policy routing protection:** Removes table 220 rules that cause asymmetric routing
- **Structured logging:** Metrics for table_220_removed, orphaned_routes, bgp_peer_routes
- **Health checks:** Integrated into status command (no additional flags required)

### Security Best Practices

**Management Access:**

```yaml
gateway_group:
  management_cidrs:
    - 10.0.0.0/8  # Your corporate network
    - 203.0.113.0/24  # Your VPN range
```

This restricts SSH access to specified CIDRs. Omit `management_cidrs` to allow SSH from anywhere (not recommended for production).

**Secret Management:**

Never commit secrets to version control. Use environment variables:

```bash
export GCP_TUNNEL_1_PSK="your-secure-psk"
export GCP_TUNNEL_2_PSK="your-secure-psk"
```

Reference in config:

```yaml
tunnels:
  - name: gcp-tunnel-1
    psk: ${GCP_TUNNEL_1_PSK}
```

**VM Recreation for Full Hardening:**

Security hardening is applied via cloud-init at VM creation. To apply hardening to existing VMs:

```bash
nebius-vpngw apply --recreate-gw --local-config-file ./nebius-vpngw.config.yaml
```

**Note:** Public IPs are preserved during recreation, but tunnels will experience downtime.

## Configuration

### Schema Validation

The configuration file is validated against a strict, versioned schema that:

- **Rejects unknown fields** - Catches typos like `inner_ciddr` instead of `inner_cidr`
- **Enforces types** - Ensures numbers are numbers, IPs are valid, CIDRs are correct
- **Validates constraints** - ASN ranges (64512-65534), /30 subnets, APIPA ranges
- **Checks consistency** - BGP mode requires `remote_asn`, static mode forbids it
- **Verifies quotas** - Total connections and tunnels within limits

**Validate before deploying:**

```bash
nebius-vpngw validate-config nebius-vpngw.config.yaml
```

**Note:** The config file is passed as a positional argument, not using `--local-config-file`.

**Example validation output:**

```text
✓ Configuration is valid!

Summary:
  • Gateway instances: 1
  • Connections: 2
  • Tunnels: 3
  • Schema version: v1
```

**Common validation errors:**

```text
Configuration validation failed:
  • connections -> 0 -> tunnels -> 0 -> inner_cidr: inner_cidr '192.168.1.0/30' must be in APIPA range 169.254.0.0/16
  • connections -> 1 -> bgp -> remote_asn: remote_asn is required when BGP is enabled
  • gateway -> local_asn: ASN 65535 is invalid. Use private ASN (64512-65534) or public ASN (1-64511)
```

The schema validation runs automatically during `nebius-vpngw apply`, but using `validate-config` first helps catch errors early without deployment overhead.

### Configuration File Structure

The main configuration file (`nebius-vpngw.config.yaml`) contains:

- **gateway_group**: VM specifications, networking, public IPs
- **gateway**: Local ASN, prefixes, quotas
- **defaults**: Default IPsec and BGP parameters
- **connections**: Tunnel definitions with peer details

### Network Configuration

**VPC Network:**

- Specify network via `network_id` in gateway_group (optional - defaults to your default VPC)
- Gateway VMs are created in `vpngw-subnet` (auto-created /27 CIDR)
- Platform constraint: 1 NIC per VM with 1 public IP

**Public IP Allocations:**

```yaml
gateway_group:
  # Auto-create allocations (if omitted or empty)
  external_ips: []

  # Use existing allocations
  external_ips:
  - 203.0.113.10  # Replace with your actual public IP
```

Public IPs are preserved during VM recreation.

### SSH Configuration

```yaml
gateway_group:
  vm_spec:
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"  # Auto-reads file content
    ssh_username: ubuntu  # Default: ubuntu
    ssh_private_key_path: "~/.ssh/id_ed25519"  # Optional, uses SSH agent if omitted
```

### Environment Variables

Use environment variables for sensitive values:

```yaml
tunnels:
  - name: gcp-tunnel-1
    psk: ${GCP_TUNNEL_1_PSK}  # Set via: export GCP_TUNNEL_1_PSK="your-secret"
```

**Important:** Do not commit sensitive values to version control.

### Peer Configuration Import

Import tunnel details from cloud provider configurations:

```bash
nebius-vpngw apply \
  --local-config-file ./nebius-vpngw.config.yaml \
  --peer-config-file ./gcp-ha-vpn-config.txt \
  --peer-config-file ./aws-vpn-config.txt
```

Peer config files automatically populate missing tunnel details (PSKs, IPs, crypto proposals) without changing your topology.

Supported vendors:

- GCP HA VPN
- AWS Site-to-Site VPN
- Azure VPN Gateway
- Cisco IOS

## CLI Usage

### Commands

**Validate configuration file (recommended before deployment):**

```bash
nebius-vpngw validate-config nebius-vpngw.config.yaml
```

This validates your configuration against the schema without deploying. Use this to catch errors early:

- Typos in field names
- Invalid IP addresses or CIDRs
- Incorrect ASN ranges
- Missing required fields for BGP/static modes
- Quota violations

**Deploy or update gateway:**

```bash
nebius-vpngw apply --local-config-file ./nebius-vpngw.config.yaml
```

Note: Validation runs automatically during `apply`, but running `validate-config` first helps catch errors without deployment overhead.

**View tunnel status and system health (default action):**

```bash
nebius-vpngw status --local-config-file ./nebius-vpngw.config.yaml
# Or simply: nebius-vpngw (status is the default if config exists)
```

**List VPC routes:**

```bash
nebius-vpngw list-routes --local-config-file ./nebius-vpngw.config.yaml
```

**Add static routes to VPC:**

```bash
nebius-vpngw add-routes --local-config-file ./nebius-vpngw.config.yaml
```

**Note:** The CLI automatically uses your Nebius CLI authentication (via `nebius login`). Token is fetched automatically from Nebius CLI configuration.

### VM Recreation

Some configuration changes require VM recreation (e.g., changing CPU, memory, boot disk type):

```bash
nebius-vpngw apply --recreate-gw --local-config-file ./nebius-vpngw.config.yaml
```

**Warning:** Recreation causes downtime. Public IPs are preserved and reassigned.

### Configuration Refresh

The `apply` command always pushes the resolved YAML config and reloads the agent, even when no diff is detected:

- Checks VM diffs first; stops if destructive changes are needed unless `--recreate-gw` is passed
- Agent won't rewrite configs if desired state matches last applied (idempotent)
- Safe for refreshing with current config (doubles as restart)

## Monitoring and Troubleshooting

### Checking Tunnel Status

View active tunnels and system health:

```bash
nebius-vpngw status --local-config-file ./nebius-vpngw.config.yaml
```

Example output:

```text
                                  VPN Gateway Status                                   
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Tunnel     ┃ Gateway VM ┃ Status     ┃ BGP    ┃ Peer IP    ┃ Encrypti… ┃ Uptime     ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ gcp-ha-tu… │ nebius-vp… │ Establish… │ Active │ 34.157.14… │ AES_GCM_… │ 30 minutes │
│ gcp-ha-tu… │ nebius-vp… │ Establish… │ Active │ 34.157.15… │ AES_GCM_… │ 36 minutes │
└────────────┴────────────┴────────────┴────────┴────────────┴───────────┴────────────┘

Checking system services...
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┓
┃ Gateway VM      ┃ Agent  ┃ StrongSwan ┃ FRR    ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━┩
│ nebius-vpn-gw-0 │ active │ active     │ active │
└─────────────────┴────────┴────────────┴────────┘

Routing Table Health:
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Gateway VM      ┃ Table 220 ┃ Broad APIPA ┃ Orphaned Routes ┃ Overall ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ nebius-vpn-gw-0 │ OK        │ OK          │ 5 routes        │ Healthy │
└─────────────────┴───────────┴─────────────┴─────────────────┴─────────┘
```

Output includes:

- **Tunnel status:** name, gateway VM, state (ESTABLISHED/CONNECTING), BGP state, peer IP, encryption, uptime
- **Service health:** `nebius-vpngw-agent`, `strongswan`, `frr` status
- **Routing health:** Table 220 check, broad APIPA detection, orphaned routes count, overall status

### Configuration Files on Gateway VMs

**IPsec (strongSwan):**

- `/etc/swanctl/conf.d/*.conf` - Tunnel configurations (auto-generated by agent)
- `/etc/swanctl/swanctl.conf` - Main strongSwan configuration
- `/etc/strongswan.conf` - Daemon settings

**BGP (FRR):**

- `/etc/frr/frr.conf` - BGP configuration (auto-generated by agent)
- `/etc/frr/daemons` - Enabled daemons

**Agent:**

- `/etc/nebius-vpngw-agent.yaml` - Instance-specific config
- `/etc/nebius-vpngw/last-applied.json` - Last applied state
- `/var/log/nebius-vpngw-agent.log` - Agent logs

**Services:**

- `nebius-vpngw-agent.service` - Config renderer and service manager
- `strongswan-starter.service` - IPsec daemon
- `frr.service` - Routing daemon

### Common Issues

#### Tunnel Not Establishing

**Symptoms:** Status shows `CONNECTING` or no connection

**Diagnosis:**

```bash
# Check IPsec status
ssh ubuntu@<gateway-ip> 'sudo ipsec statusall'

# Verify XFRM policies installed
ssh ubuntu@<gateway-ip> 'sudo ip xfrm policy'
# Should show: 10.49.0.0/16 <-> 10.10.0.0/24 (your configured subnets)

# View logs
ssh ubuntu@<gateway-ip> 'sudo journalctl -u strongswan-starter -n 100'
```

**Common errors and fixes:**

| Error | Cause | Solution |
|-------|-------|----------|
| `no IKE config found` | Wrong local IP | For responder mode: use `left=%any`. For initiator mode: use `left=<external-ip>` |
| `INVALID_SYNTAX` | Crypto mismatch | Update `ike_proposals` and `esp_proposals` to match peer |
| `AUTHENTICATION_FAILED` | Wrong PSK | Verify PSK is identical (case-sensitive). Check env var: `echo $GCP_TUNNEL_PSK` |

**Firewall requirements:**

- Allow UDP 500 (IKE) and 4500 (NAT-T) from peer IP
- Allow ESP (IP protocol 50) if not using NAT-T

#### BGP Session Not Establishing

**Quick check:**

```bash
ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show bgp summary"'
# Look for: State = Established, PfxRcvd > 0
```

**Step-by-step diagnosis:**

1. **Verify IPsec tunnels are ESTABLISHED:**

   ```bash
   ssh ubuntu@<gateway-ip> 'sudo ipsec status'
   # All tunnels must show ESTABLISHED before BGP can work
   ```

2. **Test BGP peer connectivity:**

   ```bash
   ssh ubuntu@<gateway-ip> 'ping -c 3 169.254.X.X'  # Use BGP peer IP
   ```

   **If ping fails:**

   Check interface type (must be XFRM, not VTI):

   ```bash
   ssh ubuntu@<gateway-ip> 'ip -d link show xfrm0 | head -3'
   # Should show: "xfrm" type with parent "@eth0"
   # WRONG: "vti" type (indicates VTI instead of XFRM)
   ```

   **Root cause:** VTI interfaces with strongSwan `mark=` parameter do not encrypt outbound traffic. Only XFRM interfaces with `if_id=` work correctly.

   **Verification:**

   ```bash
   # Check strongSwan config uses if_id (correct):
   ssh ubuntu@<gateway-ip> 'sudo grep -E "if_id_in|if_id_out|mark=" /etc/swanctl/conf.d/*.conf'
   # Should show: if_id_in=100 and if_id_out=100
   # WRONG: mark=100 (old VTI configuration)

   # Verify bidirectional ESP traffic:
   ssh ubuntu@<gateway-ip> 'sudo timeout 5 tcpdump -i eth0 -c 10 esp 2>&1 | grep -E "ESP|packets"'
   # Should show packets in BOTH directions (not just incoming)
   ```

3. **Check for BGP OPEN errors (ASN mismatch):**

   ```bash
   ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show ip bgp neighbor 169.254.X.X"'
   # Look for: "Last reset" line showing "Bad Peer AS" or "OPEN Message Error"
   ```

   **Fix:** Update `remote_asn` in YAML config to match peer's actual ASN.

4. **Check for policy blocking (FRR 8.4+):**

   ```bash
   ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show ip bgp neighbor 169.254.X.X" | grep -i policy'
   # If you see "(Policy)" or "discarded due to missing policy"
   ```

   **Fix:** Add `no bgp ebgp-requires-policy` to BGP config (included in frr_renderer.py by default).

5. **Verify routes are being received:**

   ```bash
   ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show ip bgp"'
   # Should show routes from peer with path info
   ```

6. **Check route installation (FRR 8.4.4 bug):**

   ```bash
   ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show ip route"'
   # Look for BGP routes marked with "B" and "*" (installed in FIB)

   ssh ubuntu@<gateway-ip> 'ip route get 10.10.0.1'
   # Should route via BGP next-hop (169.254.X.X)
   ```

   **Known bug:** FRR 8.4.4 (Ubuntu 24.04 default) may mark routes as "inactive" and not install them to kernel.

   **Solution:** Upgrade to FRR 10.x:

   ```bash
   curl -s https://deb.frrouting.org/frr/keys.asc | sudo tee /usr/share/keyrings/frrouting.asc
   echo "deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr noble frr-stable" | sudo tee /etc/apt/sources.list.d/frr.list
   sudo apt update && sudo apt install frr=10.5.0-0~ubuntu24.04.1
   sudo systemctl restart frr
   ```

**BGP troubleshooting reference:**

| Symptom | Cause | Solution |
|---------|-------|----------|
| `State = Idle`, zero messages | BGP peer unreachable | Verify IPsec ESTABLISHED, test ping to peer IP |
| `State = Active`, trying to connect | Wrong peer IP or firewall | Check `inner_remote_ip`, verify firewall allows TCP/179 |
| `OPEN Message Error/Bad Peer AS` | ASN mismatch | Update `remote_asn` to match peer's ASN |
| `State = Established` but `(Policy)` | FRR policy requirement | Verify `no bgp ebgp-requires-policy` in config |
| Routes received but PfxRcd = 0 | Route filtering or policy | Check route-maps, run `clear ip bgp * soft in` |
| BGP routes in RIB but not in kernel | Nexthop unresolved or FRR bug | Check `ip route show table all`, upgrade FRR |

**Force BGP session reset:**

```bash
ssh ubuntu@<gateway-ip> 'sudo vtysh -c "clear ip bgp *"'
```

#### Configuration Not Updating

**Verify agent received config:**

```bash
ssh ubuntu@<gateway-ip> 'cat /etc/nebius-vpngw-agent.yaml'
```

**Check agent is running:**

```bash
ssh ubuntu@<gateway-ip> 'sudo systemctl status nebius-vpngw-agent'
ssh ubuntu@<gateway-ip> 'sudo journalctl -u nebius-vpngw-agent -n 50'
```

**If you modified agent code:**

```bash
# Rebuild wheel before deploying
poetry build
nebius-vpngw --local-config-file ./nebius-vpngw.config.yaml
```

### Debug Logging

Enable detailed IPsec logging:

```bash
ssh ubuntu@<gateway-ip>
sudo nano /etc/strongswan.conf
```

Add this section:

```ini
charon {
    filelog {
        stderr {
            default = 1
            ike = 2
            cfg = 2
            knl = 2
        }
    }
}
```

Restart and view logs:

```bash
sudo systemctl restart strongswan-starter
sudo journalctl -u strongswan-starter -f
```

**Note:** Debug logging is verbose and may impact performance. Disable after troubleshooting.

### Manual Verification Commands

```bash
# IPsec status
sudo ipsec status                  # Brief
sudo ipsec statusall              # Detailed
sudo swanctl --list-sas           # List security associations
sudo ipsec reload                 # Reload config without restart

# XFRM (Linux kernel IPsec)
sudo ip xfrm policy               # View policies
sudo ip xfrm state                # View security associations
sudo ip link show type xfrm       # Show XFRM interfaces

# BGP (if using BGP mode)
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show ip route bgp"
sudo vtysh -c "show ip bgp neighbor <peer-ip>"

# Services
sudo systemctl status nebius-vpngw-agent
sudo systemctl status strongswan-starter
sudo systemctl status frr

# Logs
sudo journalctl -u nebius-vpngw-agent -n 100
sudo journalctl -u strongswan-starter -n 100
sudo journalctl -u frr -n 100

# Connectivity
ping <peer-subnet-ip>
traceroute <peer-subnet-ip>
```

## Advanced Options

This section covers advanced CLI flags and features for specific use cases.

### Dry-run Mode

Preview what changes would be applied without actually making them:

```bash
nebius-vpngw apply --local-config-file ./nebius-vpngw.config.yaml --dry-run
```

**Use cases:**

- Validating configuration changes before deployment
- CI/CD pipeline testing
- Understanding what resources will be created/modified

**What it does:**

- Parses and validates YAML configuration
- Merges peer configs and resolves environment variables
- Shows a summary of planned actions
- Exits without creating VMs or pushing configs

**Example output:**

```text
Dry-run: showing summary of actions

Gateway Group: vpngw
  Instances: 1
  VM Spec: 8 cores, 16GB RAM, 50GB boot disk
  Network: default-vpc / vpngw-subnet
  Connections: 2 (gcp-ha-tunnel-1, gcp-ha-tunnel-2)
  Tunnels: 2 active BGP tunnels
```

### Service Account Authentication

Create and use a Nebius service account for authentication (useful for CI/CD or automation):

```bash
nebius-vpngw apply \
  --local-config-file ./nebius-vpngw.config.yaml \
  --sa nb-vpngw-sa \
  --project-id "$PROJECT_ID" \
  --zone "${REGION_ID}-a"
```

**What it does:**

1. Creates a service account named `nb-vpngw-sa` if it doesn't exist
2. Assigns Editor permissions to the service account
3. Generates and uses a temporary access token for this deployment
4. Token is automatically injected into `NEBIUS_IAM_TOKEN` environment variable

**Use cases:**

- **CI/CD pipelines:** Automate deployments without interactive login
- **Automation scripts:** Run deployments from cron jobs or orchestration tools
- **Multi-account management:** Different service accounts for different environments

**When NOT to use:**

- Regular developer workflows (use `nebius login` instead)
- Local testing and debugging
- Interactive deployments

**Security considerations:**

- Service account has Editor permissions on the project
- Access tokens are temporary and expire after the session
- Consider using more restrictive roles for production (custom role with minimal permissions)
- Store service account credentials securely (use secrets managers in CI/CD)

**Example CI/CD usage (GitHub Actions):**

```yaml
- name: Deploy VPN Gateway
  env:
    PROJECT_ID: ${{ secrets.NEBIUS_PROJECT_ID }}
    REGION_ID: eu-north1
  run: |
    nebius-vpngw apply \
      --local-config-file ./nebius-vpngw.config.yaml \
      --sa github-actions-vpngw \
      --project-id "$PROJECT_ID" \
      --zone "${REGION_ID}-a"
```

**Note:** Both `--sa` and `--dry-run` flags are hidden from `--help` output to keep the standard usage simple. They remain fully functional for advanced use cases.

## Development

This section is for contributors and maintainers.

### Setup Development Environment

Install with Poetry (recommended for development):

```bash
poetry install --with dev
poetry run nebius-vpngw --help
```

Or activate the virtualenv:

```bash
eval "$(poetry env activate zsh)"
nebius-vpngw --help
```

### Code Quality

**Linting and formatting with Ruff:**

```bash
# Check for issues
poetry run ruff check .

# Auto-fix safe issues
poetry run ruff check . --fix

# Format code (Black-compatible)
poetry run ruff format .
```

**Pre-commit hooks (optional):**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format
```

Install hooks:

```bash
pip install pre-commit
pre-commit install
```

### Building and Distribution

#### Python Package (Wheel)

Build distributable package:

```bash
poetry build
# Creates: dist/nebius-vpngw-0.1.0-py3-none-any.whl
```

Install with pipx (system-wide isolated environment):

```bash
pipx install .
nebius-vpngw --help
```

**Lock dependencies for reproducibility:**

```bash
poetry lock
git add poetry.lock && git commit -m "Lock dependencies"
```

#### Single-File Binary (PyInstaller)

Build standalone executable (no Python required on target system):

```bash
poetry install
poetry run build-binary
# Creates: dist/nebius-vpngw
```

**Advanced PyInstaller options:**

```bash
# Custom build with hidden imports and bundled assets
poetry run pyinstaller -F -n nebius-vpngw \
  --hidden-import some_module \
  --add-data "image/*:image" \
  src/nebius_vpngw/__main__.py
```

**macOS distribution checklist:**

1. **Codesign:**

   ```bash
   codesign --force --options runtime \
     --sign "Developer ID Application: Your Org (TEAMID)" \
     dist/nebius-vpngw
   ```

2. **Verify:**

   ```bash
   codesign --verify --deep --strict dist/nebius-vpngw
   spctl --assess --verbose=4 dist/nebius-vpngw
   ```

3. **Notarize:**

   ```bash
   xcrun notarytool submit dist/nebius-vpngw \
     --keychain-profile "notary-profile" --wait
   ```

**Troubleshooting binary builds:**

- Resources not found: Add `--add-data` and use `importlib.resources`
- Styling issues: Set `TERM=xterm-256color`
- Custom flags: Edit `nebius_vpngw/build.py` directly

### Agent Development

The agent runs on gateway VMs and renders IPsec/FRR configurations.

**Agent workflow:**

1. Build wheel: `poetry build` → Creates `dist/nebius_vpngw-*.whl`
2. Deploy: CLI uploads wheel to VMs via SSH and installs it

**After modifying agent code:**

```bash
# Clean old artifacts
rm -rf dist/*.whl

# Rebuild wheel
poetry build

# Deploy to VMs
nebius-vpngw --local-config-file ./nebius-vpngw.config.yaml
```

**Note:** The wheel is NOT installed in your local virtualenv—only on remote VMs.

The agent:

- Reads `/etc/nebius-vpngw-agent.yaml` (pushed by orchestrator)
- Renders `/etc/swanctl/conf.d/*.conf`, `/etc/frr/frr.conf`
- Maintains idempotency via `/etc/nebius-vpngw/last-applied.json`
- Reloads strongSwan and FRR when config changes

### Upgrading Python Dependencies

#### When to Upgrade

- **Security patches**: When CVEs are announced for dependencies
- **Bug fixes**: When a dependency fixes a critical bug
- **New features**: When upgrading enables new functionality
- **Before QA**: Ensure all packages are at stable, tested versions
- **Regular maintenance**: Quarterly review of dependency versions

#### Upgrade Workflow

**1. Update `pyproject.toml` with new version constraints:**

```toml
dependencies = [
    "rich>=14.2.0,<15.0.0",  # Updated from 13.9.2
    "typer>=0.20.0,<1.0.0",  # Updated from 0.12.5
]
```

**Version constraint guidelines:**

- Use `>=X.Y.Z,<next-major` format (e.g., `>=14.2.0,<15.0.0`)
- Pin major version to prevent breaking changes
- Allow minor/patch updates for bug fixes

**2. Test locally:**

```bash
# Update packages in your venv
poetry update rich typer paramiko

# Smoke test the CLI
nebius-vpngw --help
nebius-vpngw status --local-config-file nebius-gcp-ha-vpngw.config.yaml
```

**3. Rebuild the wheel (CRITICAL):**

```bash
# Clean old wheels to prevent stale dependencies
rm -rf dist/*.whl

# Rebuild using poetry (recommended - faster, uses poetry.lock)
poetry build -f wheel
```

**Note:** `nebius-vpngw apply` automatically rebuilds the wheel and cleans old ones.

**4. Verify wheel metadata:**

```bash
# Check that wheel contains updated dependencies
unzip -p dist/nebius_vpngw-*.whl "*.dist-info/METADATA" | grep Requires-Dist
```

**5. Deploy to VMs:**

```bash
nebius-vpngw apply --local-config-file nebius-gcp-ha-vpngw.config.yaml
```

The deployment process:

1. Builds fresh wheel with updated dependencies
2. Uploads wheel to VM: `/tmp/nebius_vpngw-*.whl`
3. Runs: `sudo pip3 install --force-reinstall <wheel>`
4. Restarts agent with new packages

**6. Verify upgrade on VM:**

```bash
ssh ubuntu@<gateway-ip> 'sudo -H pip3 list | grep -E "rich|typer|paramiko"'
```

#### Best Practices

1. **Test major upgrades locally first**: Use `poetry update` in venv before updating `pyproject.toml`
2. **Review changelogs**: Check for breaking changes in major version bumps
3. **Clean wheels regularly**: Run `rm -rf dist/*.whl` before building for QA/production
4. **Lock with Poetry**: Run `poetry lock` after updates to freeze transitive dependencies
5. **Document why**: Add comment in `pyproject.toml` for non-obvious version pins

#### Troubleshooting Upgrades

**Problem:** Packages not upgrading on VM

```bash
# Check if wheel has old dependencies
unzip -p dist/nebius_vpngw-*.whl "*.dist-info/METADATA" | grep Requires-Dist

# Solution: Clean and rebuild
rm -rf dist/*.whl
poetry build -f wheel
```

**Problem:** Dependency conflicts

```bash
# Check compatibility
poetry show --tree
```

**Problem:** Import errors after upgrade

```bash
# SSH to VM and check agent logs
ssh ubuntu@<gateway-ip> 'sudo journalctl -u nebius-vpngw-agent -n 50'

# Restart agent
sudo systemctl restart nebius-vpngw-agent
```

### Troubleshooting Development Setup

**`ModuleNotFoundError: No module named 'nebius.pysdk'`**

You're using the old GitHub SDK. This package uses the PyPI SDK:

```bash
source .venv/bin/activate
pip uninstall nebius-pysdk  # Remove old SDK
pip install -e .            # Reinstall with correct SDK (nebius.sdk)
```

**Verify SDK installation:**

```python
import nebius.sdk as sdk
print("Nebius SDK OK:", sdk)
```

## Project Structure

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

## License

See `LICENSE` for details.
