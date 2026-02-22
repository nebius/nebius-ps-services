# ssh-jumphost module

Reusable Terraform module that creates a Nebius SSH jump-host VM with
cloud-init hardening.

Resources managed:

- `nebius_compute_v1_disk` (boot disk)
- `nebius_compute_v1_instance` (SSH jump-host VM)
- optional `nebius_vpc_v1_allocation` (dedicated static public IP allocation)

Out of scope:

- in-cluster application rollout and day-2 app lifecycle (handled by GitOps/Flux)
- credential/secret distribution workflows

## Usage

### Local path source

```hcl
module "ssh_jump_host" {
  source = "./platform-infra/modules/ssh-jumphost"

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "demo-ssh-jh"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
```

### Git tag source

```hcl
module "ssh_jump_host" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/ssh-jumphost?ref=v0.1.0"

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "demo-ssh-jh"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
```

### Registry source (when published)

```hcl
module "ssh_jump_host" {
  source  = "nebius/ssh-jumphost/nebius"
  version = "~> 0.1"

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "demo-ssh-jh"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
```

## Inputs summary

- Required:
  - `parent_id`
  - `region`
  - `subnet_id`
  - `name`
  - `ssh_public_key` (inline key string)
  - `allowed_cidrs` (at least one source CIDR)
- Optional:
  - `ssh_user_name`
  - `platform`
  - `preset`
- Optional public IP behavior:
  - default: `create_public_ip_allocation = true`
  - reuse existing allocation: `create_public_ip_allocation = false` +
    `public_ip_allocation_id`
- Optional disk/image controls:
  - `boot_disk_size_gib`
  - `boot_disk_block_size_bytes`
  - `boot_disk_type`
  - `source_image_family`

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

   If you use `platform-infra/stacks/customer-platform`:

   ```bash
   terraform output -raw ssh_jumphost_public_ip
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
   `modules/wireguard-jumphost` instead.

ProxyJump example from your laptop:

```bash
ssh -J <ssh_user_name>@<bastion_public_ip> <target_user>@<target_private_ip>
```

## nebius-cxcli mapping

When this module is consumed through `platform-infra/stacks/customer-platform`,
`nebius-cxcli` maps:

- `infra.ssh_user_name` -> `ssh_user_name`
- `infra.ssh_public_key` -> `ssh_public_key`
- `infra.ssh-jumphost.*` -> `ssh_jumphost_*`

## Security defaults

- SSH key-only auth (`PasswordAuthentication no`)
- `AuthenticationMethods publickey`
- `PermitRootLogin no`
- `AllowAgentForwarding no`
- `AllowTcpForwarding yes` (required for ProxyJump)
- `ClientAliveInterval`/`ClientAliveCountMax` idle session limits
- `MaxStartups` throttling for connection floods
- UFW default deny inbound
- SSH allowed only from `allowed_cidrs`
- Strict bootstrap mode: if `allowed_cidrs` is empty, setup fails instead of
  opening SSH to the internet
- `allowed_cidrs` is rendered to `/etc/bastion_allowed_cidrs` as the
  host-level policy interface for ingress
- `fail2ban` with `systemd` backend and `ufw` ban action
- `auditd` targeted watches for SSH/sudo/identity files

## State and security

- Do not hardcode backend credentials in Terraform files.
- Use remote state with locking and encryption.
- `ssh_public_key` is intentionally inline string input to avoid local path vs
  CI runner drift.

## Upgrade notes

- Pin module sources by tag (`?ref=vX.Y.Z`) in consumer stacks.
- After provider/module upgrades, run:
  - `terraform init -upgrade`
  - review `terraform.lock.hcl`
  - run `terraform plan`

## Validation commands

```bash
terraform fmt -recursive
terraform init -backend=false
terraform validate
```
