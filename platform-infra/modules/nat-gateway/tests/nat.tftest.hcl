mock_provider "nebius" {
  mock_data "nebius_vpc_v1_network" {
    defaults = { parent_id = "project-test" }
  }
  mock_data "nebius_vpc_v1_subnet" {
    defaults = { parent_id = "project-test", network_id = "network-test" }
  }
}

variables {
  parent_id       = "project-test"
  network_id      = "network-test"
  name            = "nat-test"
  source_image_id = "image-test"
  ssh_public_key  = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f example"
  admin_cidrs     = ["192.0.2.0/24"]
  source_cidrs    = ["10.0.0.0/8"]
  routing_policies = {
    first  = [{ destination_cidr = "10.20.0.0/16", allocation_id = "allocation-private-test" }]
    second = []
  }
}

run "isolated_gateway_and_preserved_policy" {
  command = plan

  assert {
    condition     = nebius_vpc_v1_subnet.gateway.ipv4_private_pools.use_network_pools == false
    error_message = "The gateway must own an explicit subnet pool."
  }
  assert {
    condition     = nebius_vpc_v1_route.gateway_egress.next_hop.default_egress_gateway == true
    error_message = "Gateway traffic must use direct egress."
  }
  assert {
    condition     = length(nebius_vpc_v1_route_table.workload) == 2 && length(nebius_vpc_v1_route.preserved) == 1
    error_message = "Separate consumer policies and non-default routes must be preserved."
  }
  assert {
    condition     = nebius_vpc_v1_route.preserved["first:10.20.0.0/16"].next_hop.allocation.id == "allocation-private-test"
    error_message = "A custom next hop cannot be replaced by NAT."
  }
}

run "reject_unrestricted_administration" {
  command = plan
  variables { admin_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.admin_cidrs]
}

run "reject_default_route_override" {
  command = plan
  variables {
    routing_policies = { bad = [{ destination_cidr = "0.0.0.0/0", allocation_id = "other" }] }
  }
  expect_failures = [var.routing_policies]
}
