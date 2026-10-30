mock_provider "nebius" {
  mock_data "nebius_vpc_v1_network" { defaults = { parent_id = "project-test" } }
  mock_data "nebius_vpc_v1_subnet" { defaults = { parent_id = "project-test", network_id = "network-test" } }
}
variables {
  parent_id        = "project-test"
  network_id       = "network-test"
  subnet_id        = "subnet-test"
  platform         = "cpu-d3"
  preset           = "4vcpu-16gb"
  ssh_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f example"
  management_cidrs = ["192.0.2.1/32"]
}
run "two_independent_routers_by_default" {
  command = plan
  assert {
    condition     = length(module.member) == 2 && length(nebius_vpc_v1_allocation.public) == 2 && output.high_availability
    error_message = "Default HA must provision two independently addressed members."
  }
  assert {
    condition     = toset(keys(module.member)) == toset(["a", "b"]) && length(nebius_vpc_v1_security_rule.ssh) == 1
    error_message = "Member keys and restricted management rule must remain stable."
  }
}
run "explicit_non_ha" {
  command = plan
  variables { replicas = 1 }
  assert {
    condition     = length(module.member) == 1 && !output.high_availability
    error_message = "One member must explicitly report non-HA."
  }
}
run "reject_global_ssh" {
  command = plan
  variables { management_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.management_cidrs]
}
run "reject_noncanonical_global_ssh" {
  command = plan
  variables { management_cidrs = ["192.0.2.1/0"] }
  expect_failures = [var.management_cidrs]
}
run "reject_unsupported_image" {
  command = plan
  variables { source_image_family = "ubuntu22.04-driverless" }
  expect_failures = [var.source_image_family]
}
