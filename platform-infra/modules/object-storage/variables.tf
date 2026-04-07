variable "parent_id" {
  description = "Nebius project ID where the bucket is created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "name" {
  description = "Nebius Object Storage bucket name."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$", var.name))
    error_message = "name must use lowercase letters, digits, and hyphens."
  }
}

variable "versioning_policy" {
  description = "Bucket versioning policy."
  type        = string
  default     = "DISABLED"
  nullable    = false

  validation {
    condition     = contains(["DISABLED", "ENABLED", "SUSPENDED"], var.versioning_policy)
    error_message = "versioning_policy must be one of: DISABLED, ENABLED, SUSPENDED."
  }
}

variable "object_audit_logging" {
  description = "Bucket object audit logging policy."
  type        = string
  default     = "NONE"
  nullable    = false

  validation {
    condition     = contains(["NONE", "MUTATE_ONLY", "ALL"], var.object_audit_logging)
    error_message = "object_audit_logging must be one of: NONE, MUTATE_ONLY, ALL."
  }
}

variable "protect_from_destroy" {
  description = "When true, add Terraform prevent_destroy lifecycle protection."
  type        = bool
  default     = false
  nullable    = false
}

variable "labels" {
  description = "Optional Nebius bucket labels."
  type        = map(string)
  default     = {}
  nullable    = false
}
