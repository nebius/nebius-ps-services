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

## Quick Start

Prereqs (macOS):

- Python 3.10–3.12
- `graphviz` (for diagrams; optional)
- Poetry (maintainers only)

Install and run CLI (pip, recommended for users):

```zsh
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -e .
python -c "import nebius.sdk as sdk; print('Nebius SDK OK:', sdk)"
nebius-vpngw --help
```

Install and run CLI (Poetry, for maintainers):

```zsh
poetry install
poetry run python -c "import nebius.sdk as sdk; print('Nebius SDK OK:', sdk)"
poetry run nebius-vpngw --help
# or run as module under Poetry
poetry run python -m nebius_vpngw --help
```

### Run

Dry-run (renders actions without applying):

```zsh
nebius-vpngw --local-config-file ./nebius-vpngw-config.yaml --dry-run
```

Apply (provisions gateway VMs and pushes config):

```zsh
# Ensure auth context (examples)
export TENANT_ID="your-tenant-id"
export PROJECT_ID="your-project-id"
export REGION_ID="eu-north1"

nebius-vpngw \
  --local-config-file ./nebius-vpngw-config.yaml \
  --sa nb-vpngw-sa \
  --project-id "$PROJECT_ID" \
  --zone "${REGION_ID}-a"
```

Networking defaults (v1):

- Single VPC network selected via `network_id` (optional). If omitted, the orchestrator resolves your environment’s default network.
- One gateway subnet named `vpngw-subnet` with CIDR `/27` is ensured/created under the selected network.
- Each gateway VM is provisioned with two NICs (eth0, eth1) attached to `vpngw-subnet`.
- Each NIC gets one public IP allocation. If `external_ips` are not provided in YAML, the orchestrator creates two allocations and attaches them.

### First run bootstrap

- If you run `nebius-vpngw` without `--local-config-file`, the CLI checks for `./nebius-vpngw-config.yaml`.
- If missing, it auto-creates one by copying the packaged `nebius-vpngw-config-template.yaml` into the current directory and exits.
- Edit the file to fill environment-specific values and secrets, then re-run.

Secrets guidance:

- Do not commit `nebius-vpngw-config.yaml`. Prefer environment variables for placeholders (e.g., `${GCP_TUNNEL_1_PSK}`) or a secret manager.
- Only the template file is distributed with the wheel.

SSH public key convenience:

- You can either embed `gateway_group.vm_spec.ssh_public_key` (inline) or set `gateway_group.vm_spec.ssh_public_key_path: "~/.ssh/id_ed25519.pub"`.
- If `ssh_public_key_path` is provided and `ssh_public_key` is empty/missing, the CLI reads the file and inlines its contents automatically.
- Optional: set `gateway_group.vm_spec.ssh_username` (default: `ubuntu`) and `gateway_group.vm_spec.ssh_private_key_path` (default: use your SSH agent) for pushing configs over SSH.

Networking-specific YAML fields:

- `gateway_group.network_id` (optional): target VPC network. If missing, defaults to the environment’s default network.
- `gateway_group.external_ips` (optional): list of public IP allocation IDs; when absent or empty, two allocations are created and attached to eth0/eth1.

### Nebius API authentication (service account)

- Create a Service Account with appropriate permissions on your `project_id` and obtain an access token (see SA setup script linked in examples).
- Configure these context variables via environment or YAML placeholders:
  - `TENANT_ID` → `${TENANT_ID}`
  - `PROJECT_ID` → `${PROJECT_ID}`
  - `REGION_ID` → `${REGION_ID}`
- Recommended: export as env vars, reference them in YAML as `${...}`.

Token requirement (PyPI SDK):

- The PyPI Nebius SDK reads an IAM token from `NEBIUS_IAM_TOKEN` by default.
- When you pass `--sa`, the CLI will attempt to create/reuse a Service Account and export `NEBIUS_IAM_TOKEN` in the environment for this run.
- Alternatively, you can set it manually:

```zsh
export NEBIUS_IAM_TOKEN="$(your_token_command_or_value)"
```

Minimal config (YAML) for quick start:

```yaml
# nebius-vpngw-config.yaml (minimal)
gateway_group:
  name: vpngw
  vm_spec:
    count: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
  # optional: if omitted, default network is used
  # network_id: ${NETWORK_ID}
  # optional: create two allocations if omitted/empty
  # external_ips: []
```

