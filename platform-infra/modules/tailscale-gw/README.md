# Tailscale gateway pair

This module provisions two persistent CPU VMs on Nebius by default. Each member
(`a`, `b`) owns a boot disk and public IP. It wraps the shared `vm` module and uses
a dedicated security group with restricted SSH ingress, UDP 41641, ICMP and
outbound connectivity. Set `replicas = 1` explicitly for a single router.

Use [cxcli's Tailscale workflow](../../../services/nebius-cxcli/docs/tailscale.md)
for enrollment, readiness, rolling updates, verification and retirement. This
module installs a pinned Tailscale package and root management helper. It does
**not** authenticate or advertise routes through Terraform. Any customer's hosted
Tailscale tailnet is supported; cloud provisioning remains Nebius-only.

cxcli deploys new gateways in `infrastructure` stage first, without Tailscale
credentials. Its onboarding wizard and generated administrator request collect
the approved access settings before a subsequent ordinary deploy activates the
pair. Optional SecretStash custody is read directly by the controller through the
Nebius Python SDK; it adds no Kubernetes or ESO dependency to this module.

## Inputs and outputs

Required inputs are `parent_id`, `network_id`, `subnet_id`, CPU `platform` and
`preset`, `ssh_public_key` and nonempty `management_cidrs`. The placement subnet
must support public allocations and outbound Internet access. Defaults are two
members, a 40 GiB NETWORK_SSD disk per member, Ubuntu 24.04 driverless, `ubuntu`
management user and Tailscale `1.104.4`. `member_overrides.a`/`.b` stage per-member
compute or disk changes. The CLI rejects plans replacing both members together.

`members` exports each exact VM, boot disk, public allocation, public/private IP,
network and subnet identity. `high_availability` reports requested topology only;
use client verification to establish actual connectivity and failover.

Runtime-only sensitive inputs `ssh_host_keys` and `bootstrap_user_data` preserve
cxcli's strict host identity and immutable original cloud-init. Those SSH private
keys are present in protected Terraform state and Nebius bootstrap metadata.
Tailscale OAuth secrets, auth keys and node private keys never enter Terraform or
cloud-init. The CLI sends one-time enrollment material through SSH stdin to a
root-only operation file under `/var/lib/nebius-tailscale/`; Tailscale persists node identity on the boot disk.
Do not regenerate retained bootstrap contents to update guest configuration.

No exit node, accepted peer routes, source-IP preservation, Headscale, Kubernetes
agent, MysteryBox/ESO dependency or automatic WireGuard migration is configured.
The VM pair is not a claim about Nebius availability-zone or physical-host spread.

## Validation

```bash
terraform init -backend=false
terraform validate
terraform test
```

The tests use a mock Nebius provider and do not create infrastructure. Live
qualification requires fresh deployment, repeat deployment, reboot, client HTTPS
traffic, bounded failover, staged replacement and retirement in a disposable
project and tailnet. See the cxcli guide for exact commands and evidence limits.

The guest persists an enrollment receipt before authentication, resumes only the
original operation/key, and rejects fenced or foreign identities. Temporary key
material is removed after enrollment or confirmed revocation. Retirement fences
further enrollment before the lifecycle owner removes exact tailnet/cloud resources.
