# Nebius VPN Gateway (VM-based)

A modular Python-based orchestrator and agent to provision Nebius VMs as Site-to-Site IPsec VPN gateways (compatible with GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, and on-premises routers)

## Features

- **IPsec (strongSwan)**: IKEv2/IKEv1, AES-256, SHA-256/384/512, DH groups 14/20/24
- **Routing modes**: BGP (FRR) and static routing
- **High availability**: Single-VM or multi-VM gateway groups
- **Configuration**: YAML-driven with optional peer config import from cloud providers
- **Automation**: Idempotent agent automatically applies and maintains configurations

## Quick Start

### Prerequisites

- Python 3.10–3.12
- Nebius account

### Installation

Install using pip (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -e .
poetry build
nebius-vpngw --help
```

### Configuration

On first run, the CLI automatically creates a template configuration file:

```bash
nebius-vpngw
# Creates ./nebius-vpngw-config.yaml from template
```

Edit the configuration file with your environment details:

```yaml
# nebius-vpngw-config.yaml (minimal example)
gateway_group:
  name: vpngw
  vm_spec:
    count: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
  # Optional: specify VPC network (defaults to your default network)
  # network_id: ${NETWORK_ID}
  # Optional: specify public IP allocations (auto-created if omitted)
  # external_ips: []
```

**Important**: Do not commit sensitive values. Use environment variables for secrets:

```yaml
tunnels:
  - name: gcp-tunnel-1
    psk: ${GCP_TUNNEL_1_PSK}  # Set via: export GCP_TUNNEL_1_PSK="your-secret"
```

### Authentication

Set up Nebius API credentials:

```bash
export TENANT_ID="your-tenant-id"
export PROJECT_ID="your-project-id"
export REGION_ID="eu-north1"
export NEBIUS_IAM_TOKEN="$(your_token_command)"
```

Alternatively, use service account authentication with the `--sa` flag (CLI will create/reuse the service account automatically).

### Deploy Your First Gateway

Preview changes (dry-run):

```bash
nebius-vpngw --local-config-file ./nebius-vpngw-config.yaml --dry-run
```

Deploy gateway VMs and configure tunnels:

```bash
nebius-vpngw \
  --local-config-file ./nebius-vpngw-config.yaml \
  --sa nb-vpngw-sa \
  --project-id "$PROJECT_ID" \
  --zone "${REGION_ID}-a"
```

Check tunnel status:

```bash
nebius-vpngw status --local-config-file ./nebius-vpngw-config.yaml
```

## Usage

### CLI Commands

**View tunnel status and system health:**

```bash
nebius-vpngw status --local-config-file ./nebius-vpngw-config.yaml
```

**Deploy or update gateway configuration:**

```bash
nebius-vpngw apply --local-config-file ./nebius-vpngw-config.yaml
```

**Import peer configuration from cloud provider:**

```bash
nebius-vpngw apply \
  --local-config-file ./nebius-vpngw-config.yaml \
  --peer-config-file ./gcp-ha-vpn-config.txt \
  --peer-config-file ./aws-vpn-config.txt
```

Peer config files automatically populate missing tunnel details (PSKs, IPs, crypto proposals) without changing your topology.

**Preview changes without applying:**

```bash
nebius-vpngw --local-config-file ./nebius-vpngw-config.yaml --dry-run
```

### CLI Options

**Authentication:**

- `--sa <name>`: Create/use service account with Editor permissions
- Without `--sa`: Uses Nebius CLI default profile credentials

**Networking:**

- Gateway VMs are created with two NICs (eth0, eth1) in `vpngw-subnet` (auto-created, /27 CIDR)
- Two public IPs are allocated (one per NIC) unless specified via `gateway_group.external_ips`
- If `network_id` is omitted, the default VPC network is used

**Destructive changes:**

Some configuration changes require VM recreation (e.g., changing CPU, memory, boot disk type):

```bash
nebius-vpngw apply --recreate-gw --local-config-file ./nebius-vpngw-config.yaml
```

**Warning**: Recreation causes downtime. Public IPs are preserved and reassigned.

### SSH Configuration

Configure SSH access in your YAML:

```yaml
gateway_group:
  vm_spec:
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"  # Auto-reads file content
    ssh_username: ubuntu  # Default: ubuntu
    ssh_private_key_path: "~/.ssh/id_ed25519"  # Optional, uses SSH agent if omitted
```

The CLI automatically reads `ssh_public_key_path` and embeds the public key in the VM configuration.

## Monitoring and Troubleshooting

### Checking Tunnel Status

View active tunnels and system health:

```bash
nebius-vpngw status --local-config-file ./nebius-vpngw-config.yaml
```

**Output includes:**

- Tunnel status table: name, gateway VM, state (ESTABLISHED/CONNECTING), peer IP, encryption, uptime
- Service health: `nebius-vpngw-agent`, `strongswan-starter`, `frr` status

Example:

```text
                           VPN Gateway Status
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Tunnel               ┃ Gateway VM  ┃ Status       ┃ Peer IP         ┃ Encryption                   ┃ Uptime    ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ gcp-classic-tunnel-0 │ vpngw-vm-0  │ ESTABLISHED  │ 34.155.169.244  │ AES_GCM_16_128/...           │ 5 minutes │
└──────────────────────┴─────────────┴──────────────┴─────────────────┴──────────────────────────────┴───────────┘
```

### Configuration Files on Gateway VMs

**IPsec (strongSwan):**

- `/etc/ipsec.conf` - Tunnel configuration (auto-generated by agent)
- `/etc/ipsec.secrets` - Pre-shared keys
- `/etc/strongswan.conf` - Daemon settings

**BGP (FRR):**

- `/etc/frr/frr.conf` - BGP configuration (auto-generated by agent)
- `/etc/frr/daemons` - Enabled daemons

**Agent:**

- `/etc/nebius-vpngw-agent.yaml` - Instance-specific config
- `/var/log/nebius-vpngw-agent.log` - Agent logs

**Services:**

- `nebius-vpngw-agent.service` - Config renderer and service manager
- `strongswan-starter.service` - IPsec daemon
- `frr.service` - Routing daemon

### Common Issues

#### Tunnel Not Establishing

**Symptoms:** Status shows `CONNECTING` or no connection

**Check IPsec status:**

```bash
ssh ubuntu@<gateway-ip> 'sudo ipsec statusall'
```

**Common errors and fixes:**

| Error | Cause | Solution |
|-------|-------|----------|
| `no IKE config found` | Wrong local IP | For responder mode: use `left=%any`. For initiator mode: use `left=<external-ip>` |
| `INVALID_SYNTAX` | Crypto mismatch | Update `ike_proposal` and `esp_proposal` to match peer. GCP example: `aes256-sha256-modp2048` |
| `AUTHENTICATION_FAILED` | Wrong PSK | Verify PSK is identical (case-sensitive). Check env var: `echo $GCP_TUNNEL_PSK` |

**Verify XFRM policies installed:**

```bash
ssh ubuntu@<gateway-ip> 'sudo ip xfrm policy'
# Should show: 10.49.0.0/16 <-> 10.10.0.0/24 (your configured subnets)
```

**Check firewall rules:**

- Allow UDP 500 (IKE) and 4500 (NAT-T) from peer IP
- Allow ESP (IP protocol 50) if not using NAT-T

**View logs:**

```bash
ssh ubuntu@<gateway-ip> 'sudo journalctl -u strongswan-starter -n 100'
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
nebius-vpngw --local-config-file ./nebius-vpngw-config.yaml
```

#### BGP Session Not Establishing

**Check BGP status:**

```bash
ssh ubuntu@<gateway-ip> 'sudo vtysh -c "show bgp summary"'
```

**Common fixes:**

- Ensure BGP enabled: Check `/etc/frr/daemons` has `bgpd=yes`
- Configure tunnel IPs: BGP mode requires `inner_cidr`, `inner_local_ip`, `inner_remote_ip` in YAML
- Verify ASNs: `local_asn` and `remote_asn` must match peer configuration

**View FRR logs:**

```bash
ssh ubuntu@<gateway-ip> 'sudo journalctl -u frr -n 100'
```

### Debug Logging

Enable detailed IPsec logging for troubleshooting:

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
sudo ipsec reload                 # Reload config without restart

# XFRM (Linux kernel IPsec)
sudo ip xfrm policy               # View policies
sudo ip xfrm state                # View security associations

# BGP (if using BGP mode)
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show ip route bgp"

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
```

## Project Structure

```text
├── nebius-vpngw-config.yaml              # Main user configuration
├── src/nebius_vpngw/
│   ├── cli.py                            # CLI orchestrator (nebius-vpngw)
│   ├── config_loader.py                  # YAML parser and peer config merger
│   ├── agent/
│   │   ├── main.py                       # On-VM agent
│   │   ├── frr_renderer.py               # BGP config renderer
│   │   └── strongswan_renderer.py        # IPsec config renderer
│   ├── deploy/
│   │   ├── vm_manager.py                 # VM lifecycle management
│   │   ├── route_manager.py              # VPC route management
│   │   └── ssh_push.py                   # Config deployment over SSH
│   └── peer_parsers/
│       ├── gcp.py                        # GCP HA VPN parser
│       ├── aws.py                        # AWS Site-to-Site VPN parser
│       ├── azure.py                      # Azure VPN Gateway parser
│       └── cisco.py                      # Cisco IOS parser
```

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

1. Build wheel: `poetry build` → Creates `dist/nebius_vpngw-0.0.0-py3-none-any.whl`
2. Deploy: CLI uploads wheel to VMs via SSH and installs it

**After modifying agent code:**

```bash
# Clean old artifacts
rm -rf src/dist/ src/build/ src/nebius_vpngw.egg-info/

# Rebuild wheel
poetry build

# Deploy to VMs
nebius-vpngw --local-config-file ./nebius-vpngw-config.yaml
```

**Note:** The wheel is NOT installed in your local virtualenv—only on remote VMs.

**Agent service on gateway VM:**

```bash
# Install agent binary and systemd unit
sudo cp /path/to/nebius-vpngw-agent /usr/bin/
sudo cp /path/to/nebius-vpngw-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nebius-vpngw-agent
```

**Example systemd unit:**

```ini
[Unit]
Description=Nebius VPNGW Agent
After=network.target

[Service]
ExecStart=/usr/bin/nebius-vpngw-agent
ExecReload=/bin/kill -HUP $MAINPID
Restart=always

[Install]
WantedBy=multi-user.target
```

The agent:

- Reads `/etc/nebius-vpngw-agent.yaml` (pushed by orchestrator)
- Renders `/etc/ipsec.conf`, `/etc/ipsec.secrets`, `/etc/frr/frr.conf`
- Maintains idempotency via `/etc/nebius-vpngw/last-applied.json`

### Troubleshooting Development Setup

**`ModuleNotFoundError: No module named 'nebius.pysdk'`**

You're using the old GitHub SDK. This package targets the PyPI SDK:

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

## Notes

- Vendor peer parsers are extensible; add custom parsers in `peer_parsers/` as needed
- The architecture emphasizes modularity and idempotency
- Agent renders configs declaratively and reloads services only when changes are detected

## License

See `LICENSE` for details.
