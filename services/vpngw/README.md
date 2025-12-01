# Nebius VPN Gateway (VM-based)

A modular Python-based orchestrator and agent to provision Nebius Compute VMs as Site-to-Site IPsec VPN gateways compatible with GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, and on-prem routers.

## Features

- IPsec (strongSwan) with IKEv2/IKEv1, AES-256, SHA-256/384/512, DH 14/20/24
- BGP (FRR) and static routing modes
- Single-VM and multi-VM gateway groups
- YAML-driven configuration plus optional peer config import
- Idempotent agent applying configs on the VM

## Project Layout

- `nebius-vpngw-config.yaml`: main user-facing config
- `src/nebius_vpngw/cli.py`: orchestrator CLI (`nebius-vpngw`)
- `src/nebius_vpngw/config_loader.py`: YAML and peer-merge logic
- `src/nebius_vpngw/peer_parsers/`: vendor parsers (GCP/AWS/Azure/Cisco)
- `src/nebius_vpngw/deploy/`: VM, route, and SSH push managers
- `src/nebius_vpngw/agent/`: always-running agent on each VM

## Install

This project uses Poetry.

```zsh
# From project root where pyproject.toml lives
cd src
poetry install
poetry shell
```

Alternatively with `pip` (editable mode):

```zsh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## CLI Usage

```zsh
# Default invocation (apply is implicit)
nebius-vpngw \
  --local-config-file ../nebius-vpngw-config.yaml \
  --dry-run

# Apply with peer configs (explicit subcommand also works)
nebius-vpngw \
  --local-config-file ../nebius-vpngw-config.yaml \
  --peer-config-file ./doc/gcp-ha-vpn-config.txt \
  --peer-config-file ./doc/aws-vpn-config.txt \
  --project-id my-project \
  --zone eu-north1-a

Tip: Supplying multiple `--peer-config-file` values lets the orchestrator merge details from several peers (e.g., one GCP and one AWS) into the YAML-defined connections. Vendor is auto-detected when possible, but files are treated generically — they only populate missing fields (PSKs, APIPA IPs, crypto, remote ASN) and never change your topology.
```

## Build Executable

You can build a standalone binary using `pyinstaller`:

```zsh
poetry add pyinstaller --group dev
pyinstaller -n nebius-vpngw -F -p nebius_vpngw nebius_vpngw/cli.py
# Binary created under `dist/nebius-vpngw`
```

Or use Poetry to create wheel/sdist:

```zsh
poetry build
# dist/nebius-vpngw-0.1.0-py3-none-any.whl
```

## Agent Service

On the gateway VM, install and run the agent (scaffold):

- Console script: `nebius-vpngw-agent`
- Reads `/etc/nebius-vpngw/config-resolved.yaml`
- Writes strongSwan (`/etc/ipsec.conf`, `/etc/ipsec.secrets`) and FRR (`/etc/frr/bgpd.conf`) configs
- Idempotent via `/etc/nebius-vpngw/last-applied.json`

Example systemd unit (to add later):

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

## Notes

- Vendor peer parsers are placeholders; integrate real parsing as needed.
- VM/route managers are stubs; wire to Nebius SDK when available.
- The scaffold emphasizes modularity and idempotency per the design.
