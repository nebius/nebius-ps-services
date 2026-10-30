# Custom NAT gateway

Add the opt-in `nat-gateway` infrastructure component with the component wizard:

```bash
nebius-cxcli component add infra:nat-gateway --config ./deployments/tenant/project/config.yaml
```

The wizard discovers the project's CPU-only platforms and presets through the
Python SDK. It prefers `cpu-d3` / `4vcpu-16gb` when available, shows other live
choices, resolves a ready Ubuntu 24.04 driverless image to its immutable ID, and
checks the image's minimum boot disk size. The default boot disk is 40 GiB
network SSD. A quota preview can remain unresolved when saving a draft; deployment
requires confirmed headroom for planned resource demand. Quota headroom is not
a capacity reservation. CPU choices do not use the GPU Capacity Advisor.

The wizard follows **network → gateway subnet → workload subnets → VM and access →
verification → summary**. `q` goes back one interactive step, preserving answers; `qq` discards
this component edit. Network, subnet, CPU, preset and image selectors offer a
live refresh. Planned deployment resources are labelled separately; exact state
identities reconcile already-created managed resources. Names and CIDRs never
prove ownership. Existing managed subnet rows omit the internal ownership suffix;
ID, name, CIDR and capabilities remain visible and selections retain exact bindings.
Valid explicit disk sizes remain unchanged; the selected image's
minimum still applies.
Workload subnets require an explicit selection; refresh is never preselected.
Deselecting and reselecting a workload subnet while backtracking preserves its
original route snapshot within that network.

Normal wizard completion saves `routing.enabled: true`, including when editing a
configuration with routing disabled. There is no separate activation question:
the final summary shows route changes and ownership without another prompt.
The wizard saves configuration only. Continue with **validate → render → deploy**;
deploy provisions the gateway, verifies readiness, activates workload routes,
then runs automatic Internet tests if enabled. Selecting external workload
subnets delegates their route-table attachment to cxcli; the wizard explains
this before selection. Remove competing attachment ownership before deployment.

Choose the parent VPC network, then select the gateway subnet directly from one
picker: eligible existing subnets first, eligible planned subnets next, followed
by **Create a dedicated public-facing gateway subnet** and **Refresh live inventory**.
There is no separate create/reuse question. Creation remains the default for new
configurations; editing preserves a valid saved subnet selection. Creation asks
for a provider-allocated prefix (default `/28`) or an explicit CIDR. Selecting a
subnet records reuse and its ID or VPC binding automatically.

Unavailable subnets are explained separately and cannot be selected. A saved
selection that is no longer eligible produces a notice and defaults the picker
to creation. An empty successful inventory still offers creation; discovery
failures require retry or backtracking. `q` returns through displayed steps, so
reuse skips the creation-only CIDR step in both directions. Changing VPC clears
dependent subnet and workload selections.
Resolving a saved live VPC ID to its exact managed binding preserves the gateway,
workload selections and original route snapshots. Partial or filtered VPC
inventory requires retry even when the provider reports no error. Refreshing
inventory cannot make the gateway selectable as a workload through a different
ID/binding representation.

Reuse requires private addressing, public-IP allocation
capability and a direct default Internet route in the same project and network.
Planned subnets with public-pool inheritance disabled and no public CIDRs are
unavailable for gateway placement.
The selector displays public-IP capability and effective default routing separately:
a private VM can have shared default egress even without its own public IP.
Reused subnets, pools and route tables remain with their existing owner. A created
gateway subnet has its own direct-egress table. Extend pools separately if needed.
Network private ranges define forwarding sources; inherited pools do not create
separate subnet CIDR security boundaries. Supply a public SSH key. SSH accepts
connections from any IPv4 source; no administration CIDR list is required.
Public-key authentication is mandatory; root, password and keyboard-interactive
login are disabled. Fail2ban blocks a source for one hour after three failed
logins within ten minutes, including failed public-key attempts. Forwarding
remains limited to verified network private ranges (`source_cidrs`).

