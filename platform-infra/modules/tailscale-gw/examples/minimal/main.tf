# Provision only. Use cxcli for protected enrollment and lifecycle management.
module "gateway" {
  source           = "../.."
  parent_id        = var.parent_id
  network_id       = var.network_id
  subnet_id        = var.subnet_id
  platform         = "cpu-d3"
  preset           = "4vcpu-16gb"
  ssh_public_key   = var.ssh_public_key
  management_cidrs = var.management_cidrs
}
variable "parent_id" { type = string }
variable "network_id" { type = string }
variable "subnet_id" { type = string }
variable "ssh_public_key" { type = string }
variable "management_cidrs" { type = list(string) }
output "members" { value = module.gateway.members }
