# ssh-jumphost module

Reusable Terraform module that creates a Nebius SSH jump-host VM with
cloud-init hardening and a VM-local day-2 allowlist helper. The module is a thin
wrapper around the shared `../vm` module, so VM disk/instance/network semantics
stay aligned with the generic VM module while this module owns only the SSH
bastion bootstrap policy, VM-local SSH source CIDR management, and optional
static public IP allocation.

Resources managed:

- shared `../vm` module resources for the boot disk and SSH jump-host VM
- optional `nebius_vpc_v1_allocation` (dedicated static public IP allocation)

Out of scope:

- in-cluster application rollout and day-2 app lifecycle (handled by GitOps/Flux)
- credential/secret distribution workflows

## Usage

### Local path source

```hcl
module "ssh_jump_host" {
  source = "./platform-infra/modules/ssh-jumphost"

  parent_id           = "project-xxxxxxxx"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  name                = "demo-ssh-jh"
  platform            = "cpu-d3"
  preset              = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  boot_disk_size_gib  = 64
  ssh_user_name       = "ubuntu"
  ssh_public_key      = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
```

### Git tag source

```hcl
module "ssh_jump_host" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/ssh-jumphost?ref=v0.1.0"

  parent_id           = "project-xxxxxxxx"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  name                = "demo-ssh-jh"
  platform            = "cpu-d3"
  preset              = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  boot_disk_size_gib  = 64
  ssh_user_name       = "ubuntu"
  ssh_public_key      = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
```

## Examples

- `examples/minimal`: baseline SSH bastion with dedicated static public IP.

## Inputs summary

- Required:
  - `parent_id`
  - `subnet_id`
  - `name`
  - `platform`
  - `preset`
  - `source_image_family`
  - `ssh_public_key` (inline key string)
  - `allowed_cidrs` (at least one source CIDR)
- Optional:
  - `ssh_user_name`
- Optional public IP behavior:
  - default: `create_public_ip_allocation = true`
  - reuse existing allocation: `create_public_ip_allocation = false` +
    `public_ip_allocation_id`
- Optional disk/image controls:
  - `boot_disk_size_gib` is required; `nebius-cxcli` renders a recommended
    value from its shared `compute.boot_disk_defaults` policy
  - `boot_disk_block_size_bytes`
  - `boot_disk_type`
  - `boot_disk_encryption_enabled` for `NETWORK_SSD_NON_REPLICATED` or
    `NETWORK_SSD_IO_M3` boot disks; `NETWORK_SSD` is platform-encrypted
  - `boot_disk_deletion_protection`
- Optional metadata:
  - `labels` adds Nebius labels. The module also applies `component` and `name`
    labels derived from the module and `name` input, and caller-provided labels
    can override them.

## nebius-cxcli usage

- `nebius-cxcli component add` prompts this module through
  `infra.components[].inputs`.
- `inputs.platform`, `inputs.preset`, and `inputs.source_image_family` are
  selected from live Nebius provider inventory. cxcli uses the same SDK-backed
  platform/preset/image-family option flow as the generic `vm` component.
- `inputs.allowed_cidrs` is required and should be entered as a YAML/JSON list
  in the wizard or edited directly in `config.yaml`. It is the first-boot
  bootstrap seed for SSH reachability, not the normal day-2 mutation path.
- `shared.admin_ssh.user_name` from the active `component_sources.yaml` is
  materialized into `infra.components[].inputs.ssh_user_name` during
  `create`/`component add`, so later renders use the project config directly.
- `inputs.ssh_public_key` may be entered as inline public key text or a readable
  local `.pub` path when using `nebius-cxcli`; the persisted `config.yaml`
  contract is normalized back to inline key text.

## Outputs summary

- `instance_id`
- `private_ip`
- `public_ip`
- `public_ip_allocation_id`
- `ssh_connect_command`

## Connection Runbook: SSH Bastion

Use this module when you need direct SSH access to one public VM and then
operator access from there.

1. Apply Terraform.
2. Confirm your source public IP is included in `allowed_cidrs`.
   Quick check from your laptop:

   ```bash
   curl -fsS https://api.ipify.org
   ```

3. Read connection outputs:

   If you use this module directly:

   ```bash
   terraform output -raw public_ip
   terraform output -raw ssh_connect_command
   ```

4. Connect over SSH:

   ```bash
   ssh -i <private_ssh_key> <ssh_user_name>@<public_ip>
   ```

5. Optional: operate Kubernetes from bastion host network path:

   ```bash
   nebius mk8s cluster get-credentials --id <cluster_id> --internal
   kubectl cluster-info
   ```

