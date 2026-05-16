variable "parent_id" {
  description = "Nebius project ID where VM resources are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "subnet_id" {
  description = "Subnet ID where the VM network interface is attached."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.subnet_id)) > 0
    error_message = "subnet_id cannot be empty."
  }
}

variable "name" {
  description = "VM name."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.name))
    error_message = "name must use lowercase letters, digits, and hyphens, and must not start or end with a hyphen."
  }
}

variable "platform" {
  description = "Nebius compute platform ID, for example cpu-d3, gpu-h200-sxm, gpu-rtx6000, or gpu-b200-sxm."
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
  description = "SSH username created on the VM."
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

variable "cloud_init_user_data_override" {
  description = "Optional complete cloud-init user data override. When set, the module still owns VM/disk/network resources, but uses this rendered cloud-init payload instead of the built-in generic VM bootstrap template."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.cloud_init_user_data_override == null ||
      length(trimspace(var.cloud_init_user_data_override)) > 0
    )
    error_message = "cloud_init_user_data_override must be null or non-empty cloud-init user data."
  }
}

variable "source_image_family" {
  description = "Boot image family used when the module creates the boot disk. Set this unless you provide source_image_id or boot_disk_existing_id."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.source_image_family == null ||
      length(trimspace(var.source_image_family)) > 0
    )
    error_message = "source_image_family must be null or a non-empty image family."
  }
}

variable "source_image_id" {
  description = "Specific boot image ID used instead of source_image_family when set."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.source_image_id == null ||
      length(trimspace(var.source_image_id)) > 0
    )
    error_message = "source_image_id must be null or a non-empty image ID."
  }
}

variable "boot_disk_existing_id" {
  description = "Existing disk ID to use as the boot disk instead of creating one."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.boot_disk_existing_id == null ||
      length(trimspace(var.boot_disk_existing_id)) > 0
    )
    error_message = "boot_disk_existing_id must be null or a non-empty disk ID."
  }
}

variable "boot_disk_size_gib" {
  description = "Boot disk size in GiB when the module creates the boot disk."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.boot_disk_size_gib == null ||
      (
        var.boot_disk_size_gib >= 20 &&
        floor(var.boot_disk_size_gib) == var.boot_disk_size_gib
      )
    )
    error_message = "boot_disk_size_gib must be null or an integer >= 20."
  }
}

variable "boot_disk_block_size_bytes" {
  description = "Boot disk block size in bytes when the module creates the boot disk."
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
  description = "Boot disk type when the module creates the boot disk."
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
  description = "Enable deletion protection on the module-managed boot disk."
  type        = bool
  default     = false
  nullable    = false
}

variable "boot_disk_device_id" {
  description = "Optional custom device ID for the boot disk."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.boot_disk_device_id == null ||
      length(trimspace(var.boot_disk_device_id)) > 0
    )
    error_message = "boot_disk_device_id must be null or a non-empty string."
  }
}

variable "public_ip_mode" {
  description = "Public IPv4 behavior for the primary network interface: none, dynamic, static, or allocation."
  type        = string
  default     = "dynamic"
  nullable    = false
  validation {
    condition = contains(
      ["none", "dynamic", "static", "allocation"],
      lower(var.public_ip_mode)
    )
    error_message = "public_ip_mode must be one of: none, dynamic, static, allocation."
  }
}

variable "public_ip_allocation_id" {
  description = "Existing public IP allocation ID used when public_ip_mode=allocation."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.public_ip_allocation_id == null ||
      length(trimspace(var.public_ip_allocation_id)) > 0
    )
    error_message = "public_ip_allocation_id must be null or a non-empty allocation ID."
  }
}

variable "private_ip_allocation_id" {
  description = "Existing private IP allocation ID for the primary network interface."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.private_ip_allocation_id == null ||
      length(trimspace(var.private_ip_allocation_id)) > 0
    )
    error_message = "private_ip_allocation_id must be null or a non-empty allocation ID."
  }
}

variable "security_group_ids" {
  description = "Security group IDs attached to the primary network interface."
  type        = list(string)
  default     = []
  nullable    = false
  validation {
    condition = alltrue([
      for group_id in var.security_group_ids : length(trimspace(group_id)) > 0
    ])
    error_message = "security_group_ids must contain non-empty security group IDs."
  }
}

