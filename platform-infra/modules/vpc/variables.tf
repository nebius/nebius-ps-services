variable "parent_id" {
  description = "Nebius project ID where VPC resources are managed."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "network" {
  description = "VPC network selector. Set existing_id to use an existing network; omit it to create a new network."
  type = object({
    existing_id                 = optional(string)
    name                        = optional(string)
    labels                      = optional(map(string), {})
    ipv4_private_cidrs          = optional(list(string), [])
    ipv4_private_pool_ids       = optional(list(string), [])
    ipv4_private_source_pool_id = optional(string)
    # Omit public pool IDs to let Nebius attach the project/tenant default
    # public pool to the network.
    ipv4_public_pool_ids = optional(list(string), [])
  })
  nullable = false

  validation {
    condition = (
      try(var.network.existing_id == null || length(trimspace(var.network.existing_id)) > 0, true)
      && try(var.network.name == null || length(trimspace(var.network.name)) > 0, true)
      && try(var.network.ipv4_private_source_pool_id == null || length(trimspace(var.network.ipv4_private_source_pool_id)) > 0, true)
      && (
        try(var.network.existing_id != null && length(trimspace(var.network.existing_id)) > 0, false)
        || try(var.network.name != null && length(trimspace(var.network.name)) > 0, false)
      )
    )
    error_message = "network.existing_id or network.name must be set; provided values cannot be empty."
  }

  validation {
    condition = alltrue([
      for cidr in try(var.network.ipv4_private_cidrs, []) : can(cidrhost(cidr, 0))
      ]) && alltrue([
      for pool_id in try(var.network.ipv4_private_pool_ids, []) : length(trimspace(pool_id)) > 0
      ]) && alltrue([
      for pool_id in try(var.network.ipv4_public_pool_ids, []) : length(trimspace(pool_id)) > 0
    ])
    error_message = "network private CIDRs must be valid CIDR blocks, and network private/public pool IDs cannot be empty when provided."
  }

  validation {
    condition = (
      try(var.network.existing_id == null || length(trimspace(var.network.existing_id)) == 0, true)
      || (
        length(try(var.network.ipv4_private_cidrs, [])) == 0
        && length(try(var.network.ipv4_private_pool_ids, [])) == 0
        && try(var.network.ipv4_private_source_pool_id == null || length(trimspace(var.network.ipv4_private_source_pool_id)) == 0, true)
        && length(try(var.network.ipv4_public_pool_ids, [])) == 0
      )
    )
    error_message = "Do not set network CIDRs, source pool, or pool IDs when network.existing_id is set; existing networks already own their pools."
  }

  validation {
    condition = (
      try(var.network.existing_id != null && length(trimspace(var.network.existing_id)) > 0, false)
      || length(try(var.network.ipv4_private_cidrs, [])) > 0
      || length(try(var.network.ipv4_private_pool_ids, [])) > 0
    )
    error_message = "Creating a new VPC network requires network.ipv4_private_cidrs or network.ipv4_private_pool_ids."
  }

  validation {
    condition = (
      try(var.network.ipv4_private_source_pool_id == null || length(trimspace(var.network.ipv4_private_source_pool_id)) == 0, true)
      || length(try(var.network.ipv4_private_cidrs, [])) > 0
    )
    error_message = "network.ipv4_private_source_pool_id applies only when network.ipv4_private_cidrs creates managed private pools."
  }
}

variable "subnets" {
  description = "Subnets to create under the selected network, keyed by logical subnet name."
  type = map(object({
    name                      = optional(string)
    labels                    = optional(map(string), {})
    route_table_id            = optional(string)
    use_network_private_pools = optional(bool, false)
    ipv4_private_cidrs        = optional(list(string), [])
    use_network_public_pools  = optional(bool, true)
    ipv4_public_cidrs         = optional(list(string), [])
  }))
  default  = {}
  nullable = false

  validation {
    condition = alltrue([
      for key, subnet in var.subnets : (
        length(trimspace(key)) > 0
        && try(subnet.name == null || length(trimspace(subnet.name)) > 0, true)
        && try(subnet.route_table_id == null || length(trimspace(subnet.route_table_id)) > 0, true)
        && alltrue([
          for cidr in try(subnet.ipv4_private_cidrs, []) : can(cidrhost(cidr, 0))
        ])
        && alltrue([
          for cidr in try(subnet.ipv4_public_cidrs, []) : can(cidrhost(cidr, 0))
        ])
      )
    ])
    error_message = "Each subnet must have a non-empty key and valid optional name, route_table_id, and CIDR values."
  }

  validation {
    condition = alltrue([
      for subnet in values(var.subnets) : (
        try(subnet.use_network_private_pools, false) == false
      )
    ])
    error_message = "VPC module subnets always use explicit private CIDRs; use_network_private_pools must be false."
  }

  validation {
    condition = alltrue([
      for subnet in values(var.subnets) : (
        length(try(subnet.ipv4_private_cidrs, [])) > 0
      )
    ])
    error_message = "Provide ipv4_private_cidrs for every VPC subnet."
  }

  validation {
    condition = alltrue([
      for subnet in values(var.subnets) : (
        length(try(subnet.ipv4_public_cidrs, [])) == 0
        || try(subnet.use_network_public_pools, true) == false
      )
    ])
    error_message = "Set use_network_public_pools=false when ipv4_public_cidrs are provided."
  }
}
