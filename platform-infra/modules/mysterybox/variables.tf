variable "parent_id" {
  description = "Nebius project ID where MysteryBox secrets are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "secrets" {
  description = <<-EOT
    Map of secret definitions keyed by logical secret ID.
    Each secret defines metadata and the payload key set; values are passed
    separately in `secret_values` to avoid writing cleartext into config files.
  EOT
  type = map(object({
    name                = string
    description         = optional(string, null)
    version_description = optional(string, null)
    labels              = optional(map(string), {})
    set_primary         = optional(bool, true)
    payload_keys        = list(string)
  }))
  default  = {}
  nullable = false

  validation {
    condition     = length(var.secrets) > 0
    error_message = "secrets must contain at least one definition."
  }

  validation {
    condition = alltrue([
      for item in values(var.secrets) :
      length(trimspace(item.name)) > 0
    ])
    error_message = "Every secret definition requires a non-empty name."
  }

  validation {
    condition = alltrue([
      for item in values(var.secrets) :
      length(item.payload_keys) > 0
    ])
    error_message = "Every secret definition requires at least one payload key."
  }

  validation {
    condition = alltrue([
      for item in values(var.secrets) :
      length(distinct(item.payload_keys)) == length(item.payload_keys)
    ])
    error_message = "payload_keys must be unique within each secret definition."
  }
}

variable "secret_values" {
  description = <<-EOT
    Sensitive payload values by secret ID and payload key.
    Provide this at runtime via TF_VAR_mysterybox_secret_values.
    Example:
    {
      app = {
        API_KEY = "..."
      }
    }
  EOT
  type        = map(map(string))
  default     = {}
  nullable    = false
  sensitive   = true
}
