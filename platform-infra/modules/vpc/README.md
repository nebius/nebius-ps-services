# Nebius VPC

Creates a Nebius VPC network with optional subnets, or creates subnets under an existing VPC network.

## Usage

```hcl
module "vpc" {
  source = "./modules/vpc"

  parent_id = var.parent_id

  network = {
    name                        = "cluster-network"
    ipv4_private_source_pool_id = "vpcpool-source-..."
    ipv4_private_cidrs          = ["172.16.0.0/12"]
  }

  subnets = {
    worker = {
      name                      = "worker-subnet"
      use_network_private_pools = false
      ipv4_private_cidrs        = ["172.16.0.0/16"]
    }
  }
}
```

To use an existing network and create only subnets, set `network.existing_id`:

```hcl
network = {
  existing_id = "vpcnetwork-..."
}
```

For existing networks, the module reads the network's attached private pools
and validates explicit subnet CIDRs against those parent ranges during
planning. Extend the live network private pool first when a new subnet CIDR
must come from a new address block. If parent private CIDR ranges cannot be
read from the selected network or private pools, plans with declared subnets
fail instead of guessing containment.

The module outputs `network_id`, `network`, `subnets`, and `subnet_ids`. Consumers should use `module.vpc.network_id` and `module.vpc.subnets["worker"].id`.

New networks require either `network.ipv4_private_cidrs` or
`network.ipv4_private_pool_ids`. `network.ipv4_private_pool_ids` is the
existing-pool path: the module attaches already-created private IPv4 pools to
the new network. `network.ipv4_private_cidrs` is the managed-pool path: the
module creates private pools for those parent network ranges and attaches them
to the network. Set `network.ipv4_private_source_pool_id` when those managed
pools must be carved from an existing Nebius source pool. Omitting a source pool
asks Nebius to create a project-level private pool directly, which can require a
broader VPC permission than creating subnets under an existing network.
When both `network.ipv4_private_pool_ids` and `network.ipv4_private_cidrs` are
set for a new network, explicit subnet CIDRs may fit either an attached
existing private pool range or a managed CIDR range; the module reads attached
pool CIDRs during planning for that containment check.

The module follows the Nebius default-network pattern for public addressing and
routing. Leave `network.ipv4_public_pool_ids` unset to let Nebius attach the
default public pool for the project/tenant to the network. With that default,
subnets inherit the network public pools and resources in those subnets can use
reserved allocations or dynamic public IPv4 addresses when their own modules
request them. Set `network.ipv4_public_pool_ids` only when you intentionally
want to attach existing public IPv4 pools. The module validates that supplied
public pool IDs are public IPv4 pools in `parent_id`.

Nebius creates a default route table for every network and assigns it to new
subnets unless `route_table_id` is set on a subnet. The `network` output exposes
`default_route_table_id`, and each `subnets` entry includes the route-table
status reported by Nebius.

Every declared subnet uses explicit private child ranges. Leave
`use_network_private_pools = false` and provide `ipv4_private_cidrs`; those
CIDRs must fit inside the selected network's attached or managed private pool
ranges and must not overlap other subnets in that network.

Subnets also inherit the selected network's public pools by default. Set
`use_network_public_pools = false` with no `ipv4_public_cidrs` to create a
private-only subnet that cannot allocate public IP addresses. Set
`ipv4_public_cidrs` only for an explicit subnet-level public child range.

Omit `subnets` or set it to `{}` to create only the network.