Select one or more private workload subnets that should use the gateway. The
gateway subnet cannot be selected. **Each workload subnet receives its own owned
route table**, even when source routes are identical or a source table is shared.
The wizard snapshots the complete supported source policy and its fingerprint.
Every non-default static route is preserved; `0.0.0.0/0` points to the gateway's
stable **private allocation ID**. More-specific routes remain exceptions to NAT.
Redistributed, ambiguous and unsupported snapshots are rejected.

Terraform owns permanent gateway resources and the workload tables/routes. It
also owns attachment fields on VPC component subnets. External subnets explicitly
delegate only `route_table_id` to cxcli; remove competing field ownership first.
Conditional SDK updates preserve the rest of their specification and metadata.
Temporary verification VMs, disks and private allocations are the narrowly scoped
SDK-created exception. A reused gateway subnet is never adopted or deleted.

## Deployment and SSH trust

The CLI selects exactly the identity matching `ssh_public_key`: the supplied
`--ssh-private-key`, otherwise a matching agent key, otherwise one matching
standard local key. Explicit private-key files must be owner-only regular files.
For an encrypted key, load it into `ssh-agent` and omit the private-key option.
Missing, mismatched or ambiguous keys fail locally before SSH. Unrelated agent
keys are never offered and cannot consume the gateway's three-attempt limit.

```bash
nebius-cxcli render ./deployments/tenant/project/config.yaml
nebius-cxcli deploy ./deployments/tenant/project/config.yaml --dry-run
nebius-cxcli deploy ./deployments/tenant/project/config.yaml
```

The first Terraform stage prepares infrastructure while retaining workload route
attachments. Deployment then checks allocation ownership, route contents,
cloud-init completion, the persistent forwarding service, and the exact guest
firewall/NAT rules over strictly authenticated SSH. It also verifies effective
key-only SSH policy, IPv4-only port 22 listening and the active fail2ban jail.
The expected digest includes the installed guest and security program hashes.
Ban entries occupy a dedicated chain before SSH acceptance and survive NAT-service
restarts. Security checks observe state without repairing it. It admits a separate plan
that can change only the selected subnet route fields, applies Terraform-owned
attachments, then updates delegated external attachments. Application setup runs
after this completes. Shared quota demand is assessed from planned creates and
growth; replacements conservatively require transient headroom.
The route-only check distinguishes provider-generated metadata from authored
subnet fields. Timestamps, status and derived CIDR metadata can become unknown
during an update. Inherited public pools can also become unknown when the VPC
configuration leaves their selection to the network. These refreshes do not
authorize changes to subnet identity, labels, CIDRs or configured pool selection;
unknown authored values, other resource updates and replacements still block apply.
Pool-default exceptions require the canonical VPC source and exact selected
resource address. Custom modules and unresolved subnet-input bindings receive
no such exceptions. Catalog defaults and Terraform's optional-null defaults are
included when determining whether public pools are inherited.
Disabling routing does not disable provisioning checks: new or changed gateway
resources still require a valid CPU preset, image, subnet and quota admission.

For operational preparation or detachment, explicitly set `routing.enabled: false`
in YAML, then render and deploy. For a new gateway this leaves workload routes
unchanged; for an active gateway it restores the original attachments. Deploy
honors this setting. Completing the normal wizard again sets routing to `true`.

### Automatic private-subnet verification

`routing.verification.enabled` defaults to `true` and is selected in the wizard.
Set it to `false` to skip data-path testing while retaining infrastructure,
trusted guest checks and route activation. When enabled, deployment checks the selected SSH identity and effective gateway
TCP forwarding, a live small CPU preset/image, transient instance/vCPU/disk/allocation quota and private
address capacity **before changing attachments**. Planned VPC attachments use
complete concrete override maps, avoiding a VPC-to-NAT dependency cycle.

After activation, cxcli creates one temporary private-only VM in **every selected
workload subnet**, sequentially. Each boots a standard-library probe with no
package download, public IP or service account. It makes a new direct IPv4 HTTPS
request to `api.ipify.org`, pins a resolved public address, requires verified TLS 1.2+, disables
proxy/redirect use, and checks the reported source IP against the gateway's public
IP. An endpoint covered by a retained more-specific route is ineligible. A successful
test proves this sampled outbound path, not all destinations or workload behavior.