variable "hostname" {
  description = "Optional hostname for the VM."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.hostname == null ||
      length(trimspace(var.hostname)) > 0
    )
    error_message = "hostname must be null or a non-empty string."
  }
}

variable "service_account_id" {
  description = "Optional existing service account ID attached to the VM."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.service_account_id == null ||
      length(trimspace(var.service_account_id)) > 0
    )
    error_message = "service_account_id must be null or a non-empty service account ID."
  }
}

variable "stopped" {
  description = "Whether the VM should be created in a stopped state."
  type        = bool
  default     = false
  nullable    = false
}

variable "labels" {
  description = "Labels applied to created resources."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "data_disks" {
  description = "Managed data disks to create and attach to the VM."
  type = list(object({
    name                = string
    size_gib            = number
    type                = optional(string, "NETWORK_SSD")
    block_size_bytes    = optional(number, 4096)
    encryption_enabled  = optional(bool, false)
    deletion_protection = optional(bool, false)
    attach_mode         = optional(string, "READ_WRITE")
    device_id           = optional(string)
    labels              = optional(map(string), {})
  }))
  default  = []
  nullable = false
  validation {
    condition = length(var.data_disks) == length(toset([
      for disk in var.data_disks : disk.name
    ]))
    error_message = "data_disks names must be unique."
  }
  validation {
    condition = alltrue([
      for disk in var.data_disks : (
        can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", disk.name)) &&
        disk.size_gib >= 1 &&
        floor(disk.size_gib) == disk.size_gib &&
        contains(
          ["NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
          upper(try(disk.type, "NETWORK_SSD"))
        ) &&
        (
          !try(disk.encryption_enabled, false) ||
          contains(
            ["NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"],
            upper(try(disk.type, "NETWORK_SSD"))
          )
        ) &&
        contains(["READ_ONLY", "READ_WRITE"], upper(try(disk.attach_mode, "READ_WRITE"))) &&
        try(disk.block_size_bytes, 4096) >= 4096 &&
        try(disk.block_size_bytes, 4096) <= 131072 &&
        floor(try(disk.block_size_bytes, 4096)) == try(disk.block_size_bytes, 4096) &&
        floor(log(try(disk.block_size_bytes, 4096), 2)) == ceil(log(try(disk.block_size_bytes, 4096), 2))
      )
    ])
    error_message = "Each data_disks entry must have a valid name, integer size_gib >= 1, supported disk type, supported attach_mode, a power-of-two block_size_bytes between 4096 and 131072, and encryption_enabled only on NETWORK_SSD_NON_REPLICATED or NETWORK_SSD_IO_M3."
  }
}

variable "existing_data_disks" {
  description = "Existing data disk IDs to attach to the VM."
  type = list(object({
    id          = string
    attach_mode = optional(string, "READ_WRITE")
    device_id   = optional(string)
  }))
  default  = []
  nullable = false
  validation {
    condition = alltrue([
      for disk in var.existing_data_disks : (
        length(trimspace(disk.id)) > 0 &&
        contains(["READ_ONLY", "READ_WRITE"], upper(try(disk.attach_mode, "READ_WRITE")))
      )
    ])
    error_message = "Each existing_data_disks entry must include a non-empty id and supported attach_mode."
  }
}

variable "filesystems" {
  description = "Existing shared filesystems to attach to the VM."
  type = list(object({
    id          = string
    mount_tag   = string
    attach_mode = optional(string, "READ_WRITE")
  }))
  default  = []
  nullable = false
  validation {
    condition = alltrue([
      for filesystem in var.filesystems : (
        length(trimspace(filesystem.id)) > 0 &&
        length(trimspace(filesystem.mount_tag)) > 0 &&
        contains(["READ_ONLY", "READ_WRITE"], upper(try(filesystem.attach_mode, "READ_WRITE")))
      )
    ])
    error_message = "Each filesystems entry must include non-empty id/mount_tag values and a supported attach_mode."
  }
}

variable "recovery_policy" {
  description = "Instance recovery policy. Preemptible VMs must use FAIL."
  type        = string
  default     = "RECOVER"
  nullable    = false
  validation {
    condition     = contains(["RECOVER", "FAIL"], upper(var.recovery_policy))
    error_message = "recovery_policy must be RECOVER or FAIL."
  }
}

variable "preemptible_enabled" {
  description = "Create a preemptible VM. Nebius only supports this on GPU platforms."
  type        = bool
  default     = false
  nullable    = false
}

variable "preemptible_priority" {
  description = "Preemptible VM priority from 1 to 5."
  type        = number
  default     = 3
  nullable    = false
  validation {
    condition = (
      floor(var.preemptible_priority) == var.preemptible_priority &&
      var.preemptible_priority >= 1 &&
      var.preemptible_priority <= 5
    )
    error_message = "preemptible_priority must be an integer from 1 to 5."
  }
}

variable "gpu_cluster_enabled" {
  description = "Attach the VM to a GPU cluster."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_cluster_id" {
  description = "Existing GPU cluster ID to attach to the VM."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_cluster_id == null ||
      length(trimspace(var.gpu_cluster_id)) > 0
    )
    error_message = "gpu_cluster_id must be null or a non-empty GPU cluster ID."
  }
}