6. If you need laptop-to-private-network VPN (not SSH bastion), use
   `modules/wireguard-gw` instead.

ProxyJump example from your laptop:

```bash
ssh -J <ssh_user_name>@<bastion_public_ip> <target_user>@<target_private_ip>
```

For a private VM, the first hop is the SSH jump host's public IP and the final
destination is the private VM address on the same reachable VPC/subnet path:

```bash
ssh -J <jump_host_user>@<jump_host_public_ip> <vm_user>@<vm_private_ip>
```

If your private key is not loaded in `ssh-agent`, add it to the command:

```bash
ssh -i <private_ssh_key> -J <jump_host_user>@<jump_host_public_ip> \
  <vm_user>@<vm_private_ip>
```

When this module is used through `nebius-cxcli`, the post-deploy report writes
the concrete ProxyJump command after Terraform outputs expose both addresses.
The source public CIDR of the operator laptop must still be present in
`allowed_cidrs`, because the jump host keeps SSH closed to every other source.

## Day-2 Source CIDR Changes

Cloud-init installs packages, SSH hardening, audit/fail2ban policy, and the
VM-local `/usr/local/sbin/nebius-ssh-jumphost` helper. The changing SSH source
CIDR allowlist is stored on the VM under `/var/lib/nebius-ssh-jumphost/` and
can be updated without replacing the VM.

From `nebius-cxcli`, use:

```bash
nebius-cxcli ssh-jumphost --add-allowed-cidrs <config.yaml> \
  --allowed-cidr 203.0.113.10/32,198.51.100.0/24
nebius-cxcli ssh-jumphost --remove-allowed-cidrs <config.yaml> \
  --allowed-cidr 198.51.100.0/24
nebius-cxcli ssh-jumphost --list-allowed-cidrs <config.yaml>
```

For direct module users, SSH to the jump host and run the helper:

```bash
sudo nebius-ssh-jumphost add-allowed-cidrs --output-json \
  --allowed-cidr 203.0.113.10/32,198.51.100.0/24
sudo nebius-ssh-jumphost remove-allowed-cidrs --output-json \
  --allowed-cidr 198.51.100.0/24
sudo nebius-ssh-jumphost list --output-json
```

The helper canonicalizes and deduplicates IPv4 CIDRs, takes a local file lock,
persists the runtime allowlist, and reapplies the module-owned UFW policy. It
refuses to remove the last remaining source CIDR to avoid SSH lockout. If you
need a different first-boot seed for a replacement jump host, update
`allowed_cidrs` and review the Terraform plan.

## nebius-cxcli mapping

For Terraform roots generated by `nebius-cxcli`, mappings are:

- `infra.components[id=ssh-jumphost].inputs.ssh_user_name` -> `ssh_user_name`
- `infra.components[id=ssh-jumphost].inputs.ssh_public_key` -> `ssh_public_key`
- `infra.components[id=ssh-jumphost].inputs.*` -> `ssh_jumphost_*`

## Security defaults

- SSH key-only auth (`PasswordAuthentication no`)
- `AuthenticationMethods publickey`
- `PermitRootLogin no`
- `AllowAgentForwarding no`
- `AllowTcpForwarding yes` (required for ProxyJump)
- `ClientAliveInterval`/`ClientAliveCountMax` idle session limits
- `MaxStartups` throttling for connection floods
- UFW default deny inbound
- SSH allowed only from the runtime allowlist seeded from `allowed_cidrs`
- Strict bootstrap mode: if `allowed_cidrs` is empty, setup fails instead of
  opening SSH to the internet
- Day-2 source CIDR changes are applied by `/usr/local/sbin/nebius-ssh-jumphost`
  and persisted under `/var/lib/nebius-ssh-jumphost/`
- The helper owns the jump-host UFW SSH policy; avoid hand-editing UFW rules on
  this VM unless you also take ownership of reconciling them afterward
- `fail2ban` with `systemd` backend and `ufw` ban action
- `auditd` targeted watches for SSH/sudo/identity files

## State and security

- Do not hardcode backend credentials in Terraform files.
- Use remote state with locking and encryption.
- `ssh_public_key` is intentionally inline string input to avoid local path vs
  CI runner drift.
- Use the repo-local or Git subdirectory source forms shown above; this wrapper
  depends on the sibling `../vm` module.

## Upgrade notes

- Pin module sources by tag (`?ref=vX.Y.Z`) in consumer roots.
- After provider/module upgrades, run:
  - `terraform init -upgrade`
  - review `terraform.lock.hcl`
  - run `terraform plan`

## Validation commands

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
```
