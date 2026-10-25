variable "project_id" {
  type = string
}
variable "network_id" {
  type = string
}
variable "image_id" {
  type = string
}
variable "ssh_public_key" {
  type = string
}
variable "admin_cidrs" {
  type = list(string)
}
variable "network_private_cidrs" {
  type = list(string)
}
module "nat" {
  source          = "../.."
  parent_id       = var.project_id
  network_id      = var.network_id
  name            = "example-nat"
  source_image_id = var.image_id
  ssh_public_key  = var.ssh_public_key
  admin_cidrs     = var.admin_cidrs
  source_cidrs    = var.network_private_cidrs
  routing_policies = {
    default = []
  }
}
output "route_table_ids" {
  value = module.nat.route_table_ids
}
