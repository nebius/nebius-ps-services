mock_provider "nebius" {
  mock_resource "nebius_vpc_v1_network" {
    defaults = { id = "network-test" }
  }
  mock_data "nebius_vpc_v1_network" {
    defaults = { parent_id = "project-test" }
  }
  mock_data "nebius_vpc_v1_subnet" {
    defaults = { parent_id = "project-test", network_id = "network-test", status = {
      ipv4_private_pools = [{ pool_id = "private", cidrs = ["10.0.2.0/28"] }]
      ipv4_public_pools  = [{ pool_id = "public", cidrs = ["203.0.113.0/24"] }]
    } }
  }
}

run "prepare_same_deployment_network_without_cycle" {
  command = plan
  module { source = "./tests/fixtures/composed" }
}

run "activate_same_deployment_network_without_cycle" {
  command = plan
  module { source = "./tests/fixtures/composed" }
  variables { route_overrides = { work = "table-nat" } }
}
