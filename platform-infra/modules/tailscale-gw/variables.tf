variable "parent_id" {
  description = "Nebius project owning the gateways."
  type        = string
  nullable    = false
}
variable "network_id" {
  description = "Nebius VPC containing every advertised destination."
  type        = string
  nullable    = false
}
variable "subnet_id" {
  description = "Gateway subnet, with a public allocation for each member."
  type        = string
  nullable    = false
}
variable "name" {
  description = "Gateway pair name; member names append -a and -b."
  type        = string
  default     = "tailscale-gw"
  nullable    = false
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,57}[a-z0-9]$", var.name))
    error_message = "name must be 2..59 lowercase letters, digits or hyphens."
  }
}
variable "replicas" {
  description = "Two persistent routers provide failover; one explicitly disables HA."
  type        = number
  default     = 2
  nullable    = false
  validation {
    condition     = contains([1, 2], var.replicas)
    error_message = "replicas must be 1 or 2."
  }
}
variable "platform" {
  description = "CPU platform, validated against live Nebius availability."
  type        = string
  nullable    = false
}
variable "preset" {
  description = "CPU preset for each member."
  type        = string
  nullable    = false
}
variable "source_image_family" {
  description = "Ubuntu 24.04 image family for the managed guest."
  type        = string
  default     = "ubuntu24.04-driverless"
  nullable    = false
  validation {
    condition     = var.source_image_family == "ubuntu24.04-driverless"
    error_message = "The managed guest requires the Ubuntu 24.04 driverless image family."
  }
}
variable "boot_disk_size_gib" {
  description = "Persistent boot disk size per member."
  type        = number
  default     = 40
  nullable    = false
  validation {
    condition     = var.boot_disk_size_gib >= 20 && floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
    error_message = "boot_disk_size_gib must be an integer of at least 20."
  }
}
variable "boot_disk_type" {
  description = "Nebius disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}
variable "member_overrides" {
  description = "Per-member infrastructure overrides for staged maintenance."
  type = map(object({
    platform            = optional(string)
    preset              = optional(string)
    source_image_family = optional(string)
    boot_disk_size_gib  = optional(number)
  }))
  default  = {}
  nullable = false
  validation {
    condition     = alltrue([for key in keys(var.member_overrides) : contains(["a", "b"], key)])
    error_message = "member_overrides keys must be a or b."
  }
  validation {
    condition     = alltrue([for value in values(var.member_overrides) : value.source_image_family == null || value.source_image_family == "ubuntu24.04-driverless"])
    error_message = "Member images must use Ubuntu 24.04 driverless."
  }
}
variable "ssh_user_name" {
  description = "Unprivileged SSH account with sudo for cxcli management."
  type        = string
  default     = "ubuntu"
  nullable    = false
}
variable "ssh_public_key" {
  description = "Administrator SSH public key, never a private key."
  type        = string
  nullable    = false
}
variable "management_cidrs" {
  description = "IPv4 sources allowed to reach SSH on both public gateway addresses."
  type        = list(string)
  nullable    = false
  validation {
    condition     = length(var.management_cidrs) > 0 && alltrue([for cidr in var.management_cidrs : try(cidrnetmask(cidr) != "0.0.0.0", false)])
    error_message = "Provide nonempty IPv4 management CIDRs; unrestricted SSH is not permitted."
  }
}
variable "tailscale_version" {
  description = "Initial stable Tailscale package pin; cxcli reconciles later upgrades over SSH."
  type        = string
  default     = "1.104.4"
  nullable    = false
  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.tailscale_version))
    error_message = "tailscale_version must be an exact stable package version."
  }
}
variable "ssh_host_keys" {
  description = "cxcli runtime-only per-member SSH host identities; protected Terraform state/metadata are secret-bearing."
  type        = map(map(string))
  default     = {}
  sensitive   = true
  nullable    = false
}
variable "bootstrap_user_data" {
  description = "cxcli runtime-only immutable per-member bootstrap snapshots encoded as JSON."
  type        = string
  default     = null
  sensitive   = true
  nullable    = true
}
variable "labels" {
  description = "Additional labels; component ownership labels cannot be overridden."
  type        = map(string)
  default     = {}
  nullable    = false
}
