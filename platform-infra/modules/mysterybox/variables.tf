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
    List of MysteryBox secret definitions.
    Each secret name is the stable identity for Terraform outputs and runtime
    payload injection. Terraform creates one initial primary version per
    secret from the declared payload shape. The optional version_id records the
    current primary MysteryBox version after deployment; use "n/a" or leave it
    unset before the first deploy. Payload entries define MysteryBox payload
    keys and value types; values are passed separately in `payload_values` to
    avoid writing cleartext into configuration files.
  EOT
  type = list(object({
    name        = string
    description = optional(string, null)
    labels      = optional(map(string), {})
    version_id  = optional(string)
    payload = map(object({
      type = optional(string, "text")
    }))
  }))
  nullable = false

  validation {
    condition     = length(var.secrets) > 0
    error_message = "secrets must contain at least one definition."
  }

  validation {
    condition = alltrue([
      for item in var.secrets :
      length(trimspace(item.name)) > 0
    ])
    error_message = "Every secret definition requires a non-empty name."
  }

  validation {
    condition = length(distinct([
      for item in var.secrets :
      trimspace(item.name)
    ])) == length(var.secrets)
    error_message = "Secret names must be unique."
  }

  validation {
    condition = alltrue([
      for item in var.secrets :
      (
        trimspace(coalesce(try(item.version_id, null), "n/a")) == "" ||
        lower(trimspace(coalesce(try(item.version_id, null), "n/a"))) == "n/a" ||
        can(regex("^mbsecver-[a-z0-9]+$", trimspace(coalesce(try(item.version_id, null), "n/a"))))
      )
    ])
    error_message = "version_id must be empty, \"n/a\", or a MysteryBox version ID starting with mbsecver-."
  }

  validation {
    condition = alltrue([
      for item in var.secrets :
      length(item.payload) > 0
    ])
    error_message = "Every secret definition requires a non-empty payload map."
  }

  validation {
    condition = alltrue([
      for item in var.secrets :
      alltrue([
        for payload_key in keys(item.payload) :
        length(trimspace(payload_key)) > 0
      ])
    ])
    error_message = "payload keys must contain non-empty MysteryBox payload entry keys."
  }

  validation {
    condition = alltrue([
      for item in var.secrets :
      alltrue([
        for payload in values(item.payload) :
        contains(["text", "file"], lower(coalesce(try(payload.type, null), "text")))
      ])
    ])
    error_message = "payload entries must use type \"text\" or \"file\"."
  }
}

variable "payload_values" {
  description = <<-EOT
    Sensitive payload values by MysteryBox secret name and payload key.
    Provide this at runtime through a caller-root sensitive variable, for example
    TF_VAR_mysterybox_payload_values in the minimal example.
    Example:
    {
      "app-runtime" = {
        API_KEY = "..."
      }
    }
  EOT
  type        = map(map(string))
  default     = {}
  nullable    = false
  sensitive   = true
}
