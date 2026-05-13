module "sfs" {
  source = "../.."

  parent_id   = var.parent_id
  filesystems = var.filesystems
}

variable "parent_id" {
  type        = string
  description = "Nebius project ID."
}

variable "filesystems" {
  type        = any
  description = "Caller-owned SFS filesystems keyed by any logical name."
  default = {
    shared = {
      name      = "example-shared"
      size_gib  = 500
      mount_tag = "shared"
    }
    scratch = {
      name      = "example-scratch"
      size_gib  = 1024
      mount_tag = "scratch"
    }
  }
}

output "filesystems" {
  description = "Named filesystem metadata keyed by input map key."
  value       = module.sfs.filesystems
}
