variable "enabled" {
  description = "Create Nebius shared filesystem."
  type        = bool
  default     = false
  nullable    = false
}

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
  default     = ""
  nullable    = false
}

variable "size_gib" {
  description = "SFS size in GiB."
  type        = number
  default     = 0
  nullable    = false
  validation {
    condition     = floor(var.size_gib) == var.size_gib && var.size_gib >= 0
    error_message = "size_gib must be an integer >= 0."
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
