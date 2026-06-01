resource "nebius_vpc_v1_pool" "private" {
  for_each = local.managed_private_pool_cidrs

  parent_id  = var.parent_id
  name       = "${var.network.name}-private-${replace(replace(each.value, ".", "-"), "/", "-")}"
  version    = "IPV4"
  visibility = "PRIVATE"
  source_pool_id = (
    local.network_private_source_pool_id != ""
    ? local.network_private_source_pool_id
    : null
  )
  cidrs = [
    {
      cidr = each.value
    }
  ]
}

data "nebius_vpc_v1_pool" "private_source" {
  count = local.network_private_source_pool_id != "" ? 1 : 0

  id = local.network_private_source_pool_id
}

data "nebius_vpc_v1_pool" "private_pool" {
  for_each = toset(local.input_network_private_pool_ids)

  id = each.value
}

data "nebius_vpc_v1_pool" "existing_private_pool" {
  for_each = toset(local.existing_network_private_pool_ids)

  id = each.value
}

data "nebius_vpc_v1_pool" "public_pool" {
  for_each = toset(local.network_public_pool_ids)

  id = each.value
}

resource "nebius_vpc_v1_network" "this" {
  count = local.create_network ? 1 : 0

  parent_id = var.parent_id
  name      = var.network.name
  labels    = length(var.network.labels) > 0 ? var.network.labels : null

  ipv4_private_pools = length(local.network_private_pool_ids) > 0 ? {
    pools = [
      for pool_id in local.network_private_pool_ids : {
        id = pool_id
      }
    ]
  } : null

  ipv4_public_pools = length(local.network_public_pool_ids) > 0 ? {
    pools = [
      for pool_id in local.network_public_pool_ids : {
        id = pool_id
      }
    ]
  } : null
}

data "nebius_vpc_v1_network" "existing" {
  count = local.create_network ? 0 : 1

  id = local.existing_network_id
}

resource "terraform_data" "network_contract" {
  input = local.network_id

  lifecycle {
    precondition {
      condition     = local.create_network ? true : data.nebius_vpc_v1_network.existing[0].parent_id == var.parent_id
      error_message = "network.existing_id must identify a VPC network in parent_id."
    }

    precondition {
      condition     = !local.create_network || length(local.network_private_pool_ids) > 0
      error_message = "Creating a new VPC network requires network.ipv4_private_cidrs or network.ipv4_private_pool_ids."
    }

    precondition {
      condition = local.network_private_source_pool_id == "" ? true : (
        data.nebius_vpc_v1_pool.private_source[0].parent_id == var.parent_id
        && data.nebius_vpc_v1_pool.private_source[0].version == "IPV4"
        && data.nebius_vpc_v1_pool.private_source[0].visibility == "PRIVATE"
      )
      error_message = "network.ipv4_private_source_pool_id must identify a private IPv4 pool in parent_id."
    }

    precondition {
      condition = alltrue([
        for pool in data.nebius_vpc_v1_pool.private_pool : (
          pool.parent_id == var.parent_id
          && pool.version == "IPV4"
          && pool.visibility == "PRIVATE"
        )
      ])
      error_message = "network.ipv4_private_pool_ids must identify private IPv4 pools in parent_id."
    }

    precondition {
      condition = alltrue([
        for pool in data.nebius_vpc_v1_pool.public_pool : (
          pool.parent_id == var.parent_id
          && pool.version == "IPV4"
          && pool.visibility == "PUBLIC"
        )
      ])
      error_message = "network.ipv4_public_pool_ids must identify public IPv4 pools in parent_id."
    }
  }
}

resource "terraform_data" "subnet_contract" {
  input = local.explicit_private_subnet_ranges

  lifecycle {
    precondition {
      condition = (
        length(local.explicit_private_subnet_ranges) == 0
        || (
          length(local.network_private_ranges) > 0
          && alltrue([
            for subnet_range in local.explicit_private_subnet_ranges : anytrue([
              for network_range in local.network_private_ranges : (
                subnet_range.start >= network_range.start
                && subnet_range.end <= network_range.end
              )
            ])
          ])
        )
      )
      error_message = "Explicit subnet ipv4_private_cidrs must fit inside selected VPC network private CIDRs; ensure the selected network or private pools expose parent CIDR ranges."
    }

    precondition {
      condition = alltrue(flatten([
        for left_index, left in local.explicit_private_subnet_ranges : [
          for right_index, right in local.explicit_private_subnet_ranges : (
            left_index >= right_index
            || left.end < right.start
            || right.end < left.start
          )
        ]
      ]))
      error_message = "Explicit subnet ipv4_private_cidrs must not overlap within the selected VPC network."
    }
  }
}

resource "nebius_vpc_v1_subnet" "this" {
  for_each = local.subnets

  depends_on = [terraform_data.network_contract, terraform_data.subnet_contract]

  parent_id      = var.parent_id
  network_id     = local.network_id
  name           = each.value.name
  route_table_id = each.value.route_table_id
  labels         = length(each.value.labels) > 0 ? each.value.labels : null

  ipv4_private_pools = {
    use_network_pools = false
    pools = [
      {
        cidrs = [
          for cidr in each.value.private_cidrs : {
            cidr = cidr
          }
        ]
      }
    ]
  }

  ipv4_public_pools = (
    each.value.use_network_public_pools && length(each.value.public_cidrs) == 0
    ? null
    : {
      use_network_pools = each.value.use_network_public_pools
      pools = each.value.use_network_public_pools ? [] : [
        {
          cidrs = [
            for cidr in each.value.public_cidrs : {
              cidr = cidr
            }
          ]
        }
      ]
    }
  )

  lifecycle {
    precondition {
      condition     = local.network_id != ""
      error_message = "subnets require a selected VPC network."
    }
  }
}
