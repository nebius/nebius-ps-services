variable "parent_id" {
  description = "Nebius project ID where the WireGuard VPN gateway VM and related resources are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "network_id" {
  description = "VPC network ID that owns subnet_id."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.network_id)) > 0
    error_message = "network_id cannot be empty."
  }
}

variable "subnet_id" {
  description = "Subnet ID where the WireGuard VPN gateway VM network interface is attached."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.subnet_id)) > 0
    error_message = "subnet_id cannot be empty."
  }
}

variable "name" {
  description = "WireGuard VPN gateway VM name."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.name))
    error_message = "name must use lowercase letters, digits, and hyphens, and must not start/end with hyphen."
  }
}

variable "platform" {
  description = "Nebius compute platform ID for the WireGuard VPN gateway VM, for example cpu-d3."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.platform)) > 0
    error_message = "platform cannot be empty."
  }
}

variable "preset" {
  description = "Nebius compute preset name for the selected platform."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.preset)) > 0
    error_message = "preset cannot be empty."
  }
}

variable "ssh_user_name" {
  description = "SSH username created on the WireGuard VPN gateway VM."
  type        = string
  default     = "ubuntu"
  nullable    = false
  validation {
    condition = (
      length(trimspace(var.ssh_user_name)) > 0 &&
      can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user_name)) &&
      !contains(["root", "admin"], lower(var.ssh_user_name))
    )
    error_message = "ssh_user_name must match Linux username format and must not be root or admin."
  }
}

variable "ssh_public_key" {
  description = "Inline SSH public key content for initial VM access."
  type        = string
  nullable    = false
  validation {
    condition = can(regex(
      "^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(256|384|521))[[:space:]]+[^[:space:]]+([[:space:]]+.*)?$",
      trimspace(var.ssh_public_key),
    ))
    error_message = "ssh_public_key must be an inline OpenSSH public key string using ssh-rsa, ssh-ed25519, or ECDSA."
  }
}

variable "create_public_ip_allocation" {
  description = "Create a dedicated static public IP allocation and attach it to the VM."
  type        = bool
  default     = true
  nullable    = false
}

variable "public_ip_allocation_id" {
  description = "Use an existing public IP allocation ID instead of creating a new one."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = !(
      var.public_ip_allocation_id != null &&
      var.create_public_ip_allocation
    )
    error_message = "Set create_public_ip_allocation=false when public_ip_allocation_id is provided."
  }
  validation {
    condition = (
      (var.public_ip_allocation_id == null && var.create_public_ip_allocation) ||
      (
        var.public_ip_allocation_id != null &&
        length(trimspace(var.public_ip_allocation_id)) > 0
      )
    )
    error_message = "Set create_public_ip_allocation=true, or provide a non-empty public_ip_allocation_id."
  }
}

variable "public_ip_allocation_name" {
  description = "Name for created public IP allocation. Ignored when using existing allocation_id."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.public_ip_allocation_name == null ||
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.public_ip_allocation_name))
    )
    error_message = "public_ip_allocation_name must use lowercase letters, digits, and hyphens."
  }
}

variable "boot_disk_size_gib" {
  description = "Boot disk size in GiB."
  type        = number
  nullable    = false
  validation {
    condition = (
      var.boot_disk_size_gib >= 20 &&
      floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
    )
    error_message = "boot_disk_size_gib must be an integer >= 20."
  }
}

variable "boot_disk_block_size_bytes" {
  description = "Boot disk block size in bytes."
  type        = number
  default     = 4096
  nullable    = false
  validation {
    condition = (
      var.boot_disk_block_size_bytes >= 4096 &&
      var.boot_disk_block_size_bytes <= 131072 &&
      floor(var.boot_disk_block_size_bytes) == var.boot_disk_block_size_bytes &&
      floor(log(var.boot_disk_block_size_bytes, 2)) == ceil(log(var.boot_disk_block_size_bytes, 2))
    )
    error_message = "boot_disk_block_size_bytes must be a power of two between 4096 and 131072."
  }
}

variable "boot_disk_type" {
  description = "Boot disk type."
  type        = string
  default     = "NETWORK_SSD"
  nullable    = false
  validation {
    condition = contains(
      ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
      var.boot_disk_type
    )
    error_message = "boot_disk_type must be one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "boot_disk_encryption_enabled" {
  description = "Enable provider-managed data encryption on the boot disk. Nebius supports explicit disk_encryption only for NETWORK_SSD_NON_REPLICATED and NETWORK_SSD_IO_M3; NETWORK_SSD is always encrypted by the platform."
  type        = bool
  default     = false
  nullable    = false
}

variable "boot_disk_deletion_protection" {
  description = "Enable deletion protection on the WireGuard VPN gateway boot disk."
  type        = bool
  default     = false
  nullable    = false
}

variable "source_image_family" {
  description = "Image family used for the WireGuard VPN gateway VM boot disk."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.source_image_family)) > 0
    error_message = "source_image_family cannot be empty."
  }
}

