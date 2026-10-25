variable "parent_id" {
  description = "Nebius project ID."
  type        = string
  nullable    = false
}
variable "network_id" {
  description = "Existing or same-deployment network ID."
  type        = string
  nullable    = false
}
variable "name" {
  description = "Gateway instance name and resource-name prefix."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,39}[a-z0-9])?$", var.name))
    error_message = "name must be a lowercase resource name of at most 41 characters."
  }
}
variable "gateway_subnet_cidr" {
  description = "Dedicated subnet IPv4 CIDR or provider-allocated prefix length."
  type        = string
  default     = "/28"
  validation {
    condition     = can(cidrnetmask(var.gateway_subnet_cidr)) || can(regex("^/([1-9]|[12][0-9]|3[0-2])$", var.gateway_subnet_cidr))
    error_message = "gateway_subnet_cidr must be an IPv4 CIDR or prefix length."
  }
}
variable "platform" {
  description = "CPU platform validated against live project inventory by the caller."
  type        = string
  default     = "cpu-d3"
}
variable "preset" {
  description = "CPU-only preset validated against live resource metadata by the caller."
  type        = string
  default     = "4vcpu-16gb"
}
variable "source_image_id" {
  description = "Immutable driverless Ubuntu image ID resolved and validated before deployment."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.source_image_id)) > 0
    error_message = "source_image_id must be resolved before deployment."
  }
}
variable "boot_disk_size_gib" {
  description = "Boot disk size, at least the resolved image minimum."
  type        = number
  default     = 20
  validation {
    condition     = var.boot_disk_size_gib > 0 && floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
    error_message = "boot_disk_size_gib must be a positive integer."
  }
}
variable "boot_disk_type" {
  description = "Live-validated Compute disk type."
  type        = string
  default     = "NETWORK_SSD"
}
variable "ssh_user_name" {
  description = "Non-root administration user."
  type        = string
  default     = "ubuntu"
  validation {
    condition     = var.ssh_user_name != "root" && can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user_name))
    error_message = "ssh_user_name must be a non-root Linux user name."
  }
}
variable "ssh_public_key" {
  description = "Public OpenSSH key; never provide a private key."
  type        = string
  nullable    = false
}
variable "admin_cidrs" {
  description = "Explicit IPv4 source ranges permitted to SSH to the gateway."
  type        = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for cidr in var.admin_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "admin_cidrs requires restricted IPv4 CIDRs; unrestricted SSH is not supported."
  }
}
variable "source_cidrs" {
  description = "Verified network private ranges permitted to forward through the gateway."
  type        = list(string)
  validation {
    condition     = length(var.source_cidrs) > 0 && alltrue([for cidr in var.source_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "source_cidrs must contain bounded IPv4 network ranges."
  }
}
variable "routing_policies" {
  description = "Reviewed non-default static routes, keyed by stable policy identity. Consumer attachment is owned separately."
  type = map(list(object({
    destination_cidr = string
    allocation_id    = string
  })))
  default = {}
  validation {
    condition = alltrue(flatten([for key, routes in var.routing_policies : [
      can(regex("^[a-z0-9][a-z0-9-]{0,31}$", key)),
      length(distinct([for route in routes : route.destination_cidr])) == length(routes),
      alltrue([for route in routes : can(cidrnetmask(route.destination_cidr)) && route.destination_cidr != "0.0.0.0/0" && length(trimspace(route.allocation_id)) > 0])
    ]]))
    error_message = "Policy keys must be stable names and routes must have unique non-default IPv4 destinations and allocation IDs."
  }
}
variable "labels" {
  description = "Additional resource labels."
  type        = map(string)
  default     = {}
}