Deploy configures and collects these tests automatically over SSH through the
trusted gateway, including deployments whose Terraform plan has no changes.
There is no separate test command, manual probe-key setup or manual SSH step.
Serial-log reads, logging permissions and retained log samples are not required.

Each probe gets a temporary deployment-generated Ed25519 host identity through
Compute cloud-init and a password-locked, unprivileged SSH user authorized with
the already-selected client public key. Both SSH hops use explicit identities
and strict pinned host checking. Client private keys remain local; SSH agent
forwarding is disabled. Before connecting and before accepting a result, SDK
readback binds the private address to the exact owned VM, subnet, allocation and
disk. Gateway forwarding is inspected before activation, without changing its
SSH policy.

The guest publishes a complete JSON result atomically into a root-owned directory.
The test user can read the result but cannot change it. The collector validates
the current activation/run, configuration and program digest, boot/freshness and
observed egress IP. Each SSH attempt has a 30-second budget, with a bounded
supervisor shutdown grace; the complete result wait is limited to five minutes.
Output is capped before buffering, and the supervisor stops the jump connection
and its descendants before resource cleanup. Temporary host private keys are
excluded from configuration, journals and reports; they exist only in protected
runtime files/memory and the authenticated Compute provisioning request/metadata.

Failed or inconclusive enabled tests fail deployment. cxcli restores this
activation's attachment preimages (which may already be NAT tables), using a
restricted Terraform plan for managed fields and conditional SDK updates for
external fields. Concurrent drift is preserved and reported for recovery. The
gateway remains for diagnosis. Each temporary resource has durable write-ahead
intent, an idempotency key and recorded operation/resource IDs. Cleanup deletes
only matching owned resources, VM before disk/allocation, and remains pending
when outcomes are ambiguous. Rerun deploy to recover interrupted activations;
unknown submissions require explicit settled-request reconciliation first.
Removing the component cannot bypass an unfinished activation or pending probe
cleanup, even when its route attachments are already detached.

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

For a fresh gateway, deployment generates and retains a pinned Ed25519 host
identity before Terraform provisioning. The private key stays in owner-only
project runtime storage under `.nebius-cxcli/nat-host-keys/` and is passed through
a sensitive runtime-only module input. It never goes into `config.yaml`, generated
tfvars, routing journals, reports or ordinary outputs. Terraform plans/state and
Compute cloud-init necessarily contain the private material: protect both state
storage and Compute read access. A dry run uses an ephemeral identity when no
persistent one exists.

Cloud-init installs the selected pair. On a later deployment, the CLI recovers
and validates that same pair from the exact instance's authenticated Compute
metadata; it does not rotate the key from a stale local cache. Before publishing
a missing public pin in `generated/ssh_known_hosts`, it verifies the instance,
boot disk and public allocation/address against Terraform outputs and rechecks
provider identity. Existing entries remain unchanged; every connection uses
strict host-key checking. SSH trust does not depend on serial logs, network-first
key acceptance or the machine-global known-hosts file.

Render preserves the current default trust file as protected runtime data; it
does not include pins in configuration snapshots or accept them from staged
render artifacts. Concurrent host enrollment prevents publication until retried.
Repeated preflights and coordinated source/stage planners reuse the same prepared
host identity and route overrides only when NAT configuration and rendered module
identities match. These inputs remain in memory during execution; private keys
are not copied into admission or recovery records.
Interrupted route restoration recovers retained identities before planning and
does not generate new host keys for absent or removed gateways.

A previously deployed gateway with a guest-generated identity retains its
original cloud-init, host key, VM and boot disk. Supply its independently verified
public host key in `generated/ssh_known_hosts`, or use an explicit trust file:

```bash
nebius-cxcli deploy ./deployments/tenant/project/config.yaml \
  --ssh-known-hosts-file ./trusted_hosts --ssh-private-key ~/.ssh/id_ed25519
```

Explicit trust files are never populated or changed by deployment. An existing
pin that fails strict verification requires operator investigation; deployment
never replaces it automatically. A missing key on a retained guest-generated VM
stops before route activation. Rerun deploy after establishing trust. Public pins
are written with mode `0600`; trust and private runtime files are excluded from
the managed deployments-root Git repository. Render with the updated module before
creating a fresh gateway so its runtime-only identity input is present.

