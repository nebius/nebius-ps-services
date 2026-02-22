variable "tenant_id" {
  description = "Nebius tenant ID."
  type        = string
  nullable    = false
}

variable "parent_id" {
  description = "Nebius project ID."
  type        = string
  nullable    = false
}

variable "region" {
  description = "Nebius region ID."
  type        = string
  nullable    = false
}

variable "cluster_name" {
  description = "Cluster name."
  type        = string
  nullable    = false
}

variable "subnet_id" {
  description = "MK8s subnet ID."
  type        = string
  nullable    = false
}

variable "ssh_user_name" {
  description = "Shared SSH username used by MK8s bootstrap and jump-host modules."
  type        = string
  default     = "ubuntu"
  nullable    = false
  validation {
    condition = (
      length(trimspace(var.ssh_user_name)) > 0 &&
      can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user_name))
    )
    error_message = "ssh_user_name must match Linux username format (for example ubuntu, admin_user)."
  }
}

variable "ssh_public_key" {
  description = "Shared inline SSH public key used by MK8s bootstrap and jump-host modules."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.ssh_public_key)) >= 20
    error_message = "ssh_public_key must be a valid inline SSH public key."
  }
}

variable "cpu_nodes_count" {
  description = "CPU node count."
  type        = number
  default     = 2
  nullable    = false
}

variable "cpu_nodes_platform" {
  description = "CPU node platform."
  type        = string
  nullable    = false
}

variable "cpu_nodes_preset" {
  description = "CPU node preset."
  type        = string
  nullable    = false
}

variable "cpu_nodes_preemptible" {
  description = "CPU preemptible mode."
  type        = bool
  default     = false
  nullable    = false
}

variable "cpu_nodes_public_ips" {
  description = "CPU public IP mode."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_enabled" {
  description = "Enable GPU nodes."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_node_groups" {
  description = "GPU node group count."
  type        = number
  default     = 0
  nullable    = false
}

variable "gpu_nodes_count_per_group" {
  description = "GPU nodes per group."
  type        = number
  default     = 0
  nullable    = false
}

variable "gpu_nodes_platform" {
  description = "GPU node platform."
  type        = string
  default     = ""
  nullable    = false
}

variable "gpu_nodes_preset" {
  description = "GPU node preset."
  type        = string
  default     = ""
  nullable    = false
}

variable "gpu_nodes_preemptible" {
  description = "GPU preemptible mode."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_nodes_public_ips" {
  description = "GPU public IP mode."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_driverfull_image" {
  description = "GPU driverfull image mode."
  type        = bool
  default     = true
  nullable    = false
}

variable "infiniband_fabric" {
  description = "GPU fabric name."
  type        = string
  default     = ""
  nullable    = false
}

variable "mk8s_cluster_public_endpoint" {
  description = "MK8s public control plane endpoint toggle."
  type        = bool
  default     = false
  nullable    = false
}

variable "k8s_version" {
  description = "Kubernetes version."
  type        = string
  default     = null
  nullable    = true
}

variable "etcd_cluster_size" {
  description = "etcd cluster size."
  type        = number
  default     = null
  nullable    = true
}

variable "mig_strategy" {
  description = "MIG strategy label hint."
  type        = string
  default     = null
  nullable    = true
}

variable "mig_parted_config" {
  description = "MIG parted config label value."
  type        = string
  default     = null
  nullable    = true
}

variable "mk8s_cluster_overrides" {
  description = "Optional cluster override object."
  type        = map(any)
  default     = {}
  nullable    = false
}

variable "mk8s_cpu_node_group_overrides" {
  description = "Optional CPU node group override object."
  type        = map(any)
  default     = {}
  nullable    = false
}

variable "mk8s_gpu_node_group_overrides" {
  description = "Optional GPU node group override object."
  type        = map(any)
  default     = {}
  nullable    = false
}

variable "managed_postgresql_enabled" {
  description = "Enable managed PostgreSQL."
  type        = bool
  default     = false
  nullable    = false
}

variable "managed_postgresql_name" {
  description = "Managed PostgreSQL cluster name."
  type        = string
  default     = ""
  nullable    = false
}

variable "managed_postgresql_tier" {
  description = "Managed PostgreSQL tier."
  type        = string
  default     = "medium"
  nullable    = false
}

