variable "parent_id" {
  description = "Nebius project ID where SFS is created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "name" {
  description = "SFS name."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "name cannot be empty."
  }
}

variable "size_gib" {
  description = "SFS size in GiB."
  type        = number
  nullable    = false
  validation {
    condition     = floor(var.size_gib) == var.size_gib && var.size_gib >= 1
    error_message = "size_gib must be an integer >= 1."
  }
}

variable "block_size_kib" {
  description = "SFS block size in KiB."
  type        = number
  default     = 4
  nullable    = false
  validation {
    condition     = floor(var.block_size_kib) == var.block_size_kib && var.block_size_kib >= 4
    error_message = "block_size_kib must be an integer >= 4."
  }
}

variable "type" {
  description = "SFS type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}
