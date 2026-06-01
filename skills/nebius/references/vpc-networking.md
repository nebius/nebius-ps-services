# Nebius VPC Networking

## Read This When

- the task depends on Nebius network pools or subnet CIDR ownership
- a user reports overlapping CIDRs in the Nebius console
- route logic depends on explicit versus inherited subnets
- a dedicated service subnet or workload subnet design needs review

## Mental Model

- A VPC network has a **parent private pool**.
- A subnet with `use_network_pools=false` has **explicit subnet CIDR(s)** and should be treated as owning them.
- A subnet with `use_network_pools=true` inherits the **parent network pool view** and should not be treated as owning every displayed CIDR.
- A pool assigned to a network is the network's allowed private address
  inventory. A pool assigned to a subnet is the subnet-owned range resources
  use for private addresses.
- Child subnet CIDRs intentionally overlap the parent network pool. That is not
  a conflict. The conflict check is against explicit peer subnet ranges and
  live allocations within the same network.
- Multiple explicit CIDRs on one subnet are modeled as one subnet with one
  explicit private-pool configuration that contains several CIDR entries, not
  as several subnet resources. Prefer one CIDR per subnet in guided UX unless
  the operator has a clear multi-pool reason.

## API Interpretation Rules

### Network

- `network.spec.ipv4_private_pools.pools[*].pool_id`
  Parent network pool references.
- `network.spec.ipv4_private_pools.pools[*].id`
  Some SDK/CLI shapes expose the same pool reference as `id`.
- `network.status.default_route_table_id`
  Network default route table. Subnets inherit it when no custom route table is
  specified.

### Pool

- `pool.spec.cidrs`
  CIDRs carried by that pool.
- `pool.spec.source_pool_id`
  Parent link for derived child pools.
- `pool.status.scope_id`
  Observed SDK/control-plane compatibility boundary. Pools attached to one
  network must be compatible with that network's pool tree/scope. If an
  otherwise matching pool belongs to a different tree or is already assigned,
  skip it and keep scanning candidates.

### Subnet

- `subnet.spec.ipv4_private_pools.use_network_pools`
  Subnet allocation mode.
- `subnet.spec.ipv4_private_pools.pools[*].cidrs`
  Explicit subnet CIDRs when `use_network_pools=false`.
- `subnet.status.ipv4_private_cidrs`
  Useful for comparison, but not safe as the only ownership signal.
- `subnet.status.route_table.default`
  Indicates whether the subnet uses the network default route table.
- `subnet.status.route_table.id`
  Effective route table ID for read-only route inspection.

### Allocation

- `allocation.status.details.allocated_cidr`
  Reserved private or public CIDR/IP. For subnet carve-outs, treat private
  allocations inside the proposed explicit subnet CIDR as blockers.
- `allocation.status.details.subnet_id`
  Subnet that owns the allocation, when reported.
- `allocation.status.details.pool_id`
  Pool backing the allocation, when reported.

## Current Nebius Control-Plane Caveat

Nebius can expose inherited parent-pool CIDRs on `use_network_pools=true` subnets in a way that looks like subnet ownership.

Operationally:

- allocator behavior is stricter than the display
- console and API representation can be ambiguous
- automation must not rely on inherited status CIDRs as proof of subnet ownership

Officially documented behavior to keep in mind:

- The default network pool's CIDR is region-specific, and the default subnet
  reuses that range.
- Subnets can either explicitly reserve CIDR blocks inside the network range,
  or transparently use the network pools. Subnets that use network pools can
  concurrently use those pools.
- Explicit subnet CIDRs must be within the network range and must not overlap
  CIDR blocks used by other subnets in that network.
- If private addresses outside the default/current range are needed, add the
  CIDR block to the network's private pool first, then create a subnet with
  that CIDR in the network.

## Safe Rules For Automation

- treat explicit subnet `spec` CIDRs as authoritative
- treat inherited subnets as non-owning
- treat `status.ipv4_private_cidrs` as display/effective-state data; use it as
  an explicit-ownership fallback only after confirming
  `use_network_pools=false`
- resolve network parent private CIDRs by reading attached pool IDs and then
  the pool CIDRs; accept either SDK field spelling (`pool_id` or `id`)
- require explicit subnet CIDRs to fit inside a known parent network private
  CIDR; fail closed if parent ranges cannot be resolved
- reject explicit subnet CIDRs that overlap another explicit subnet's CIDR in
  the same network
