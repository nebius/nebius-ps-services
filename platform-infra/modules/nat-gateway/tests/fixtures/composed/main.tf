terraform {
  required_providers {
    nebius = { source = "nebius/nebius", version = ">= 0.6.8, < 0.7.0" }
  }
}
variable "route_overrides" {
  type    = map(string)
  default = { "work" = null }
}
module "vpc" {
  source    = "../../../../vpc"
  parent_id = "project-test"
  network   = { name = "network-test", ipv4_private_cidrs = ["10.0.0.0/16"] }
  subnets = {
    work = {
      name                      = "work"
      use_network_private_pools = false
      ipv4_private_cidrs        = ["10.0.1.0/24"]
      route_table_id            = lookup(var.route_overrides, "work", module.nat.route_table_ids["default"])
    }
  }
}
module "nat" {
  source           = "../../.."
  parent_id        = "project-test"
  network_id       = module.vpc.network_id
  name             = "nat-test"
  source_image_id  = "image-test"
  ssh_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f example"
  admin_cidrs      = ["192.0.2.0/24"]
  source_cidrs     = ["10.0.0.0/16"]
  routing_policies = { default = [] }
}
