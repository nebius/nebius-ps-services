# Custom NAT gateway

Add the opt-in `nat-gateway` infrastructure component with the component wizard:

```bash
nebius-cxcli component add infra:nat-gateway --config ./deployments/tenant/project/config.yaml
```

The wizard discovers the project's CPU-only platforms and presets through the
Python SDK. It prefers `cpu-d3` / `4vcpu-16gb` when available, shows other live
choices, resolves a ready Ubuntu 24.04 driverless image to its immutable ID, and
checks the image's minimum boot disk size. The default boot disk is 20 GiB
network SSD. A quota preview can remain unresolved when saving a draft; deployment
requires confirmed headroom for planned resource demand. Quota headroom is not
a capacity reservation. CPU choices do not use the GPU Capacity Advisor.

Select an existing network or a VPC component from the same deployment, then
select workload subnets. The gateway receives a **dedicated explicit subnet**,
using a provider-allocated `/28` by default. A specific IPv4 CIDR is also accepted
after pool, allocation and subnet overlap validation. Extend network pools
separately when space is insufficient. Network private ranges define the guest's
forwarding sources; subnets with inherited pools do not provide distinct CIDR
security boundaries. Provide restricted administration CIDRs and an SSH public
key. Password/root SSH access is disabled.

The wizard records the source table and a fingerprint of its routes. Distinct
source policies receive distinct owned tables. Each table retains non-default
static routes and changes `0.0.0.0/0` to the gateway's stable private allocation.
More-specific routes remain exceptions to NAT, and in-network traffic follows
Nebius's routing rules. The gateway subnet has its own direct-egress table.
Redistributed, ambiguous and unsupported route snapshots are rejected.

Terraform owns the subnet, VM, boot disk, private/public allocations, route tables
and routes. It also owns attachment fields on VPC component subnets. For external
subnets, the wizard requires explicit delegation of **only `route_table_id`** to
cxcli; remove competing management of that field first. SDK updates preserve the
rest of the subnet specification and metadata and use the resource version.

## Deployment and SSH trust

```bash
nebius-cxcli render ./deployments/tenant/project/config.yaml
nebius-cxcli deploy ./deployments/tenant/project/config.yaml --dry-run
nebius-cxcli deploy ./deployments/tenant/project/config.yaml \
  --ssh-known-hosts-file ./trusted_hosts --ssh-private-key ~/.ssh/id_ed25519
```

The first Terraform stage prepares infrastructure while retaining workload route
attachments. Deployment then checks allocation ownership, route contents,
cloud-init completion, the persistent forwarding service, and the exact guest
firewall/NAT rules over strictly authenticated SSH. It admits a separate plan
that can change only the selected subnet route fields, applies Terraform-owned
attachments, then updates delegated external attachments. Application setup runs
after this completes. Shared quota demand is assessed from planned creates and
growth; replacements conservatively require transient headroom.
Disabling routing does not disable provisioning checks: new or changed gateway
resources still require a valid CPU preset, image, subnet and quota admission.

Provision host keys in `trusted_hosts` from an independently trusted source. The
CLI never accepts an unverified key automatically. On a first deployment, missing
trust leaves the gateway provisioned with workload routes unchanged. Establish
trust, then rerun the same deploy command. The default trust path is the project's
`generated/ssh_known_hosts`; an explicit file outside generated artifacts is
recommended. Private keys and trust contents are never saved in config or the
routing journal.

This is a single VM. Reboots and replacement interrupt connections; there is no
automatic failover or fallback to direct workload egress. Disable and restore
routing before changing an established gateway's topology or compute/image
configuration. Stable allocations are independent of the VM.

## Inspection and recovery

```bash
nebius-cxcli nat status ./deployments/tenant/project/config.yaml
nebius-cxcli nat status ./deployments/tenant/project/config.yaml --ssh-known-hosts-file ./trusted_hosts
```

Status separates VM/allocation observations, route attachment observations and
optional guest verification. It explicitly reports workload egress as unmeasured;
verify outbound traffic from a selected workload separately before production
qualification.

Original attachments and operation progress live in the existing protected
regular Object Storage backend at `<state-key>.nat.json`. The conditional shared
lock is `<state-key>.nat.lock`; it never expires automatically. Filesystem buckets
and anonymous access to these objects are rejected. Terraform's state lock and
the existing local deployment lock remain in use.
The record retains the rendered module identity. Recovery observes Terraform
state when a partial apply has not published root outputs, and checks attachment
ownership before resuming. Terraform-owned subnets cannot delegate their route
field to the SDK, including while outputs are unavailable.

Rerun deploy after an ordinary failure. A known SDK operation is polled and its
attachment observed before continuing. An unacknowledged submission is not
automatically repeated. After independently confirming all previous writers and
requests have stopped or reached a terminal state, use the explicit controls:

```bash
nebius-cxcli nat unlock ./deployments/tenant/project/config.yaml \
  --owner LOCK_OWNER --confirm-writers-stopped
nebius-cxcli nat reconcile ./deployments/tenant/project/config.yaml \
  --confirm-requests-settled
```

Use `unlock` only for an abandoned lock with the exact owner from status. It does
not clear pending operations. `reconcile` observes pending attachments and updates
the recovery record without changing routes. Conflicting third-party attachments
remain blocked and are never overwritten automatically.

## Restoration, refresh and removal

1. Set the component's `routing.enabled` to `false`, render, and deploy. This
   restores recorded external attachments and the VPC component's original
   route fields while retaining the gateway resources. Guest SSH readiness is
   unnecessary for restoration. Source-route, image availability and network-pool
   drift do not prevent a route-only restoration. Provisioning changes in the
   same plan still require their normal checks. The saved original route takes
   precedence over a concurrently edited VPC route setting during detach.
2. To revise source policies, run `nat refresh-routes CONFIG_YAML`. This updates
   config snapshots only. Review the diff, set `routing.enabled: true`, render,
   and deploy to reactivate through readiness checks.
3. To retire the gateway, remove the detached component with `component remove`,
   render, and deploy. Retirement requires the shared detached record and checks
   that no subnet still consumes an owned NAT table. External resources are not
   deleted. A retry also finalizes the ownership record if a prior removal
   completed before the CLI recorded completion.

Direct `terraform apply`/`destroy` CLI commands reject NAT configuration or NAT
resources remaining in state. Generated NAT roots also include an execution
precondition directing operators and CI to the deployment lifecycle. Do not
override its internal authority or route variables manually.

The reusable Terraform module can also be consumed independently; its caller
then owns readiness, quota admission, attachment, recovery and retirement.

## Sources

- [Nebius custom NAT gateway](https://docs.nebius.com/vpc/routing/custom-nat-gateway)
- [Routing overview](https://docs.nebius.com/vpc/routing/overview)
- [Subnet resource and route attachment](https://docs.nebius.com/terraform-provider/reference/resources/vpc_v1_subnet)
- [VPC quotas and limits](https://docs.nebius.com/vpc/resources/quotas-limits)
- [Object Storage S3 API compatibility](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility)
