variable "parent_id" {
  description = "Nebius project ID where SFS filesystems are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "name" {
  description = "Single SFS name. Used only when filesystems is empty."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      length(var.filesystems) > 0 ||
      (
        var.name != null &&
        length(trimspace(var.name)) > 0
      )
    )
    error_message = "name must be non-empty when filesystems is empty."
  }
}

variable "size_gib" {
  description = "Single SFS size in GiB. Used only when filesystems is empty."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      length(var.filesystems) > 0 ||
      (
        var.size_gib != null &&
        floor(var.size_gib) == var.size_gib &&
        var.size_gib >= 1
      )
    )
    error_message = "size_gib must be an integer >= 1 when filesystems is empty."
  }
}

variable "block_size_kib" {
  description = "Default SFS block size in KiB."
  type        = number
  default     = 4
  nullable    = false
  validation {
    condition     = floor(var.block_size_kib) == var.block_size_kib && var.block_size_kib >= 4
    error_message = "block_size_kib must be an integer >= 4."
  }
}

variable "type" {
  description = "Default SFS type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}

variable "mount_tag" {
  description = "Single SFS mount tag exposed to consumers. Defaults to name when filesystems is empty."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.mount_tag == null || length(trimspace(var.mount_tag)) > 0
    error_message = "mount_tag cannot be empty when provided."
  }
}

variable "forbid_deletion" {
  description = "Default Nebius filesystem deletion guard."
  type        = bool
  default     = false
  nullable    = false
}

variable "filesystems" {
  description = "Named SFS filesystems for multi-volume workloads such as Soperator jail, controller-spool, accounting, and jail submounts. Set existing_id to reference an existing filesystem."
  type = map(object({
    name            = optional(string)
    existing_id     = optional(string)
    size_gib        = optional(number)
    block_size_kib  = optional(number)
    type            = optional(string)
    mount_tag       = optional(string)
    forbid_deletion = optional(bool)
  }))
  default  = {}
  nullable = false
  validation {
    condition = alltrue([
      for key, filesystem in var.filesystems : (
        length(trimspace(key)) > 0 &&
        try(filesystem.name == null || length(trimspace(filesystem.name)) > 0, true) &&
        try(filesystem.existing_id == null || length(trimspace(filesystem.existing_id)) > 0, true) &&
        try(filesystem.mount_tag == null || length(trimspace(filesystem.mount_tag)) > 0, true) &&
        try(filesystem.size_gib == null || (floor(filesystem.size_gib) == filesystem.size_gib && filesystem.size_gib >= 1), true) &&
        try(filesystem.block_size_kib == null || (floor(filesystem.block_size_kib) == filesystem.block_size_kib && filesystem.block_size_kib >= 4), true)
      )
    ])
    error_message = "Each filesystems entry must have a non-empty key and valid optional name, existing_id, mount_tag, size_gib, and block_size_kib values."
  }
}
