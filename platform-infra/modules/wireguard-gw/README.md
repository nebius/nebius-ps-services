# wireguard-gw module

Reusable Terraform module that creates one Nebius VM as a WireGuard point-to-site
VPN gateway for private VPC access. It wraps the shared `../vm` module for VM,
disk, and network-interface behavior, then adds WireGuard-specific cloud-init,
firewall, forwarding, and client-generation tooling.

The module always needs a public endpoint for clients to reach the WireGuard UDP
listener. By default it creates a dedicated static public IP allocation. To reuse
an existing allocation, set `create_public_ip_allocation = false` and provide
`public_ip_allocation_id`.

## How It Works

Cloud-init runs once on first boot. It installs WireGuard, OpenSSH, UFW,
fail2ban, auditd, unattended security upgrades, writes the server config,
enables `wg-quick@wg0`, and installs this VM-local day-2 command:

```bash
sudo nebius-wireguard-client add --output-json --local-subnet 10.0.0.0/8
sudo nebius-wireguard-client add-local-subnets --local-subnet 10.20.0.0/16,10.30.0.0/16
sudo nebius-wireguard-client remove-local-subnets --local-subnet 10.20.0.0/16
sudo nebius-wireguard-client list --pretty
```

That command owns changing client state after boot. It allocates the next free
client `/32` from `wireguard_tunnel_cidr`, generates client keys plus a
pre-shared key, appends the server peer, applies the peer to the running
`wg0` interface, writes the client `.conf` under
`/var/lib/wireguard/clients/<client-name>/`, and records allocation metadata in
`/var/lib/nebius-wireguard/clients.json`. Client names are also used as
`wg-quick` interface config basenames, so they must be lowercase letters,
digits, and hyphens, up to 15 characters. If `--name` is omitted, the helper
generates a short unique `wg-...` name.

Day-2 local subnet changes are stored under `/var/lib/nebius-wireguard/` and
affect future generated client configs. Existing downloaded or imported client
configs are not rewritten automatically.

Generate one client config per device or user. Do not reuse the same `.conf`
for multiple simultaneous clients because it contains a unique private key and
tunnel `/32`.

## Networking Model

- `wireguard_tunnel_cidr` is the tunnel interface CIDR on the server, for
  example `10.8.0.1/22`. The server uses `10.8.0.1`; clients are assigned free
  `/32` addresses from the same tunnel network. With the default `/22`, the
  helper has about 1,000 usable client addresses after reserving the network,
  broadcast, and server addresses.
- `local_subnets` are the private destination CIDRs that a client routes through
  the tunnel, for example Nebius VPC ranges such as `10.0.0.0/8`.
- On the server, each peer `AllowedIPs` is the client's tunnel `/32`.
- In the generated client config, peer `AllowedIPs` is `local_subnets`; it is
  route selection, not an ACL.
- Choose a tunnel CIDR that does not overlap with the Nebius VPC, the operator's
  local network, or another VPN. Use private RFC1918 space such as `10.x.x.x`
  for the tunnel; avoid APIPA/link-local ranges because they are meant for
  local-link behavior and can interact badly with host routing.

### NAT Mode

`nat_mode = true` means source NAT from WireGuard clients into the Nebius VPC.
Client traffic enters the public WireGuard UDP endpoint, is decrypted on the
gateway, then leaves the gateway toward private Nebius IPs with the source
rewritten to the gateway private IP. This is not NAT from Nebius to the
Internet and not a reverse proxy.

This default is the practical mode for point-to-site access because private VMs
do not need public IPs or routes back to the WireGuard client subnet.

`nat_mode = false` keeps routed WireGuard forwarding without MASQUERADE. Use it
only when the cloud network has an explicit return path to
`wireguard_tunnel_cidr` and security policy allows that routed traffic. Without
those return routes, clients can send packets into the VPC but replies will not
find their way back.

## Usage

```hcl
module "wireguard_gw" {
  source = "./platform-infra/modules/wireguard-gw"

  parent_id           = "project-xxxxxxxx"
  network_id          = "vpcnetwork-xxxxxxxx"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  name                = "demo-wg"
  platform            = "cpu-d3"
  preset              = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  boot_disk_size_gib  = 64
  ssh_user_name       = "ubuntu"
  ssh_public_key      = "ssh-ed25519 AAAA... user@example"

  wireguard_tunnel_cidr = "10.8.0.1/22"
  wireguard_listen_port = 51820
  nat_mode              = true

  local_subnets = [
    "10.0.0.0/8",
  ]

  client_default_dns = [
    "1.1.1.1",
    "1.0.0.1",
  ]
}
```

For a pinned Git source:

```hcl
module "wireguard_gw" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/wireguard-gw?ref=v0.1.0"
  # same inputs as above
}
```

## Inputs

Required VM inputs:

