variable "parent_id" {
  description = "Nebius project ID where the NFS VM and disks are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "name" {
  description = "NFS VM name."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "name cannot be empty."
  }
}

variable "subnet_id" {
  description = "VPC subnet ID for the NFS VM."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.subnet_id)) > 0
    error_message = "subnet_id cannot be empty."
  }
}

variable "platform" {
  description = "Nebius compute platform for the NFS VM."
  type        = string
  default     = "cpu-d3"
  nullable    = false
  validation {
    condition     = length(trimspace(var.platform)) > 0
    error_message = "platform cannot be empty."
  }
}

variable "preset" {
  description = "Nebius compute preset for the NFS VM."
  type        = string
  default     = "2vcpu-8gb"
  nullable    = false
  validation {
    condition     = length(trimspace(var.preset)) > 0
    error_message = "preset cannot be empty."
  }
}

variable "source_image_family" {
  description = "Boot image family used when source_image_id is not set."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.source_image_family == null || length(trimspace(var.source_image_family)) > 0
    error_message = "source_image_family cannot be empty when provided."
  }
}

variable "source_image_id" {
  description = "Optional boot image ID. When null, source_image_family is used."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.source_image_id == null || length(trimspace(var.source_image_id)) > 0
    error_message = "source_image_id cannot be empty when provided."
  }
}

variable "boot_disk_size_gib" {
  description = "Boot disk size in GiB."
  type        = number
  default     = 32
  nullable    = false
  validation {
    condition     = floor(var.boot_disk_size_gib) == var.boot_disk_size_gib && var.boot_disk_size_gib >= 1
    error_message = "boot_disk_size_gib must be an integer >= 1."
  }
}

variable "boot_disk_type" {
  description = "Boot disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}

variable "boot_disk_block_size_bytes" {
  description = "Boot disk block size in bytes."
  type        = number
  default     = 4096
  nullable    = false
  validation {
    condition     = floor(var.boot_disk_block_size_bytes) == var.boot_disk_block_size_bytes && var.boot_disk_block_size_bytes >= 4096
    error_message = "boot_disk_block_size_bytes must be an integer >= 4096."
  }
}

variable "ssh_user_name" {
  description = "Admin Linux user created on the NFS VM."
  type        = string
  default     = "ubuntu"
  nullable    = false
  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user_name))
    error_message = "ssh_user_name must be a valid Linux username."
  }
}

variable "ssh_public_key" {
  description = "SSH public key for the admin Linux user."
  type        = string
  nullable    = false
  sensitive   = true
  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+)\\s+\\S+.*$", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must be a valid SSH public key."
  }
}

variable "public_ip_mode" {
  description = "Public IP mode for the NFS VM: none, dynamic, static, or allocation."
  type        = string
  default     = "none"
  nullable    = false
  validation {
    condition     = contains(["none", "dynamic", "static", "allocation"], lower(var.public_ip_mode))
    error_message = "public_ip_mode must be none, dynamic, static, or allocation."
  }
}

variable "public_ip_allocation_id" {
  description = "Existing public IP allocation ID when public_ip_mode=allocation."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.public_ip_allocation_id == null || length(trimspace(var.public_ip_allocation_id)) > 0
    error_message = "public_ip_allocation_id cannot be empty when provided."
  }
}

variable "private_ip_allocation_id" {
  description = "Optional existing private IP allocation ID."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.private_ip_allocation_id == null || length(trimspace(var.private_ip_allocation_id)) > 0
    error_message = "private_ip_allocation_id cannot be empty when provided."
  }
}

variable "security_group_ids" {
  description = "Security group IDs attached to the NFS VM interface."
  type        = list(string)
  default     = []
  nullable    = false
  validation {
    condition     = alltrue([for group_id in var.security_group_ids : length(trimspace(group_id)) > 0])
    error_message = "security_group_ids entries cannot be empty."
  }
}

variable "export_path" {
  description = "Filesystem path exported by NFS."
  type        = string
  default     = "/srv/nfs/home"
  nullable    = false
  validation {
    condition     = startswith(var.export_path, "/") && length(trimspace(var.export_path)) > 1
    error_message = "export_path must be an absolute path."
  }
}

variable "client_cidrs" {
  description = "CIDR ranges allowed in /etc/exports."
  type        = list(string)
  default     = ["10.0.0.0/8"]
  nullable    = false
  validation {
    condition     = length(var.client_cidrs) > 0 && alltrue([for cidr in var.client_cidrs : length(trimspace(cidr)) > 0])
    error_message = "client_cidrs must contain at least one non-empty entry."
  }
}

variable "mount_options" {
  description = "Recommended Kubernetes NFS mount options exported for chart/cxcli consumers."
  type        = list(string)
  default     = ["vers=4.2", "hard", "timeo=600", "retrans=2"]
  nullable    = false
}

variable "export_options" {
  description = "NFS server export options written to /etc/exports."
  type        = list(string)
  default     = ["rw", "sync", "no_subtree_check", "no_root_squash"]
  nullable    = false
}

variable "labels" {
  description = "Labels applied to Nebius resources created by this module."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "data_disk" {
  description = "Optional Nebius data disk formatted and mounted at export_path."
  type = object({
    enabled          = optional(bool, true)
    name             = optional(string)
    size_gib         = optional(number, 128)
    type             = optional(string, "NETWORK_SSD")
    block_size_bytes = optional(number, 4096)
    device_id        = optional(string, "nfs-data")
    filesystem_type  = optional(string, "ext4")
  })
  default  = {}
  nullable = false
  validation {
    condition = (
      try(var.data_disk.enabled, true) == false ||
      (
        try(floor(var.data_disk.size_gib) == var.data_disk.size_gib && var.data_disk.size_gib >= 1, true) &&
        try(floor(var.data_disk.block_size_bytes) == var.data_disk.block_size_bytes && var.data_disk.block_size_bytes >= 4096, true) &&
        try(length(trimspace(var.data_disk.device_id)) > 0, true) &&
        contains(["ext4", "xfs"], lower(try(var.data_disk.filesystem_type, "ext4")))
      )
    )
    error_message = "data_disk must use valid size, block size, device_id, and filesystem_type when enabled."
  }
}

variable "filesystems" {
  description = "Existing Nebius shared filesystems to attach and mount before exporting."
  type = list(object({
    id          = string
    mount_tag   = string
    mount_path  = string
    attach_mode = optional(string, "READ_WRITE")
  }))
  default  = []
  nullable = false
  validation {
    condition = alltrue([
      for filesystem in var.filesystems : (
        length(trimspace(filesystem.id)) > 0 &&
        length(trimspace(filesystem.mount_tag)) > 0 &&
        startswith(filesystem.mount_path, "/") &&
        contains(["READ_ONLY", "READ_WRITE"], upper(try(filesystem.attach_mode, "READ_WRITE")))
      )
    ])
    error_message = "Each filesystems entry must include id, mount_tag, absolute mount_path, and a supported attach_mode."
  }
}