Minimal Python example with `nebius.sdk`:

```python
import os
import nebius.sdk as sdk

tenant_id = os.environ["TENANT_ID"]
project_id = os.environ["PROJECT_ID"]
region_id = os.environ.get("REGION_ID", "eu-north1")

client = sdk.SDK(tenant_id=tenant_id, project_id=project_id, region_id=region_id)

# Example: list VPC networks (API surface may vary by SDK version)
vpc = client.vpc()
for net in vpc.network.list(parent_id=project_id):
  print(net)
```

Note: ensure your SA token is available to the SDK per pysdk instructions (e.g., env or config file). Refer to the official API reference.


Reproducibility tip:

```zsh
poetry lock
git add poetry.lock && git commit -m "Lock dependencies"
```

## Install

This project uses Poetry.

```zsh
# pyproject.toml is at repository root
poetry install
# Activate the virtualenv (Poetry >=2.x removed the default shell command):
eval "$(poetry env activate zsh)"
# Now run the CLI
nebius-vpngw --help

# Optional: restore legacy 'poetry shell' behavior by installing the plugin
# poetry self add poetry-plugin-shell
# poetry shell
```

Alternatively with `pip` (editable mode):

```zsh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -c "import nebius.sdk as sdk; print('Nebius SDK OK:', sdk)"
```

Troubleshooting:

- If you see `ModuleNotFoundError: No module named 'nebius.pysdk'`, your environment still references the GitHub SDK layout. This package targets the PyPI Nebius SDK (`nebius.sdk`). Ensure the virtualenv is active and reinstall via pip as shown above.

## CLI Usage

```zsh
# Default invocation (apply is implicit)
nebius-vpngw \
  --local-config-file ./nebius-vpngw-config.yaml \
  --dry-run

# Apply with peer configs (explicit subcommand also works)
nebius-vpngw \
  --local-config-file ./nebius-vpngw-config.yaml \
  --peer-config-file ./doc/gcp-ha-vpn-config.txt \
  --peer-config-file ./doc/aws-vpn-config.txt \
  --sa nb-vpngw-sa \
  --project-id my-project \
  --zone eu-north1-a

Tip: Supplying multiple `--peer-config-file` values lets the orchestrator merge details from several peers (e.g., one GCP and one AWS) into the YAML-defined connections. Vendor is auto-detected when possible, but files are treated generically — they only populate missing fields (PSKs, APIPA IPs, crypto, remote ASN) and never change your topology.

Service Account option (`--sa`):
- If you pass `--sa <name>`, the CLI will attempt to ensure a Service Account of that name exists (with Editor permissions) and obtain a token via the Nebius SDK. If it cannot, it falls back to using the Nebius CLI default profile.
- If you omit `--sa`, the Nebius SDK initialization relies on your local Nebius CLI config (default profile). See https://docs.nebius.com/cli/quickstart.

Public IPs (dynamic vs static allocations):
- Default: Two static public IP allocations are attached — one per NIC (eth0, eth1). If `external_ips` is omitted/empty, the orchestrator creates and attaches them under `vpngw-subnet`.
- Specific allocations: Provide two allocation IDs via `gateway_group.external_ips` to attach existing allocations. The orchestrator validates their region/network alignment.
- Region alignment: Allocations and `vpngw-subnet` must be in the VM’s region.

Agent service on VM:
- Install the agent binary and systemd unit (packaged at `nebius_vpngw/systemd/nebius-vpngw-agent.service`), then enable/start:
 
  ```zsh
  sudo cp /path/to/nebius-vpngw-agent /usr/bin/
  sudo cp $(python -c 'import importlib.resources as r; print(r.files("nebius_vpngw").joinpath("systemd/nebius-vpngw-agent.service"))') /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now nebius-vpngw-agent
  ```


## Build Executable

Build a standalone binary using Poetry (recommended):

```zsh
poetry install
poetry run build-binary
# Binary created under `dist/nebius-vpngw`
```

Advanced (optional): raw PyInstaller invocation if you need custom flags:

```zsh
poetry run pyinstaller -F -n nebius-vpngw src/nebius_vpngw/__main__.py
```

## Distribution Options

This project supports two distribution modes to fit different audiences:

- Python package (recommended for dev/ops teams)
- Single-file binary via PyInstaller (for non-Python users)

### Python Package (Dev/Ops, reproducible installs)

- Why: Deterministic environments using `poetry.lock`; easy install and updates.
- Entry points: Defined in `tool.poetry.scripts`.

Commands (macOS / zsh):

```zsh
# Create/refresh lock for reproducible builds
poetry lock