- `parent_id`
- `network_id`
- `subnet_id`
- `name`
- `platform`
- `preset`
- `source_image_family`
- `boot_disk_size_gib`
- `ssh_public_key`

`boot_disk_size_gib` is explicit. `nebius-cxcli` resolves the live
platform/preset metadata, applies its shared `compute.boot_disk_defaults`
policy, and renders the recommended value for this wrapper.

Disk security inputs are passed through to the shared VM module:
`boot_disk_encryption_enabled` enables provider-managed encryption only for
`NETWORK_SSD_NON_REPLICATED` or `NETWORK_SSD_IO_M3`; `NETWORK_SSD` is
platform-encrypted. `boot_disk_deletion_protection` enables provider-side
deletion protection on the created boot disk.

WireGuard inputs:

- `wireguard_tunnel_cidr`: server tunnel interface CIDR.
- `wireguard_listen_port`: UDP listener port, default `51820`.
- `nat_mode`: enable source NAT from VPN clients to private VPC targets,
  default `true`.
- `endpoint_host`: optional DNS name or public IP written to generated client
  configs. When unset, the VM tries to detect its public IP.
- `local_subnets`: default private destination CIDRs for new clients.
- `client_default_dns`: DNS servers written to new client configs. The module
  defaults to Cloudflare public resolvers `1.1.1.1` and `1.0.0.1`; override
  this list when clients should use private DNS.
- `client_default_persistent_keepalive`: default keepalive interval, default
  `25`.
- `clients`: optional first-boot seed clients. For day-2 clients, prefer the
  gateway-local generator command.
- `labels`: extra Nebius labels. The module always adds `component` and `name`
  labels derived from the module and `name` input, and caller-provided labels
  can override them.

Client seed shape:

```hcl
clients = [
  {
    name                     = "laptop"
    client_wg_tunnel_address = "10.8.0.2/32" # optional; auto-assigned when omitted
    local_subnets            = ["10.0.0.0/8"]
    dns                      = ["1.1.1.1"]
    persistent_keepalive     = 25
  }
]
```

Public IP inputs:

- Default: `create_public_ip_allocation = true`
- Reuse existing: `create_public_ip_allocation = false` plus
  `public_ip_allocation_id`

## Outputs

- `instance_id`
- `private_ip`
- `public_ip`
- `public_ip_allocation_id`
- `wireguard_listen_port`
- `wireguard_clients_path`
- `wireguard_client_registry_path`
- `wireguard_client_generator_path`

## Client Connection

Retrieve a generated client config with SSH, then use it with the WireGuard CLI
tools:

```bash
umask 077
ssh -i ~/.ssh/id_ed25519 ubuntu@<wireguard-public-ip> \
  'sudo cat /var/lib/wireguard/clients/<client-name>/<client-name>.conf' \
  > <client-name>.conf
chmod 600 <client-name>.conf
```

On macOS, install the command-line tools with Homebrew:

```bash
brew install wireguard-tools
wg-quick up ./<client-name>.conf
wg-quick down ./<client-name>.conf
```

macOS may ask for an admin password when changing the tunnel state. This
workflow uses only `wireguard-tools`; there is no HTTP UI on the VPN gateway.

## Security Notes

- SSH is key-only, root SSH login is disabled, and SSH forwarding is disabled
  on this host. Use `modules/ssh-jumphost` when you need ProxyJump behavior.
- UFW denies inbound traffic by default and opens only the WireGuard UDP
  listener plus SSH administration. Routed traffic is allowed from `wg0` to the
  VM's VPC interface for client-initiated private access; unsolicited routed
  traffic back toward VPN clients is not opened.
- fail2ban protects SSH with UFW-backed bans, auditd watches SSH/sudo/identity
  and WireGuard config/state paths, and unattended security upgrades are
  enabled without automatic reboot.
- IPv4 forwarding is enabled for WireGuard, while redirect-related sysctls are
  disabled to reduce routing spoofing and redirect risks.
- Client `.conf` files contain private key material and pre-shared keys; treat
  them as secrets.
- Distribute each generated client config to one device or user only.
- `nebius-wireguard-client list` redacts stored key material; retrieve or
  rotate client configs from the per-client files under
  `/var/lib/wireguard/clients/`.
- Client private keys are generated on the VM and are not Terraform outputs.
- Keep Terraform state encrypted and access-controlled.

## References

- WireGuard install guide: <https://www.wireguard.com/install/>
- WireGuard quick start: <https://www.wireguard.com/quickstart/>
- WireGuard routing and namespaces: <https://www.wireguard.com/netns/>
- `wg(8)`: <https://git.zx2c4.com/wireguard-tools/about/src/man/wg.8>
- `wg-quick(8)`: <https://git.zx2c4.com/wireguard-tools/about/src/man/wg-quick.8>
