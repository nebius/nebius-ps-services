output "network_id" {
  description = "Selected VPC network ID, either created by this module or supplied via network.existing_id."
  value       = local.network_id
}

output "network" {
  description = "Selected VPC network metadata."
  value = {
    id                     = local.network_id
    name                   = local.network_name
    parent_id              = var.parent_id
    created                = local.create_network
    default_route_table_id = local.network_default_route_table_id
    ipv4_private_pool_ids  = local.effective_network_private_pool_ids
    ipv4_public_pool_ids   = local.effective_network_public_pool_ids
  }
}

output "subnets" {
  description = "Created subnet metadata keyed by logical subnet name."
  value       = local.subnet_output
}

output "subnet_ids" {
  description = "Created subnet IDs keyed by logical subnet name."
  value = {
    for key, subnet in nebius_vpc_v1_subnet.this : key => subnet.id
  }
}
