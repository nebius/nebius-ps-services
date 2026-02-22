variable "parent_id" {
  description = "Nebius project ID where jump-host resources are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "region" {
  description = "Nebius region ID used for defaults and metadata."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.region)) > 0
    error_message = "region cannot be empty."
  }
}

variable "subnet_id" {
  description = "Subnet ID where the jump-host VM interface is attached."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.subnet_id)) > 0
    error_message = "subnet_id cannot be empty."
  }
}

variable "name" {
  description = "Jump-host VM name."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.name))
    error_message = "name must use lowercase letters, digits, and hyphens, and must not start/end with hyphen."
  }
}

variable "platform" {
  description = "Compute platform for jump-host VM. If null, region defaults are used."
  type        = string
  default     = null
  nullable    = true
}

variable "preset" {
  description = "Compute preset for jump-host VM. If null, region defaults are used."
  type        = string
  default     = null
  nullable    = true
}

variable "ssh_user_name" {
  description = "SSH username created on the jump-host VM."
  type        = string
  default     = "ubuntu"
  nullable    = false
  validation {
    condition = (
      length(trimspace(var.ssh_user_name)) > 0 &&
      can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user_name))
    )
    error_message = "ssh_user_name must match Linux username format (for example ubuntu, admin_user)."
  }
}

variable "ssh_public_key" {
  description = "Inline SSH public key content for initial VM access."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.ssh_public_key)) >= 20
    error_message = "ssh_public_key must be a valid inline public key string."
  }
}

variable "allowed_cidrs" {
  description = "Allowed source CIDRs for inbound SSH on the jump-host VM."
  type        = list(string)
  default     = []
  nullable    = false
  validation {
    condition = alltrue([
      for cidr in var.allowed_cidrs : try(cidrhost(cidr, 0), null) != null
    ])
    error_message = "allowed_cidrs must contain valid CIDRs (for example 203.0.113.10/32)."
  }
  validation {
    condition     = length(var.allowed_cidrs) > 0
    error_message = "allowed_cidrs must contain at least one CIDR to avoid SSH lockout."
  }
}

variable "create_public_ip_allocation" {
  description = "Create a dedicated static public IP allocation and attach it to the VM."
  type        = bool
  default     = true
  nullable    = false
}

variable "public_ip_allocation_id" {
  description = "Use an existing public IP allocation ID instead of creating a new one."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = !(
      var.public_ip_allocation_id != null &&
      var.create_public_ip_allocation
    )
    error_message = "Set create_public_ip_allocation=false when public_ip_allocation_id is provided."
  }
  validation {
    condition = (
      (var.public_ip_allocation_id == null && var.create_public_ip_allocation) ||
      (
        var.public_ip_allocation_id != null &&
        length(trimspace(var.public_ip_allocation_id)) > 0
      )
    )
    error_message = "Set create_public_ip_allocation=true, or provide a non-empty public_ip_allocation_id."
  }
}

variable "public_ip_allocation_name" {
  description = "Name for created public IP allocation. Ignored when using existing allocation_id."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.public_ip_allocation_name == null ||
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.public_ip_allocation_name))
    )
    error_message = "public_ip_allocation_name must use lowercase letters, digits, and hyphens."
  }
}

variable "boot_disk_size_gib" {
  description = "Boot disk size in GiB."
  type        = number
  default     = 60
  nullable    = false
  validation {
    condition = (
      var.boot_disk_size_gib >= 20 &&
      floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
    )
    error_message = "boot_disk_size_gib must be an integer >= 20."
  }
}

variable "boot_disk_block_size_bytes" {
  description = "Boot disk block size in bytes."
  type        = number
  default     = 4096
  nullable    = false
  validation {
    condition = (
      var.boot_disk_block_size_bytes >= 4096 &&
      var.boot_disk_block_size_bytes <= 131072 &&
      floor(var.boot_disk_block_size_bytes) == var.boot_disk_block_size_bytes &&
      floor(log(var.boot_disk_block_size_bytes, 2)) == ceil(log(var.boot_disk_block_size_bytes, 2))
    )
    error_message = "boot_disk_block_size_bytes must be a power of two between 4096 and 131072."
  }
}

variable "boot_disk_type" {
  description = "Boot disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
  validation {
    condition = contains(
      ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
      var.boot_disk_type
    )
    error_message = "boot_disk_type must be one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "source_image_family" {
  description = "Image family used for the jump-host VM boot disk."
  type        = string
  default     = "ubuntu22.04-driverless"
  nullable    = false
  validation {
    condition     = length(trimspace(var.source_image_family)) > 0
    error_message = "source_image_family cannot be empty."
  }
}

variable "labels" {
  description = "Optional labels applied to created resources."
  type        = map(string)
  default     = {}
  nullable    = false
}