variable "managed_postgresql_storage_gib" {
  description = "Managed PostgreSQL storage GiB."
  type        = number
  default     = 100
  nullable    = false
}

variable "managed_postgresql_postgresql_version" {
  description = "Managed PostgreSQL major version."
  type        = number
  default     = 16
  nullable    = false
}

variable "managed_postgresql_public_access" {
  description = "Expose managed PostgreSQL public endpoints."
  type        = bool
  default     = false
  nullable    = false
}

variable "sfs_enabled" {
  description = "Enable shared filesystem."
  type        = bool
  default     = false
  nullable    = false
}

variable "sfs_name" {
  description = "SFS name."
  type        = string
  default     = ""
  nullable    = false
}

variable "sfs_size_gib" {
  description = "SFS size GiB."
  type        = number
  default     = 0
  nullable    = false
}

variable "sfs_block_size_kib" {
  description = "SFS block size KiB."
  type        = number
  default     = 4
  nullable    = false
}

variable "sfs_type" {
  description = "SFS filesystem type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
  validation {
    condition = contains(
      ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
      var.sfs_type
    )
    error_message = "sfs_type must be one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "state_bucket_name" {
  description = "Terraform state bucket name."
  type        = string
  default     = ""
  nullable    = false
  validation {
    condition = (
      !var.state_bucket_manage || length(trimspace(var.state_bucket_name)) > 0
    )
    error_message = "state_bucket_name must be set when state_bucket_manage=true."
  }
}

variable "state_bucket_prefix" {
  description = "Terraform state prefix."
  type        = string
  default     = "tfstate"
  nullable    = false
}

variable "state_bucket_use_lockfile" {
  description = "Terraform state lockfile setting."
  type        = bool
  default     = true
  nullable    = false
}

variable "state_bucket_versioning_policy" {
  description = "State bucket versioning policy."
  type        = string
  default     = "ENABLED"
  nullable    = false
  validation {
    condition     = contains(["DISABLED", "ENABLED", "SUSPENDED"], var.state_bucket_versioning_policy)
    error_message = "state_bucket_versioning_policy must be DISABLED, ENABLED, or SUSPENDED."
  }
}

variable "state_bucket_object_audit_logging" {
  description = "State bucket audit logging policy."
  type        = string
  default     = "ALL"
  nullable    = false
  validation {
    condition     = contains(["NONE", "MUTATE_ONLY", "ALL"], var.state_bucket_object_audit_logging)
    error_message = "state_bucket_object_audit_logging must be NONE, MUTATE_ONLY, or ALL."
  }
}

variable "inventory_bucket_name" {
  description = "Inventory bucket name."
  type        = string
  default     = ""
  nullable    = false
  validation {
    condition = (
      !var.inventory_bucket_manage || length(trimspace(var.inventory_bucket_name)) > 0
    )
    error_message = "inventory_bucket_name must be set when inventory_bucket_manage=true."
  }
}

variable "inventory_bucket_prefix" {
  description = "Inventory bucket prefix."
  type        = string
  default     = "inventory"
  nullable    = false
}

variable "state_bucket_manage" {
  description = "Manage state bucket resource from this stack."
  type        = bool
  default     = false
  nullable    = false
}

variable "state_bucket_protect_from_destroy" {
  description = "Protect state bucket from Terraform destroy."
  type        = bool
  default     = true
  nullable    = false
}

variable "inventory_bucket_versioning_policy" {
  description = "Inventory bucket versioning policy."
  type        = string
  default     = "DISABLED"
  nullable    = false
  validation {
    condition = contains(
      ["DISABLED", "ENABLED", "SUSPENDED"],
      var.inventory_bucket_versioning_policy
    )
    error_message = "inventory_bucket_versioning_policy must be DISABLED, ENABLED, or SUSPENDED."
  }
}

variable "inventory_bucket_object_audit_logging" {
  description = "Inventory bucket object audit logging policy."
  type        = string
  default     = "NONE"
  nullable    = false
  validation {
    condition = contains(
      ["NONE", "MUTATE_ONLY", "ALL"],
      var.inventory_bucket_object_audit_logging
    )
    error_message = "inventory_bucket_object_audit_logging must be NONE, MUTATE_ONLY, or ALL."
  }
}

variable "inventory_bucket_manage" {
  description = "Manage inventory bucket resource from this stack."
  type        = bool
  default     = true
  nullable    = false
  validation {
    condition     = var.inventory_bucket_manage || var.state_bucket_manage
    error_message = "At least one of inventory_bucket_manage or state_bucket_manage must be true."
  }
}

variable "inventory_bucket_protect_from_destroy" {
  description = "Protect inventory bucket from Terraform destroy."
  type        = bool
  default     = false
  nullable    = false
}

variable "mysterybox_enabled" {
  description = "Enable MysteryBox secret management from this stack."
  type        = bool
  default     = false
  nullable    = false
}

variable "mysterybox_secrets" {
  description = "MysteryBox secret definitions for platform and in-cluster workloads."
  type = list(object({
    id                  = string
    scope               = optional(string, "platform")
    name                = string
    description         = optional(string, null)
    version_description = optional(string, null)
    labels              = optional(map(string), {})
    set_primary         = optional(bool, true)
    payload_keys        = list(string)
  }))
  default  = []
  nullable = false

  validation {
    condition = (
      !var.mysterybox_enabled || length(var.mysterybox_secrets) > 0
    )
    error_message = "mysterybox_secrets must contain at least one item when mysterybox_enabled=true."
  }

  validation {
    condition = (
      length(distinct([for secret in var.mysterybox_secrets : secret.id])) ==
      length(var.mysterybox_secrets)
    )
    error_message = "mysterybox_secrets[].id values must be unique."
  }

  validation {
    condition = (
      length(distinct([for secret in var.mysterybox_secrets : secret.name])) ==
      length(var.mysterybox_secrets)
    )
    error_message = "mysterybox_secrets[].name values must be unique."
  }

  validation {
    condition = alltrue([
      for secret in var.mysterybox_secrets :
      contains(["platform", "apps"], secret.scope)
    ])
    error_message = "mysterybox_secrets[].scope must be either 'platform' or 'apps'."
  }

  validation {
    condition = alltrue([
      for secret in var.mysterybox_secrets :
      length(secret.payload_keys) > 0
    ])
    error_message = "mysterybox_secrets[].payload_keys must contain at least one key."
  }

  validation {
    condition = alltrue([
      for secret in var.mysterybox_secrets :
      length(distinct(secret.payload_keys)) == length(secret.payload_keys)
    ])
    error_message = "mysterybox_secrets[].payload_keys values must be unique per secret."
  }
}

variable "mysterybox_secret_values" {
  description = "Sensitive MysteryBox payload values by secret ID and payload key."
  type        = map(map(string))
  default     = {}
  nullable    = false
  sensitive   = true
}

variable "wireguard_enabled" {
  description = "Enable WireGuard jump host."
  type        = bool
  default     = false
  nullable    = false
}

variable "wireguard_name" {
  description = "WireGuard instance name."
  type        = string
  default     = ""
  nullable    = false
}

variable "wireguard_platform" {
  description = "WireGuard platform."
  type        = string
  default     = "cpu-d3"
  nullable    = false
}

variable "wireguard_preset" {
  description = "WireGuard preset."
  type        = string
  default     = "4vcpu-16gb"
  nullable    = false
}

variable "wireguard_create_public_ip_allocation" {
  description = "Create public IP allocation for WireGuard."
  type        = bool
  default     = true
  nullable    = false
}

variable "wireguard_public_ip_allocation_id" {
  description = "Existing public IP allocation ID for WireGuard."
  type        = string
  default     = null
  nullable    = true
}

variable "wireguard_public_ip_allocation_name" {
  description = "Public IP allocation name for WireGuard when creating one."
  type        = string
  default     = null
  nullable    = true
}

variable "wireguard_boot_disk_size_gib" {
  description = "WireGuard boot disk size GiB."
  type        = number
  default     = 60
  nullable    = false
}

variable "wireguard_boot_disk_block_size_bytes" {
  description = "WireGuard boot disk block size bytes."
  type        = number
  default     = 4096
  nullable    = false
}

variable "wireguard_boot_disk_type" {
  description = "WireGuard boot disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}

variable "wireguard_source_image_family" {
  description = "WireGuard source image family."
  type        = string
  default     = "ubuntu22.04-driverless"
  nullable    = false
}

variable "wireguard_tunnel_cidr" {
  description = "WireGuard server interface CIDR (for example 10.8.0.1/24)."
  type        = string
  default     = "10.8.0.1/24"
  nullable    = false
  validation {
    condition = (
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$", var.wireguard_tunnel_cidr)) &&
      try(cidrhost(var.wireguard_tunnel_cidr, 0), null) != null
    )
    error_message = "wireguard_tunnel_cidr must be a valid IPv4 interface CIDR (example: 10.8.0.1/24)."
  }
}

variable "wireguard_listen_port" {
  description = "WireGuard UDP listen port."
  type        = number
  default     = 51820
  nullable    = false
  validation {
    condition = (
      floor(var.wireguard_listen_port) == var.wireguard_listen_port &&
      var.wireguard_listen_port >= 1 &&
      var.wireguard_listen_port <= 65535
    )
    error_message = "wireguard_listen_port must be an integer between 1 and 65535."
  }
}

variable "wireguard_nat_mode" {
  description = "Enable NAT masquerade mode for WireGuard point-to-site traffic."
  type        = bool
  default     = true
  nullable    = false
}

variable "wireguard_endpoint_host" {
  description = "Optional endpoint host used in generated WireGuard client configs."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.wireguard_endpoint_host == null ||
      length(trimspace(var.wireguard_endpoint_host)) > 0
    )
    error_message = "wireguard_endpoint_host cannot be empty when provided."
  }
}