This is a single VM. Reboots and replacement interrupt connections; there is no
automatic failover or fallback to direct workload egress. Disable and restore
routing before changing an established gateway's topology or compute/image
configuration. Stable allocations are independent of the VM.

### Upgrading from administration CIDR restrictions

`inputs.admin_cidrs` and the module's `admin_cidrs` input have been removed.
Configurations containing the old field fail with conversion guidance; it is
never silently ignored.

1. Retain the previous matching CLI and module revision. With that version and
   the original configuration, set `routing.enabled: false`, render and deploy
   to restore workload routes. Verify the gateway is detached.
2. Upgrade the CLI and module together, then remove `inputs.admin_cidrs` (or the
   direct module argument). Keep forwarding `source_cidrs` unchanged.
3. Render and review the plan. Cloud-init changes replace the VM and boot disk;
   stable allocations and owned routing resources remain independent.
4. Deploy the detached gateway, establish new exact host trust from an
   independently trusted source, then enable routing and deploy. Activation
   requires successful guest and SSH security verification.

Rollback uses the previous matching CLI, module and configuration after detaching
through the currently installed version. Rebuilding the guest interrupts service;
there is no live in-place migration or mixed-version guest protocol.

### Local security verification

The [isolated Ubuntu fixture](../../../platform-infra/modules/nat-gateway/tests/systemd/README.md)
renders the real cloud-init template and tests both SSH startup models, key
selection, banning, restarts and rejection of invalid security policy. cxcli CI
runs this fixture. Container proof does not establish live Nebius provisioning
or workload egress.

## Inspection and recovery

```bash
nebius-cxcli nat status ./deployments/tenant/project/config.yaml
nebius-cxcli nat status ./deployments/tenant/project/config.yaml --ssh-known-hosts-file ./trusted_hosts
```

Status separates current VM/allocation and attachment observations, optional guest
verification, and the last activation's probe results and cleanup state. Historical
probe success is not a continuous health check. Skipped tests remain unmeasured.

Lifetime original attachments, per-activation preimages, probe results and operation progress live in the existing protected
regular Object Storage backend at `<state-key>.nat.json`. The conditional shared
lock is `<state-key>.nat.lock`; it never expires automatically. Filesystem buckets
and anonymous access to these objects are rejected. Terraform's state lock and
the existing local deployment lock remain in use.
The record retains the rendered module identity. Recovery observes Terraform
state when a partial apply has not published root outputs, and checks attachment
ownership before resuming. Terraform-owned subnets cannot delegate their route
field to the SDK, including while outputs are unavailable.

Rerun deploy after an ordinary failure. A known SDK operation is polled and its
postcondition observed before continuing. An unacknowledged submission is not
automatically repeated. After independently confirming all previous writers and
requests have stopped or reached a terminal state, including probe SSH collectors
and their descendants on the original runners, use the explicit controls:

```bash
nebius-cxcli nat unlock ./deployments/tenant/project/config.yaml \
  --owner LOCK_OWNER --confirm-writers-stopped
nebius-cxcli nat reconcile ./deployments/tenant/project/config.yaml \
  --confirm-requests-settled
```

Use `unlock` only for an abandoned lock with the exact owner from status. It does
not clear pending operations. `reconcile` observes pending attachments and updates
the recovery record without changing routes. Conflicting third-party attachments
remain blocked and are never overwritten automatically. If supervised SSH shutdown
cannot be confirmed, probe cleanup stays pending; `--confirm-requests-settled` also
attests that these collectors and descendants have stopped. Normal successful and
failed tests perform cleanup automatically.

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
- [OpenSSH jump-host configuration](https://man.openbsd.org/ssh_config#ProxyJump)
- [ipify IPv4 API](https://www.ipify.org/)
- [Routing overview](https://docs.nebius.com/vpc/routing/overview)
- [Subnet resource and route attachment](https://docs.nebius.com/terraform-provider/reference/resources/vpc_v1_subnet)
- [VPC quotas and limits](https://docs.nebius.com/vpc/resources/quotas-limits)
- [Object Storage S3 API compatibility](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility)
