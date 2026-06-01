variable "parent_id" {
  type = string
}

module "vpc" {
  source = "../.."

  parent_id = var.parent_id

  network = {
    name               = "example-network"
    ipv4_private_cidrs = ["172.16.0.0/12"]
  }

  subnets = {
    worker = {
      name                      = "example-worker"
      use_network_private_pools = false
      ipv4_private_cidrs        = ["172.16.0.0/16"]
    }
  }
}

output "network_id" {
  value = module.vpc.network_id
}

output "subnet_ids" {
  value = module.vpc.subnet_ids
}
