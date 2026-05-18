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
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.name))
    error_message = "name must use lowercase letters, digits, and hyphens, and must not start or end with a hyphen."
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
  nullable    = false
  validation {
    condition     = length(trimspace(var.platform)) > 0
    error_message = "platform cannot be empty."
  }
}

variable "preset" {
  description = "Nebius compute preset for the NFS VM."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.preset)) > 0
    error_message = "preset cannot be empty."
  }
}

variable "source_image_family" {
  description = "Boot image family used when source_image_id and boot_disk_existing_id are not set."
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

variable "boot_disk_existing_id" {
  description = "Existing disk ID to use as the NFS VM boot disk instead of creating one."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.boot_disk_existing_id == null || length(trimspace(var.boot_disk_existing_id)) > 0
    error_message = "boot_disk_existing_id must be null or a non-empty disk ID."
  }
}

variable "boot_disk_size_gib" {
  description = "Boot disk size in GiB when the upstream VM module creates the boot disk."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.boot_disk_size_gib == null ||
      (
        var.boot_disk_size_gib >= 20 &&
        floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
      )
    )
    error_message = "boot_disk_size_gib must be null or an integer >= 20."
  }
}

variable "boot_disk_type" {
  description = "Boot disk type when the upstream VM module creates the boot disk."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
  validation {
    condition = contains(
      ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
      upper(var.boot_disk_type)
    )
    error_message = "boot_disk_type must be one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "boot_disk_block_size_bytes" {
  description = "Boot disk block size in bytes when the upstream VM module creates the boot disk."
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

variable "boot_disk_encryption_enabled" {
  description = "Enable provider-managed data encryption on the boot disk. Nebius supports explicit disk_encryption only for NETWORK_SSD_NON_REPLICATED and NETWORK_SSD_IO_M3; NETWORK_SSD is always encrypted by the platform."
  type        = bool
  default     = false
  nullable    = false
}

variable "boot_disk_deletion_protection" {
  description = "Enable deletion protection on the upstream VM module-managed boot disk."
  type        = bool
  default     = false
  nullable    = false
}

variable "boot_disk_device_id" {
  description = "Optional custom device ID for the NFS VM boot disk."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.boot_disk_device_id == null || length(trimspace(var.boot_disk_device_id)) > 0
    error_message = "boot_disk_device_id must be null or a non-empty string."
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

variable "storage_uid" {
  description = "Numeric UID that owns the exported NFS directory and receives root-squashed writes."
  type        = number
  default     = 1000
  nullable    = false
  validation {
    condition     = floor(var.storage_uid) == var.storage_uid && var.storage_uid > 0
    error_message = "storage_uid must be a positive integer."
  }
}

variable "storage_gid" {
  description = "Numeric GID that owns the exported NFS directory and receives root-squashed writes."
  type        = number
  default     = 2000
  nullable    = false
  validation {
    condition     = floor(var.storage_gid) == var.storage_gid && var.storage_gid > 0
    error_message = "storage_gid must be a positive integer."
  }
}

variable "export_permissions" {
  description = "Octal permissions applied to the exported directory."
  type        = string
  default     = "2770"
  nullable    = false
  validation {
    condition     = can(regex("^[0-7]{3,4}$", var.export_permissions))
    error_message = "export_permissions must be a three- or four-digit octal mode."
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

variable "kubernetes_target_ref" {
  description = "Optional cxcli binding hint for the MK8s target that should consume this NFS export. Terraform resources ignore this value."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.kubernetes_target_ref == null || can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.kubernetes_target_ref))
    error_message = "kubernetes_target_ref must be null or use lowercase letters, digits, and hyphens."
  }
}

variable "mount_options" {
  description = "Recommended Kubernetes NFS mount options exported for chart/cxcli consumers."
  type        = list(string)
  default     = ["nfsvers=4.1"]
  nullable    = false
}

variable "export_options" {
  description = "Optional NFS server export options written to /etc/exports. When null, the module derives root_squash anonuid/anongid options from storage_uid/storage_gid."
  type        = list(string)
  default     = null
  nullable    = true
  validation {
    condition     = var.export_options == null || length(var.export_options) > 0
    error_message = "export_options must be null or contain at least one option."
  }
}

variable "labels" {
  description = "Labels applied to Nebius resources created by this module."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "data_disk_enabled" {
  description = "Create and attach one module-managed secondary data disk for export_path."
  type        = bool
  default     = true
  nullable    = false
}

variable "data_disk_name" {
  description = "Optional name for the NFS data disk. Defaults to <name>-data."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.data_disk_name == null ||
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.data_disk_name))
    )
    error_message = "data_disk_name must be null or use lowercase letters, digits, and hyphens."
  }
}

variable "data_disk_size_gib" {
  description = "NFS data disk size in GiB."
  type        = number
  default     = 128
  nullable    = false
  validation {
    condition     = floor(var.data_disk_size_gib) == var.data_disk_size_gib && var.data_disk_size_gib >= 1
    error_message = "data_disk_size_gib must be an integer >= 1."
  }
}

variable "data_disk_type" {
  description = "NFS data disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
  validation {
    condition = contains(
      ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
      upper(var.data_disk_type)
    )
    error_message = "data_disk_type must be one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "data_disk_block_size_bytes" {
  description = "NFS data disk block size in bytes."
  type        = number
  default     = 4096
  nullable    = false
  validation {
    condition = (
      var.data_disk_block_size_bytes >= 4096 &&
      var.data_disk_block_size_bytes <= 131072 &&
      floor(var.data_disk_block_size_bytes) == var.data_disk_block_size_bytes &&
      floor(log(var.data_disk_block_size_bytes, 2)) == ceil(log(var.data_disk_block_size_bytes, 2))
    )
    error_message = "data_disk_block_size_bytes must be a power of two between 4096 and 131072."
  }
}

variable "data_disk_encryption_enabled" {
  description = "Enable provider-managed data encryption on the NFS data disk. Nebius supports explicit disk_encryption only for NETWORK_SSD_NON_REPLICATED and NETWORK_SSD_IO_M3; NETWORK_SSD is always encrypted by the platform."
  type        = bool
  default     = false
  nullable    = false
}

variable "data_disk_deletion_protection" {
  description = "Enable deletion protection on the NFS data disk."
  type        = bool
  default     = false
  nullable    = false
}

variable "data_disk_device_id" {
  description = "Device ID for the NFS data disk. The guest sees it as /dev/disk/by-id/virtio-<device_id>."
  type        = string
  default     = "nfs-data"
  nullable    = false
  validation {
    condition     = length(trimspace(var.data_disk_device_id)) > 0
    error_message = "data_disk_device_id cannot be empty."
  }
}

variable "data_disk_filesystem_type" {
  description = "Filesystem type used to format the NFS data disk on first boot."
  type        = string
  default     = "ext4"
  nullable    = false
  validation {
    condition     = contains(["ext4", "xfs"], lower(var.data_disk_filesystem_type))
    error_message = "data_disk_filesystem_type must be ext4 or xfs."
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
