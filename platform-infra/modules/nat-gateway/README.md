# Custom NAT gateway

Terraform module implementing Nebius's
[custom NAT gateway topology](https://docs.nebius.com/vpc/routing/custom-nat-gateway).
It composes the sibling `vm` module, creates or reuses a gateway subnet, and owns
stable private/public allocations plus one workload table per reviewed policy.
Creation owns an explicit dedicated subnet and direct-egress table. Reuse reads
the selected subnet and never adopts or modifies its pools or routes. Each workload table has a default
route to the gateway's private allocation and the supplied non-default routes.

The module does not attach workload subnets. The caller must verify the guest
and next hops before changing consumer route fields, preserve original
attachments, and restore them before destroying the gateway. `nebius-cxcli`
provides that lifecycle and live Python SDK discovery/quota checks. It assigns a
distinct policy key/table to each target subnet. Reuse suitability and same-network
routing must be verified by the caller before allocation/activation.

For same-deployment VPC reuse, pass `module.vpc.subnets["gateway"].id`; do not
make VPC subnet inputs refer back to NAT outputs. Supply complete concrete route
override maps in separate preparation/activation plans. Existing singleton subnet
resource addresses have explicit Terraform `moved` blocks to preserve identity.

The gateway is one regular CPU VM. Defaults are `cpu-d3`, `4vcpu-16gb`, a `/28`
subnet and a 40 GiB `NETWORK_SSD` boot disk. Callers must validate platform/preset
availability, zero GPUs, disk quota and a ready Ubuntu 24.04 driverless image's
minimum disk size. Supply its immutable `source_image_id`.

| Input | Purpose |
| --- | --- |
| `parent_id`, `network_id`, `name` | Project, network and resource prefix |
| `gateway_subnet_mode`, `gateway_subnet_id` | Create (default), or reuse an independently owned public-capable direct-egress subnet |
| `gateway_subnet_cidr` | Explicit IPv4 CIDR or an automatically allocated prefix |
| `platform`, `preset` | Live-validated CPU VM size |
| `source_image_id`, `boot_disk_size_gib`, `boot_disk_type` | Image and disk selection |
| `ssh_user_name`, `ssh_public_key` | Non-root key-only administration from any IPv4 source, protected by fail2ban |
| `source_cidrs` | Verified network private ranges allowed to forward |
| `routing_policies` | Map of policy keys to preserved non-default IPv4 routes and allocation IDs |
| `labels` | Additional labels; component ownership label is reserved |
| `ssh_host_keys` | Sensitive runtime-only Ed25519 private/public map; empty preserves guest-generated identity |

Outputs include `instance_id`, `boot_disk_id`, `subnet_id`, private/public IPs and
allocation IDs, `gateway_route_table_id`, `route_table_ids`, and the expected
`guest_config_sha256` readiness digest. IP outputs follow the underlying VM module.

Cloud-init installs persistent IPv4 forwarding and owned iptables chains, resolves
the actual egress interface, permits IPv4 SSH with key-only authentication and
fail2ban (3 failures per 600 seconds, 3600-second bans), blocks forwarding to link-local
metadata addresses and masquerades allowed source ranges. The read-only command
`sudo -n /usr/local/sbin/nebius-nat status` verifies the digest, forwarding flag,
rule order/hooks, effective SSH policy, IPv4 listener and active fail2ban jail.
The digest also binds the installed guest/security programs. A dedicated ban
chain keeps dynamic ban entries separate from the exact static NAT rules. Check it over independently authenticated SSH after
cloud-init and `nebius-nat.service` succeed. Single-VM reboot/replacement interrupts
connections; this module does not provide HA.

See [the minimal root](examples/minimal/main.tf). Use Terraform `>=1.10,<2` and
Nebius provider `>=0.6.74,<0.7`; commit the consuming root's provider lock file.
Run `terraform init -backend=false`, `terraform validate`, and `terraform test`
in an isolated module copy for local mock-provider verification.

## SSH policy upgrade

The cxcli deployment owner prepares a pinned Ed25519 identity before creating a
fresh VM and supplies `ssh_host_keys` through sensitive runtime inputs. Cloud-init
installs that pair. Private material belongs only in protected runtime storage,
Terraform plans/state and authenticated Compute metadata; never commit it or put
it in config, tfvars, reports or outputs. Protect state and Compute read access.
An empty map preserves the previous cloud-init bytes and sensitivity metadata for
retained VMs with guest-generated keys, which require independent operator trust.
Only public map field names are declassified; populated key values stay sensitive.
Adding or changing a host identity changes cloud-init and replaces its VM and boot
disk; cxcli recovers the existing identity instead of rotating it automatically.

The `admin_cidrs` module argument is removed. Existing callers must detach
workload routing using their previous matching CLI/module revision before
upgrading and removing that argument. Cloud-init changes replace the VM and boot
disk; stable allocations and routing resources remain independent. Establish new
exact host trust and verify guest security before reattaching workload routes.
The [cxcli guide](../../../services/nebius-cxcli/docs/nat-gateway.md#upgrading-from-administration-cidr-restrictions)
contains the full upgrade and rollback sequence.

The guest listens on IPv4 only, disables root/password/keyboard-interactive login,
and requires the configured public key. Fail2ban reads the system journal and
counts failed public-key authentication. Its service starts after NAT creates the
ban chain; restarting NAT preserves bans. No UFW or VPN-specific rules are installed.

Run the [isolated systemd tests](tests/systemd/README.md) for effective SSH and
ban enforcement, in addition to the Terraform mock-provider tests.
