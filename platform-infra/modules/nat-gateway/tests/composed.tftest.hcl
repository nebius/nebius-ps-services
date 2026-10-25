mock_provider "nebius" {
  mock_data "nebius_vpc_v1_network" {
    defaults = { parent_id = "project-test" }
  }
  mock_data "nebius_vpc_v1_subnet" {
    defaults = { parent_id = "project-test" }
  }
}

run "prepare_same_deployment_network_without_cycle" {
  command = plan
  module { source = "./tests/fixtures/composed" }
}

run "activate_same_deployment_network_without_cycle" {
  command = plan
  module { source = "./tests/fixtures/composed" }
  variables { route_overrides = {} }
}
