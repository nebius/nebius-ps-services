mock_provider "nebius" {
  mock_data "nebius_vpc_v1_network" {
    defaults = { parent_id = "project-test" }
  }
  mock_data "nebius_vpc_v1_subnet" {
    defaults = {
      parent_id  = "project-test"
      network_id = "vpcnetwork-test"
    }
  }
}

run "service_account_keys_are_known_before_creation" {
  command = plan

  variables {
    cluster = {
      parent_id       = "project-test"
      cluster_name    = "test"
      network_id      = "vpcnetwork-test"
      subnet_id       = "vpcsubnet-test"
      k8s_version     = "1.34"
      public_endpoint = false
    }
    node_groups = {
      created = {
        platform        = "cpu-d3", preset = "32vcpu-128gb", node_count = 1
        service_account = { name = "test-created" }
      }
      existing = {
        platform        = "cpu-d3", preset = "32vcpu-128gb", node_count = 1
        service_account = { id = "serviceaccount-test" }
      }
      omitted = {
        platform = "cpu-d3", preset = "32vcpu-128gb", node_count = 1
      }
      empty = {
        platform        = "cpu-d3", preset = "32vcpu-128gb", node_count = 1
        service_account = { name = " " }
      }
      disabled = {
        platform = "cpu-d3", preset = "32vcpu-128gb", node_count = 1
        enabled  = false, service_account = { name = "test-disabled" }
      }
    }
  }

  assert {
    condition     = keys(output.service_account_ids) == ["created", "existing"]
    error_message = "Service-account keys must be known at plan time and exclude unconfigured or disabled groups."
  }
  assert {
    condition     = output.service_account_ids["existing"] == "serviceaccount-test"
    error_message = "Caller-provided service-account IDs must be preserved."
  }
}
