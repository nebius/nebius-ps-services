mock_provider "nebius" {}

variables {
  parent_id = "project-test"
  network = {
    name               = "test-network"
    ipv4_private_cidrs = ["172.16.0.0/12"]
  }
}

run "accept_explicit_subnet_private_cidr" {
  command = plan

  variables {
    subnets = {
      worker = {
        name                      = "worker-subnet"
        use_network_private_pools = false
        ipv4_private_cidrs        = ["172.16.0.0/16"]
      }
    }
  }
}

run "reject_inherited_subnet_private_pool_mode" {
  command = plan

  variables {
    subnets = {
      worker = {
        name                      = "worker-subnet"
        use_network_private_pools = true
        ipv4_private_cidrs        = ["172.16.0.0/16"]
      }
    }
  }

  expect_failures = [
    var.subnets,
  ]
}

run "reject_missing_subnet_private_cidrs" {
  command = plan

  variables {
    subnets = {
      worker = {
        name = "worker-subnet"
      }
    }
  }

  expect_failures = [
    var.subnets,
  ]
}

run "reject_subnet_private_cidr_outside_parent_range" {
  command = plan

  variables {
    subnets = {
      worker = {
        name                      = "worker-subnet"
        use_network_private_pools = false
        ipv4_private_cidrs        = ["192.168.0.0/16"]
      }
    }
  }

  expect_failures = [
    terraform_data.subnet_contract,
  ]
}

run "reject_existing_network_without_parent_private_ranges" {
  command = plan

  variables {
    network = {
      existing_id = "vpcnetwork-existing"
    }
    subnets = {
      worker = {
        name                      = "worker-subnet"
        use_network_private_pools = false
        ipv4_private_cidrs        = ["172.16.0.0/16"]
      }
    }
  }

  override_data {
    target = data.nebius_vpc_v1_network.existing[0]
    values = {
      id        = "vpcnetwork-existing"
      parent_id = "project-test"
      name      = "existing-network"
    }
  }

  expect_failures = [
    terraform_data.subnet_contract,
  ]
}

run "reject_overlapping_subnet_private_cidrs" {
  command = plan

  variables {
    subnets = {
      worker-a = {
        name                      = "worker-a"
        use_network_private_pools = false
        ipv4_private_cidrs        = ["172.16.0.0/16"]
      }
      worker-b = {
        name                      = "worker-b"
        use_network_private_pools = false
        ipv4_private_cidrs        = ["172.16.0.0/17"]
      }
    }
  }

  expect_failures = [
    terraform_data.subnet_contract,
  ]
}
