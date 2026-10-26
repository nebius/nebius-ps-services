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
when the selected pool has an available, aligned range. cxcli creates this subnet;
you do not need to prepare it manually. Extend network pools separately when
space is insufficient. Network private ranges define the guest's
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

### Private address capacity

The wizard and initial preparation show an advisory address-space assessment.
A well-formed draft can be saved with insufficient or unresolved capacity, but
missing network or forwarding-source CIDRs must still be resolved. Every fresh
provisioning plan that needs new private space requires a successful assessment
before apply. Disabled routing does not bypass this gate.

The assessment uses each network's private pool identities, enabled blocks and
mask restrictions. It subtracts child-pool reservations, including unattached
and pending reservations, and direct allocations, including dynamic addresses
without a subnet ID. Inherited subnets do not reserve the entire parent pool.
It combines the plan's gateway, workload subnet, pool and private-allocation
demand, checks explicit CIDRs and contiguous prefix fits, and reports requested
addresses, free space and the largest eligible block. Unknown membership,
incomplete inventory or exhausted assessment limits block new reservations;
they are never treated as empty pools.
An omitted subnet-pool specification inherits network pools; a false or unknown
flag alone does not prove a dedicated reservation. Repeated automatic prefix
requests retain their multiplicity, and unresolved prefix expansion blocks
admission. Expanding a derived pool also checks the additional ranges against
its upstream pools before crediting the new space.

Dynamic VM interfaces add host-address demand when they use inherited pools.
Kubernetes networking can allocate Service and Pod blocks implicitly; when a
changed cluster or node group shares a NAT parent pool through an inherited
subnet, admission reports unresolved demand rather than guessing its footprint.
Use a dedicated explicit workload subnet for this composition. Canonical
same-deployment VPC bindings prove that isolation even before subnet IDs exist;
custom or ambiguous bindings must be resolved. Kubernetes subnet sizing remains
subject to its separate preflight checks. See the provider's
[Kubernetes address requirements](https://docs.nebius.com/kubernetes/networking/requirements#private-ip-address-allocations).

Pending deletions do not free capacity for admission. A replacement must fit
while the old reservation remains; an in-place explicit expansion checks only
its additional range. A retained subnet, VM-only replacement or route-only
restoration needs no spare-space check. Each new plan reads a new snapshot.

After provisioning, cxcli verifies the realized gateway CIDR or prefix, parent
pool ownership and private host-allocation containment before route activation.
This check does not require spare capacity. A feasible snapshot is **not a
reservation**: concurrent allocation or provider ordering between automatic
and explicit requests can still cause apply to fail. cxcli keeps Terraform's
automatic allocation and does not reserve addresses or change apply ordering.

### SSH trust and availability

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
