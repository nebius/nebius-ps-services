# wireguard-jumphost module

Reusable Terraform module that creates a Nebius WireGuard jump host VM for
point-to-site VPN access to private cloud resources.

## What it configures

Resources:

- `nebius_compute_v1_disk` (boot disk)
- `nebius_compute_v1_instance` (WireGuard VM with cloud-init)
- optional `nebius_vpc_v1_allocation` (static public IP allocation)

On-host bootstrap:

- Creates admin SSH user (`ssh_user_name`) with sudo and key-based auth.
- Installs WireGuard (`wireguard` + `wireguard-tools`) + OpenSSH + UFW.
- Configures server `wg0` in NAT mode by default.
- Opens WireGuard UDP port in UFW (`wireguard_listen_port`, default `51820`).
- Auto-generates client peers and client config files when `clients` are set.
- Ensures SSH service is enabled and reachable through UFW (`OpenSSH`).
- Generates per-client pre-shared keys (PSK) in addition to keypairs.

Out of scope:

- Nebius perimeter firewall/NSG policy objects (not managed by this module).
- Kubernetes in-cluster components (Flux/GitOps scope).

## Default networking behavior

- `nat_mode = true` (default):
  - enables IPv4 forwarding
  - applies MASQUERADE for WireGuard client subnet egress via WAN interface
  - recommended for simple point-to-site connectivity
- `nat_mode = false`:
  - keeps WireGuard tunnel without MASQUERADE
  - requires routed-mode network design (for example explicit routes/security
    policy for the WireGuard client subnet)
- `wireguard_listen_port = 51820` (default, configurable)

This aligns with production-style NAT mode for VPN jump access where you want
operators to reach private VMs/clusters without managing extra routed-mode VPC
route tables.

Current Nebius behavior in this stack does not require separate NSG resource
management here; if perimeter policy controls are introduced in your
environment, allow inbound UDP on `wireguard_listen_port` to the jump host.

## Usage

### Local path source

```hcl
module "wireguard_jump_host" {
  source = "./platform-infra/modules/wireguard-jumphost"

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "demo-wg"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  wireguard_tunnel_cidr = "10.8.0.1/24"
  wireguard_listen_port = 51820
  nat_mode              = true

  clients = [
    {
      name                 = "laptop-ops"
      address              = "10.8.0.2/32"
      allowed_ips          = ["10.8.0.0/24", "10.0.0.0/8"]
      dns                  = ["1.1.1.1"]
      persistent_keepalive = 25
      write_ssh_config     = true
    }
  ]
}
```

### Git tag source

```hcl
module "wireguard_jump_host" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/wireguard-jumphost?ref=v0.1.0"
  # same inputs as above
}
```

## Examples

- `examples/minimal`: baseline WireGuard jump host with NAT mode enabled.

## Inputs summary

- Required:
  - `parent_id`
  - `region`
  - `subnet_id`
  - `name`
  - `ssh_public_key` (inline)
- Core VPN controls:
  - `wireguard_tunnel_cidr` (default `10.8.0.1/24`)
  - `wireguard_listen_port` (default `51820`)
  - `nat_mode` (default `true`)
  - `endpoint_host` (optional override for client config endpoint)
- Client automation:
  - `clients[]` with `name`, `address`, optional `allowed_ips`, `dns`,
    `persistent_keepalive`, `write_ssh_config`
  - each `clients[]` entry is rendered into server `[Peer]` and client `.conf`
    automatically during cloud-init
- Public IP behavior:
  - default: create dedicated static allocation
  - reuse: set `create_public_ip_allocation = false` and provide
    `public_ip_allocation_id`

## nebius-cxcli usage

- `nebius-cxcli component add` prompts this module through
  `infra.components[].inputs`.
- `clients` is optional, but when used it should be entered as a YAML/JSON list
  in the wizard or edited directly in `config.yaml`.
- `ssh_user_name` now follows the same Linux username validation used by the
  SSH jump-host module and the shared CLI SSH defaults.

## Outputs summary

- `instance_id`
- `private_ip`
- `public_ip`
- `public_ip_allocation_id`
- `wireguard_listen_port`
- `wireguard_clients_path` (`/var/lib/wireguard/clients`)

## Automated peer/client flow

When `clients` are provided, cloud-init automatically:

1. Generates a keypair per client on the server.
2. Generates a per-client PSK and adds each client as a `[Peer]` in `/etc/wireguard/wg0.conf`.
3. Writes per-client config files to:
   - `/var/lib/wireguard/clients/<client-name>/<client-name>.conf`
4. Optionally writes SSH ProxyJump snippet:
   - `/var/lib/wireguard/clients/<client-name>/ssh_config`

This removes the manual `wireguard-ui` user/peer creation workflow.

## Operator runbook (fully automated model)

1. `terraform apply`.
2. Read jump host public IP:

   ```bash
   terraform output -raw public_ip
   ```

3. SSH to admin user on WireGuard VM:

   ```bash
   ssh -i <private_key> <ssh_user_name>@<public_ip>
   ```

4. Retrieve generated client config:

   ```bash
   sudo ls -la /var/lib/wireguard/clients
   sudo cat /var/lib/wireguard/clients/<client-name>/<client-name>.conf
   ```

5. Import config into WireGuard app and connect.

6. Verify:

   ```bash
   sudo wg show
   ping -c 3 <private-vm-ip>
   ssh <vm-user>@<private-vm-ip>
   ```

## nebius-cxcli mapping

For Terraform roots generated by `nebius-cxcli`, mappings are:

- `infra.ssh_user_name` -> `ssh_user_name`
- `infra.ssh_public_key` -> `ssh_public_key`
- `infra.wireguard-jumphost.*` -> `wireguard_*`

## Security notes

- SSH is key-only; no password auth.
- WireGuard UDP port is explicitly opened in UFW.
- UFW forwarding policy is set to allow routed WireGuard traffic.
- NAT mode means private targets see source as jump-host private IP.
- Keep Terraform state encrypted and access-controlled.

## Validation commands

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
```