variable "gpu_cluster_infiniband_fabric" {
  description = "InfiniBand fabric identifier used to create a new GPU cluster."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_cluster_infiniband_fabric == null ||
      length(trimspace(var.gpu_cluster_infiniband_fabric)) > 0
    )
    error_message = "gpu_cluster_infiniband_fabric must be null or a non-empty fabric ID."
  }
}

variable "gpu_cluster_name" {
  description = "Optional name for a created GPU cluster."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_cluster_name == null ||
      can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.gpu_cluster_name))
    )
    error_message = "gpu_cluster_name must be null or use lowercase letters, digits, and hyphens."
  }
}

variable "container_enabled" {
  description = "Bootstrap Docker on the VM and run one container workload via cloud-init/systemd."
  type        = bool
  default     = false
  nullable    = false
}

variable "container_image" {
  description = "Container image to run when container_enabled=true."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.container_image == null ||
      length(trimspace(var.container_image)) > 0
    )
    error_message = "container_image must be null or a non-empty image reference."
  }
}

variable "container_entrypoint" {
  description = "Optional container entrypoint used by docker run --entrypoint."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.container_entrypoint == null ||
      length(trimspace(var.container_entrypoint)) > 0
    )
    error_message = "container_entrypoint must be null or a non-empty string."
  }
}

variable "container_args" {
  description = "Additional container command arguments appended after the image name."
  type        = list(string)
  default     = []
  nullable    = false
}

variable "container_env" {
  description = "Environment variables passed to the managed container."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "container_ports" {
  description = "Published container ports in host_port:container_port form."
  type = list(object({
    host_port      = number
    container_port = number
    protocol       = optional(string, "tcp")
  }))
  default  = []
  nullable = false
  validation {
    condition = alltrue([
      for port in var.container_ports : (
        floor(port.host_port) == port.host_port &&
        floor(port.container_port) == port.container_port &&
        port.host_port >= 1 &&
        port.host_port <= 65535 &&
        port.container_port >= 1 &&
        port.container_port <= 65535 &&
        contains(["tcp", "udp"], lower(try(port.protocol, "tcp")))
      )
    ])
    error_message = "container_ports must use integer ports from 1 to 65535 and protocol tcp or udp."
  }
}

variable "container_mounts" {
  description = "Host-path mounts passed to docker run."
  type = list(object({
    host_path      = string
    container_path = string
    read_only      = optional(bool, false)
  }))
  default  = []
  nullable = false
  validation {
    condition = alltrue([
      for mount in var.container_mounts : (
        startswith(mount.host_path, "/") &&
        startswith(mount.container_path, "/")
      )
    ])
    error_message = "container_mounts host_path and container_path must be absolute paths."
  }
}

variable "container_use_gpu" {
  description = "Configure the managed container to run with --gpus all."
  type        = bool
  default     = false
  nullable    = false
}

variable "container_restart_policy" {
  description = "Docker restart policy for the managed container."
  type        = string
  default     = "unless-stopped"
  nullable    = false
  validation {
    condition = contains(
      ["no", "on-failure", "always", "unless-stopped"],
      lower(var.container_restart_policy)
    )
    error_message = "container_restart_policy must be one of: no, on-failure, always, unless-stopped."
  }
}