# Install in a virtualenv managed by Poetry
poetry install

# Run the CLIs from the virtualenv
poetry run nebius-vpngw --help
poetry run nebius-vpngw-agent --help

# Optional: install system-wide via pipx (isolated environment)
pipx install .
nebius-vpngw --help
```

Notes:

- Commit `poetry.lock` to version control.
- For library-like usage you may choose not to commit the lock, but for apps/services it’s best practice to commit it.

### Single-File Binary (Non-Python users)

- Why: One executable, no Python required on target hosts.
- Tool: PyInstaller.
- Requirements: Test on a clean macOS, codesign/notarize if distributing externally, bundle any required assets.

Build commands:

```zsh
# Ensure dependencies are installed in the Poetry venv
poetry install

# Build single-file binary
poetry run build-binary  # uses [tool.poetry.scripts] build-binary entry

# Result: dist/nebius-vpngw
ls -la dist

# Or raw PyInstaller (equivalent)
poetry run pyinstaller -F -n nebius-vpngw src/nebius_vpngw/__main__.py

# If your CLI loads modules/plugins dynamically, you may need:
#   --hidden-import some_module
# If your app needs assets (e.g., diagram templates), bundle them:
#   --add-data "image/*:image"
```

macOS distribution checklist:

- Codesign the binary with a valid Apple Developer ID.
- Notarize the signed binary with Apple (required for Gatekeeper).
- Verify on a clean macOS VM before sharing.

Example codesign/notarize (placeholder):

```zsh
# Sign (replace with your identity)
codesign --force --options runtime --sign "Developer ID Application: Your Org (TEAMID)" dist/nebius-vpngw

# Verify signature
codesign --verify --deep --strict dist/nebius-vpngw
spctl --assess --verbose=4 dist/nebius-vpngw

# Notarize (requires xcrun altool/notarytool setup)
# xcrun notarytool submit dist/nebius-vpngw --keychain-profile "notary-profile" --wait
```

Troubleshooting:

- If the binary fails to locate resources, add `--add-data` for required files and access them via `importlib.resources` or relative paths.
- If Rich/Typer styles don’t render correctly in minimal terminals, try setting `TERM=xterm-256color`.
- If you need custom PyInstaller arguments, run the underlying command directly (see `nebius_vpngw/build.py`).
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

## Development

Install development dependencies (pytest, ruff, pyinstaller) using Poetry's group:

```zsh
# Include the dev group
poetry install --with dev

# Or if already installed without dev, add it:
poetry install --with dev --sync
```

Run the test suite:

```zsh
poetry run pytest -q
# Focus on a single test file
poetry run pytest tests/test_config_loading.py::test_load_config_env_token -q
```

Lint and format with Ruff:

```zsh
# Static analysis (errors + style)
poetry run ruff check .

# Auto-fix (safe fixes only) then re-check
poetry run ruff check . --fix

# Format (Black-compatible mode)
poetry run ruff format .
```

Suggested pre-commit configuration (optional):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format
  - repo: https://github.com/pytest-dev/pytest
    rev: 8.3.3
    hooks:
      - id: pytest
        additional_dependencies: []
```

Then:

```zsh
pip install pre-commit
pre-commit install
```

Environment-variable placeholders in the YAML (`${VAR}`) must be set before running tests that load configs. For local iteration you can export dummy values:

```zsh
export GCP_TUNNEL_1_PSK=dummy AWS_TUNNEL_1_PSK=dummy TENANT_ID=tenant PROJECT_ID=project REGION_ID=eu-north1
```

## Notes

- Vendor peer parsers are placeholders; integrate real parsing as needed.
- VM/route managers are stubs; wire to Nebius SDK when available.
- The scaffold emphasizes modularity and idempotency per the design.

## License

See `LICENSE` for details.