variable "wireguard_clients" {
  description = "WireGuard clients to provision automatically on the jump host."
  type = list(object({
    name                 = string
    address              = string
    allowed_ips          = optional(list(string), [])
    dns                  = optional(list(string), ["1.1.1.1"])
    persistent_keepalive = optional(number, 25)
    write_ssh_config     = optional(bool, true)
  }))
  default  = []
  nullable = false
}

variable "ssh_jumphost_enabled" {
  description = "Enable SSH jump host."
  type        = bool
  default     = false
  nullable    = false
  validation {
    condition = (
      !var.ssh_jumphost_enabled ||
      length(var.ssh_jumphost_allowed_cidrs) > 0
    )
    error_message = "ssh_jumphost_allowed_cidrs must contain at least one CIDR when ssh_jumphost_enabled=true."
  }
}

variable "ssh_jumphost_name" {
  description = "SSH jump-host instance name."
  type        = string
  default     = ""
  nullable    = false
}

variable "ssh_jumphost_platform" {
  description = "SSH jump-host platform."
  type        = string
  default     = "cpu-d3"
  nullable    = false
}

variable "ssh_jumphost_preset" {
  description = "SSH jump-host preset."
  type        = string
  default     = "4vcpu-16gb"
  nullable    = false
}

variable "ssh_jumphost_allowed_cidrs" {
  description = "Allowed source CIDRs for inbound SSH on the jump-host VM."
  type        = list(string)
  default     = []
  nullable    = false
  validation {
    condition = alltrue([
      for cidr in var.ssh_jumphost_allowed_cidrs :
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$", cidr))
    ])
    error_message = "ssh_jumphost_allowed_cidrs must contain IPv4 CIDRs."
  }
}

variable "ssh_jumphost_create_public_ip_allocation" {
  description = "Create public IP allocation for SSH jump host."
  type        = bool
  default     = true
  nullable    = false
}

variable "ssh_jumphost_public_ip_allocation_id" {
  description = "Existing public IP allocation ID for SSH jump host."
  type        = string
  default     = null
  nullable    = true
}

variable "ssh_jumphost_public_ip_allocation_name" {
  description = "Public IP allocation name for SSH jump host when creating one."
  type        = string
  default     = null
  nullable    = true
}

variable "ssh_jumphost_boot_disk_size_gib" {
  description = "SSH jump-host boot disk size GiB."
  type        = number
  default     = 60
  nullable    = false
}

variable "ssh_jumphost_boot_disk_block_size_bytes" {
  description = "SSH jump-host boot disk block size bytes."
  type        = number
  default     = 4096
  nullable    = false
}

variable "ssh_jumphost_boot_disk_type" {
  description = "SSH jump-host boot disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
}

variable "ssh_jumphost_source_image_family" {
  description = "SSH jump-host source image family."
  type        = string
  default     = "ubuntu22.04-driverless"
  nullable    = false
}
