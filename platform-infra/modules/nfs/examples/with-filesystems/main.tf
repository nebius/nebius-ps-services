module "nfs" {
  source = "../.."

  parent_id           = var.parent_id
  network_id          = var.network_id
  name                = var.name
  subnet_id           = var.subnet_id
  platform            = "cpu-d3"
  preset              = "2vcpu-8gb"
  ssh_public_key      = var.ssh_public_key
  source_image_family = "ubuntu24.04"

  boot_disk_size_gib = 64

  export_path       = var.export_path
  client_cidrs      = var.client_cidrs
  data_disk_enabled = false
  filesystems       = var.filesystems
}

variable "parent_id" {
  type        = string
  description = "Nebius project ID."
}

variable "network_id" {
  type        = string
  description = "Nebius VPC network ID."
}

variable "name" {
  type        = string
  description = "NFS VM name."
  default     = "example-nfs-with-filesystems"
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

variable "export_path" {
  type        = string
  description = "NFS export path."
  default     = "/srv/nfs/home"
}

variable "client_cidrs" {
  type        = list(string)
  description = "CIDR ranges allowed to mount the NFS export."
  default     = ["10.0.0.0/8"]
}

variable "filesystems" {
  type = list(object({
    id          = string
    mount_tag   = string
    mount_path  = string
    attach_mode = optional(string, "READ_WRITE")
  }))
  description = "Caller-owned Nebius shared filesystems attached to the NFS VM."
  default = [
    {
      id         = "filesystem-xxxxxxxx"
      mount_tag  = "shared"
      mount_path = "/srv/nfs/home"
    },
  ]
}

output "export_spec" {
  description = "Structured NFS export metadata for cxcli and Helm values."
  value       = module.nfs.export_spec
}
