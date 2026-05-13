module "nfs" {
  source = "../.."

  parent_id           = var.parent_id
  name                = "example-nfs"
  subnet_id           = var.subnet_id
  ssh_public_key      = var.ssh_public_key
  source_image_family = "ubuntu24.04"

  export_path = "/srv/nfs/home"
  client_cidrs = [
    "10.0.0.0/8",
  ]
}

variable "parent_id" {
  type        = string
  description = "Nebius project ID."
}

variable "subnet_id" {
  type        = string
  description = "Nebius subnet ID."
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key for the NFS VM admin user."
  sensitive   = true
}

output "server_ip" {
  value = module.nfs.server_ip
}

output "export_path" {
  value = module.nfs.export_path
}