variable "wireguard_tunnel_cidr" {
  description = "WireGuard server interface CIDR (for example 10.8.0.1/22)."
  type        = string
  default     = "10.8.0.1/22"
  nullable    = false
  validation {
    condition = (
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$", var.wireguard_tunnel_cidr)) &&
      try(cidrhost(var.wireguard_tunnel_cidr, 0), null) != null
    )
    error_message = "wireguard_tunnel_cidr must be a valid IPv4 interface CIDR (example: 10.8.0.1/22)."
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

variable "nat_mode" {
  description = "Enable source NAT/MASQUERADE for WireGuard client traffic egressing from the VPN gateway into the Nebius VPC."
  type        = bool
  default     = true
  nullable    = false
}

variable "endpoint_host" {
  description = "WireGuard endpoint host used in generated client configs. If null, auto-detect public IP."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.endpoint_host == null ||
      length(trimspace(var.endpoint_host)) > 0
    )
    error_message = "endpoint_host cannot be empty when provided."
  }
}

variable "local_subnets" {
  description = "Default private destination CIDRs routed by generated WireGuard client configs when a client does not set per-client local_subnets."
  type        = list(string)
  nullable    = false
  validation {
    condition = alltrue([
      for cidr in var.local_subnets : can(cidrnetmask(cidr))
    ])
    error_message = "local_subnets must contain valid IPv4 CIDRs."
  }
}

variable "client_default_dns" {
  description = "Default DNS server IPv4 addresses written to generated WireGuard client configs when a client does not set dns."
  type        = list(string)
  default     = ["1.1.1.1", "1.0.0.1"]
  nullable    = false
  validation {
    condition = alltrue([
      for dns in var.client_default_dns : try(cidrhost("${dns}/32", 0), null) == dns
    ])
    error_message = "client_default_dns entries must be IPv4 addresses."
  }
}

variable "client_default_persistent_keepalive" {
  description = "Default WireGuard PersistentKeepalive interval, in seconds, for generated clients."
  type        = number
  default     = 25
  nullable    = false
  validation {
    condition = (
      floor(var.client_default_persistent_keepalive) == var.client_default_persistent_keepalive &&
      var.client_default_persistent_keepalive >= 0 &&
      var.client_default_persistent_keepalive <= 65535
    )
    error_message = "client_default_persistent_keepalive must be an integer between 0 and 65535."
  }
}

variable "clients" {
  description = "Initial WireGuard clients to generate during first boot. Day-2 clients should be created with the gateway-local generator."
  type = list(object({
    name                     = string
    client_wg_tunnel_address = optional(string)
    local_subnets            = optional(list(string), [])
    dns                      = optional(list(string), [])
    persistent_keepalive     = optional(number, 25)
  }))
  default  = []
  nullable = false

  validation {
    condition = length(distinct([
      for c in var.clients : c.name
    ])) == length(var.clients)
    error_message = "wireguard clients must have unique names."
  }

  validation {
    condition = alltrue([
      for c in var.clients :
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", c.name))
    ])
    error_message = "each wireguard client name must use lowercase letters, digits, and hyphens."
  }

  validation {
    condition = alltrue([
      for c in var.clients :
      c.client_wg_tunnel_address == null || (
        can(cidrnetmask(c.client_wg_tunnel_address)) &&
        try(tonumber(split("/", c.client_wg_tunnel_address)[1]), null) == 32
      )
    ])
    error_message = "each wireguard client client_wg_tunnel_address must be null or a valid IPv4 /32 CIDR (for example 10.8.0.2/32)."
  }

  validation {
    condition = alltrue([
      for c in var.clients :
      alltrue([
        for cidr in c.local_subnets : can(cidrnetmask(cidr))
      ])
    ])
    error_message = "each wireguard client local_subnets entry must be a valid IPv4 CIDR."
  }

  validation {
    condition = alltrue([
      for c in var.clients :
      alltrue([
        for dns in c.dns : try(cidrhost("${dns}/32", 0), null) == dns
      ])
    ])
    error_message = "each wireguard client dns entry must be an IPv4 address."
  }

  validation {
    condition = alltrue([
      for c in var.clients :
      c.persistent_keepalive >= 0 && c.persistent_keepalive <= 65535
    ])
    error_message = "each wireguard client persistent_keepalive must be between 0 and 65535."
  }
}

variable "labels" {
  description = "Additional labels applied to created resources. The module also applies component and name labels."
  type        = map(string)
  default     = {}
  nullable    = false
}
