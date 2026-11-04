variable "parent_id" {
  description = "Project containing the gateway and its access secrets."
  type        = string
  nullable    = false
}
variable "name" {
  description = "Stable access dependency instance name."
  type        = string
  nullable    = false
}
variable "admin_group_id" {
  description = "Existing operator-managed group receiving login and connection payload access."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.admin_group_id)) > 0
    error_message = "An exact administrator group ID is required."
  }
}
variable "secret_ids" {
  description = "Metadata-only secret containers keyed by login, host and connection."
  type = object({
    login      = string
    host       = string
    connection = string
  })
  nullable = false
  validation {
    condition     = length(distinct(values(var.secret_ids))) == 3
    error_message = "Access roles require three distinct secret containers."
  }
}
