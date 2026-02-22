variable "enabled" {
  description = "Create managed PostgreSQL cluster."
  type        = bool
  default     = false
  nullable    = false
}

variable "parent_id" {
  description = "Nebius project ID where PostgreSQL cluster is created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "network_id" {
  description = "Nebius VPC network ID for PostgreSQL cluster."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.network_id)) > 0
    error_message = "network_id cannot be empty."
  }
}

variable "name" {
  description = "Managed PostgreSQL cluster name."
  type        = string
  default     = ""
  nullable    = false
}

variable "tier" {
  description = "Sizing tier: small | medium | large."
  type        = string
  default     = "medium"
  nullable    = false
  validation {
    condition     = contains(["small", "medium", "large"], lower(var.tier))
    error_message = "tier must be one of: small, medium, large."
  }
}

variable "storage_gib" {
  description = "Disk size in GiB."
  type        = number
  default     = 100
  nullable    = false
  validation {
    condition     = floor(var.storage_gib) == var.storage_gib && var.storage_gib >= 1
    error_message = "storage_gib must be an integer >= 1."
  }
}

variable "postgresql_version" {
  description = "PostgreSQL major version."
  type        = number
  default     = 16
  nullable    = false
  validation {
    condition     = floor(var.postgresql_version) == var.postgresql_version && var.postgresql_version >= 10
    error_message = "postgresql_version must be an integer >= 10."
  }
}

variable "public_access" {
  description = "Expose PostgreSQL public endpoints."
  type        = bool
  default     = false
  nullable    = false
}

variable "bootstrap_db_name" {
  description = "Bootstrap DB name."
  type        = string
  default     = "app"
  nullable    = false
  validation {
    condition     = length(trimspace(var.bootstrap_db_name)) > 0
    error_message = "bootstrap_db_name cannot be empty."
  }
}

variable "bootstrap_user_name" {
  description = "Bootstrap user name."
  type        = string
  default     = "app"
  nullable    = false
  validation {
    condition     = length(trimspace(var.bootstrap_user_name)) > 0
    error_message = "bootstrap_user_name cannot be empty."
  }
}

variable "bootstrap_user_password" {
  description = "Bootstrap user password. If null, a random password is generated."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}
