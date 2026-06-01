locals {
  existing_network_id = try(trimspace(var.network.existing_id), "")
  create_network      = local.existing_network_id == ""

  input_network_private_pool_ids = distinct([
    for pool_id in try(var.network.ipv4_private_pool_ids, []) : trimspace(pool_id)
    if length(trimspace(pool_id)) > 0
  ])
  existing_network_private_pool_ids = (
    local.create_network
    ? []
    : try([
      for pool in data.nebius_vpc_v1_network.existing[0].ipv4_private_pools.pools : pool.id
    ], [])
  )
  input_network_private_cidrs = distinct([
    for cidr in try(var.network.ipv4_private_cidrs, []) : trimspace(cidr)
    if length(trimspace(cidr)) > 0
  ])
  input_network_private_pool_cidrs = distinct(flatten([
    for pool in data.nebius_vpc_v1_pool.private_pool : try(pool.status.cidrs, [])
  ]))
  existing_network_private_pool_cidrs = distinct(flatten([
    for pool in data.nebius_vpc_v1_pool.existing_private_pool : try(pool.status.cidrs, [])
  ]))
  network_private_source_pool_id = try(trimspace(var.network.ipv4_private_source_pool_id), "")
  network_public_pool_ids = distinct([
    for pool_id in try(var.network.ipv4_public_pool_ids, []) : trimspace(pool_id)
    if length(trimspace(pool_id)) > 0
  ])

  network_id = local.create_network ? nebius_vpc_v1_network.this[0].id : data.nebius_vpc_v1_network.existing[0].id
  network_name = (
    local.create_network
    ? try(var.network.name, null)
    : try(data.nebius_vpc_v1_network.existing[0].name, null)
  )
  network_default_route_table_id = (
    local.create_network
    ? try(nebius_vpc_v1_network.this[0].status.default_route_table_id, null)
    : try(data.nebius_vpc_v1_network.existing[0].status.default_route_table_id, null)
  )
  effective_network_private_pool_ids = (
    local.create_network
    ? try([
      for pool in nebius_vpc_v1_network.this[0].ipv4_private_pools.pools : pool.id
    ], local.network_private_pool_ids)
    : try([
      for pool in data.nebius_vpc_v1_network.existing[0].ipv4_private_pools.pools : pool.id
    ], [])
  )
  effective_network_public_pool_ids = (
    local.create_network
    ? try([
      for pool in nebius_vpc_v1_network.this[0].ipv4_public_pools.pools : pool.id
    ], local.network_public_pool_ids)
    : try([
      for pool in data.nebius_vpc_v1_network.existing[0].ipv4_public_pools.pools : pool.id
    ], [])
  )

  subnets = {
    for key, subnet in var.subnets : key => {
      name                      = coalesce(try(subnet.name, null), key)
      labels                    = try(subnet.labels, {})
      route_table_id            = try(subnet.route_table_id, null)
      use_network_private_pools = try(subnet.use_network_private_pools, false)
      private_cidrs = distinct([
        for cidr in try(subnet.ipv4_private_cidrs, []) : trimspace(cidr)
        if length(trimspace(cidr)) > 0
      ])
      use_network_public_pools = try(subnet.use_network_public_pools, true)
      public_cidrs = distinct([
        for cidr in try(subnet.ipv4_public_cidrs, []) : trimspace(cidr)
        if length(trimspace(cidr)) > 0
      ])
    }
  }

  managed_private_pool_cidrs = {
    for cidr in local.input_network_private_cidrs : cidr => cidr
    if local.create_network
  }
  network_private_pool_ids = distinct(concat(
    local.input_network_private_pool_ids,
    [for pool in nebius_vpc_v1_pool.private : pool.id],
  ))
  network_private_cidrs = distinct(concat(
    local.existing_network_private_pool_cidrs,
    local.input_network_private_pool_cidrs,
    local.input_network_private_cidrs,
  ))
  network_private_ranges = [
    for cidr in local.network_private_cidrs : {
      cidr = cidr
      start = sum([
        for index, octet in split(".", cidrhost(cidr, 0)) :
        parseint(octet, 10) * pow(256, 3 - index)
      ])
      end = sum([
        for index, octet in split(".", cidrhost(cidr, -1)) :
        parseint(octet, 10) * pow(256, 3 - index)
      ])
    }
  ]
  explicit_private_subnet_ranges = flatten([
    for key, subnet in local.subnets : [
      for cidr in subnet.private_cidrs : {
        key  = key
        cidr = cidr
        start = sum([
          for index, octet in split(".", cidrhost(cidr, 0)) :
          parseint(octet, 10) * pow(256, 3 - index)
        ])
        end = sum([
          for index, octet in split(".", cidrhost(cidr, -1)) :
          parseint(octet, 10) * pow(256, 3 - index)
        ])
      }
    ]
  ])

  subnet_output = {
    for key, subnet in nebius_vpc_v1_subnet.this : key => {
      id                 = subnet.id
      name               = try(subnet.name, local.subnets[key].name)
      network_id         = subnet.network_id
      parent_id          = subnet.parent_id
      ipv4_private_cidrs = try(subnet.status.ipv4_private_cidrs, [])
      ipv4_public_cidrs  = try(subnet.status.ipv4_public_cidrs, [])
      route_table = {
        id      = try(subnet.status.route_table.id, null)
        default = try(subnet.status.route_table.default, null)
      }
    }
  }
}
