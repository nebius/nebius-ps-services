variable "parent_id" {
  description = "Nebius project ID where buckets are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "buckets" {
  description = <<-EOT
    Map of object-storage bucket definitions keyed by logical ID (for example
    "state", "inventory", "artifacts"). Each entry controls bucket name and
    policies.
  EOT
  type = map(object({
    name                 = string
    versioning_policy    = optional(string, "DISABLED")
    object_audit_logging = optional(string, "NONE")
    protect_from_destroy = optional(bool, false)
    labels               = optional(map(string), {})
  }))
  default  = {}
  nullable = false

  validation {
    condition     = length(var.buckets) > 0
    error_message = "buckets must contain at least one bucket definition."
  }

  validation {
    condition = length(distinct([
      for bucket in values(var.buckets) : bucket.name
    ])) == length(values(var.buckets))
    error_message = "bucket names must be unique across buckets map entries."
  }

  validation {
    condition = alltrue([
      for bucket in values(var.buckets) :
      can(regex("^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$", bucket.name))
    ])
    error_message = "each bucket.name must use lowercase letters, digits, and hyphens."
  }

  validation {
    condition = alltrue([
      for bucket in values(var.buckets) :
      contains(["DISABLED", "ENABLED", "SUSPENDED"], bucket.versioning_policy)
    ])
    error_message = "versioning_policy must be one of: DISABLED, ENABLED, SUSPENDED."
  }

  validation {
    condition = alltrue([
      for bucket in values(var.buckets) :
      contains(["NONE", "MUTATE_ONLY", "ALL"], bucket.object_audit_logging)
    ])
    error_message = "object_audit_logging must be one of: NONE, MUTATE_ONLY, ALL."
  }
}
