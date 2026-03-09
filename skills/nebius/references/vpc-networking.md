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

## API Interpretation Rules

### Network

- `network.spec.ipv4_private_pools.pools[*].pool_id`
  Parent network pool references.

### Pool

- `pool.spec.cidrs`
  CIDRs carried by that pool.
- `pool.spec.source_pool_id`
  Parent link for derived child pools.

### Subnet

- `subnet.spec.ipv4_private_pools.use_network_pools`
  Subnet allocation mode.
- `subnet.spec.ipv4_private_pools.pools[*].cidrs`
  Explicit subnet CIDRs when `use_network_pools=false`.
- `subnet.status.ipv4_private_cidrs`
  Useful for comparison, but not safe as the only ownership signal.

## Current Nebius Control-Plane Caveat

Nebius can expose inherited parent-pool CIDRs on `use_network_pools=true` subnets in a way that looks like subnet ownership.

Operationally:

- allocator behavior is stricter than the display
- console and API representation can be ambiguous
- automation must not rely on inherited status CIDRs as proof of subnet ownership

## Safe Rules For Automation

- treat explicit subnet `spec` CIDRs as authoritative
- treat inherited subnets as non-owning
- scope route operations to the intended network
- skip inherited subnets for subnet-targeted automation unless Nebius adds explicit ownership semantics

## Dedicated Subnet Rules

- keep service and appliance subnets explicit when ownership matters
- require `use_network_pools=false`
- if the requested dedicated-subnet CIDR is outside the current network pool, extend the network pool first
- reject overlap with existing explicit subnets

## Operator Wording

The clean explanation is:

- network pool = VPC private CIDR inventory
- explicit subnet pool = subnet-owned CIDR carved from that inventory
- inherited subnet = allocation mode, not CIDR ownership