- list live allocations and reject proposed subnet CIDRs that overlap existing
  private allocations in the same network
- scope route operations to the intended network
- skip inherited subnets for subnet-targeted automation unless Nebius adds explicit ownership semantics

## CIDR Suggestion Rules

For a new explicit subnet under an existing network:

- derive candidate child CIDRs from the network's current parent private CIDRs
- exclude ranges that overlap explicit peer-subnet CIDRs
- exclude ranges that overlap live private allocations
- if the operator requests an out-of-parent CIDR, treat that as a parent pool
  extension workflow, not as a subnet-only workflow

For parent-pool extension suggestions:

- keep suggestions inside RFC1918 private IPv4 space unless the task explicitly
  calls for public addressing
- useful broad private blocks are `172.16.0.0/12` and `192.168.0.0/16` when
  they are not already consumed by explicit subnets or live allocations
- avoid Nebius documented regional default private-pool ranges when suggesting
  new custom parent ranges for additional networks

## Dedicated Subnet Rules

- keep service and appliance subnets explicit when ownership matters
- require `use_network_pools=false`
- if the requested dedicated-subnet CIDR is outside the current network pool,
  extend an attached network private pool first
- reject overlap with existing explicit subnets
- reject overlap with live private allocations

## Existing Network Extension Workflow

Use this when adding an explicit subnet CIDR outside the selected live
network's current parent private CIDRs.

1. Read the selected network and its attached private pool IDs.
2. Read those pools and collect CIDRs plus compatibility metadata such as
   source/scope when available.
3. If the requested CIDR already fits an attached parent pool, do not extend
   the network; proceed to explicit subnet creation.
4. If it does not fit, update an attached compatible private pool by adding
   the requested parent CIDR block.
5. Create the subnet with `use_network_pools=false` and explicit CIDRs that
   are children of the now-extended parent range.

Implementation notes:

- Prefer updating the attached network private pool's CIDR list over creating
  and attaching a detached root pool.
- Send enough resource metadata for updates: ID, parent ID, and current
  resource version when the SDK/API requires optimistic concurrency.
- Make extension idempotent. If a requested CIDR already exists on an attached
  compatible pool, treat it as success.
- If a name or CIDR collision points to a pool that is assigned elsewhere,
  empty, public, or in a different tree/scope, skip it and keep scanning. If no
  compatible candidate exists, fail with the collected reasons.

## New Network Pool Selection

For new networks:

- offer unassigned private IPv4 pools only when they already have at least one
  CIDR
- hide pools already assigned to a network or subnet; SDK shapes can expose
  assignment as `networks`/`subnets` or `network_ids`/`subnet_ids`
- hide empty pools because they do not provide a usable parent CIDR for subnet
  containment
- direct config can still create a managed private pool from CIDR or from a
  source pool when the implementation supports that path
- public pool and route-table inputs can often stay omitted: Nebius provides
  default public-pool/default-route behavior for ordinary VM public IP paths

## Terraform Module Rules

- For explicit subnets, render Terraform provider shape equivalent to
  `ipv4_private_pools.use_network_pools = false` with one or more explicit
  CIDR entries.
- Reject `use_network_pools=true` when explicit subnet CIDRs are provided.
- Reject subnets with no explicit private CIDRs when the module's contract is
  explicit-only.
- For existing networks, keep Terraform ownership boundaries clear: Terraform
  can read the existing network and create subnets, while any parent private
  pool extension should happen before render/apply through the chosen
  automation layer.
- For planned same-run networks and consumers, render consumer inputs as direct
  Terraform module arguments such as `network_id` and `subnet_id`; keep any
  higher-level binding syntax out of Terraform modules.

## Operator Wording

The clean explanation is:

- network pool = VPC private CIDR inventory
- explicit subnet pool = subnet-owned CIDR carved from that inventory
- inherited subnet = allocation mode, not CIDR ownership

## Official References

- Nebius VPC overview:
  `https://docs.nebius.com/vpc/overview`
- Default private CIDR blocks per region:
  `https://docs.nebius.com/vpc/addressing/available-addresses`
- Custom private address workflow:
  `https://docs.nebius.com/vpc/addressing/custom-private-addresses`
- Terraform subnet resource schema:
  `https://docs.nebius.com/terraform-provider/reference/resources/vpc_v1_subnet`
- Terraform network data-source schema:
  `https://docs.nebius.com/terraform-provider/reference/data-sources/vpc_v1_network`
