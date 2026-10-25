# Custom NAT gateway

Terraform module implementing Nebius's
[custom NAT gateway topology](https://docs.nebius.com/vpc/routing/custom-nat-gateway).
It composes the sibling `vm` module and creates an explicit dedicated subnet,
stable private/public allocations, a direct-egress gateway route table, and one
workload table per reviewed routing policy. Each workload table has a default
route to the gateway's private allocation and the supplied non-default routes.

The module does not attach workload subnets. The caller must verify the guest
and next hops before changing consumer route fields, preserve original
attachments, and restore them before destroying the gateway. `nebius-cxcli`
provides that lifecycle and live Python SDK discovery/quota checks.

The gateway is one regular CPU VM. Defaults are `cpu-d3`, `4vcpu-16gb`, a `/28`
subnet and a 20 GiB `NETWORK_SSD` boot disk. Callers must validate platform/preset
availability, zero GPUs, disk quota and a ready Ubuntu 24.04 driverless image's
minimum disk size. Supply its immutable `source_image_id`.

| Input | Purpose |
| --- | --- |
| `parent_id`, `network_id`, `name` | Project, network and resource prefix |
| `gateway_subnet_cidr` | Explicit IPv4 CIDR or an automatically allocated prefix |
| `platform`, `preset` | Live-validated CPU VM size |
| `source_image_id`, `boot_disk_size_gib`, `boot_disk_type` | Image and disk selection |
| `ssh_user_name`, `ssh_public_key`, `admin_cidrs` | Non-root key-only administration from restricted sources |
| `source_cidrs` | Verified network private ranges allowed to forward |
| `routing_policies` | Map of policy keys to preserved non-default IPv4 routes and allocation IDs |
| `labels` | Additional labels; component ownership label is reserved |

Outputs include `instance_id`, `boot_disk_id`, `subnet_id`, private/public IPs and
allocation IDs, `gateway_route_table_id`, `route_table_ids`, and the expected
`guest_config_sha256` readiness digest. IP outputs follow the underlying VM module.

Cloud-init installs persistent IPv4 forwarding and owned iptables chains, resolves
the actual egress interface, restricts SSH ingress, blocks forwarding to link-local
metadata addresses and masquerades allowed source ranges. The read-only command
`sudo -n /usr/local/sbin/nebius-nat status` verifies the digest, forwarding flag,
rule order and hooks. Check it over independently authenticated SSH after
cloud-init and `nebius-nat.service` succeed. Single-VM reboot/replacement interrupts
connections; this module does not provide HA.

See [the minimal root](examples/minimal/main.tf). Use Terraform `>=1.10,<2` and
Nebius provider `>=0.6.8,<0.7`; commit the consuming root's provider lock file.
Run `terraform init -backend=false`, `terraform validate`, and `terraform test`
in an isolated module copy for local mock-provider verification.
