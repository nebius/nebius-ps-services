variable "parent_id" {
  description = "Nebius project ID where MK8s resources are created."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.parent_id)) > 0
    error_message = "parent_id cannot be empty."
  }
}

variable "cluster_name" {
  description = "MK8s cluster name."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.cluster_name)) > 0
    error_message = "cluster_name cannot be empty."
  }
}

variable "subnet_id" {
  description = "VPC subnet ID for MK8s control plane and default node interfaces."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.subnet_id)) > 0
    error_message = "subnet_id cannot be empty."
  }
}

variable "k8s_version" {
  description = "Kubernetes version <major>.<minor> (for example 1.31)."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.k8s_version == null ||
      length(trimspace(var.k8s_version)) > 0
    )
    error_message = "k8s_version cannot be an empty string when provided."
  }
}

variable "etcd_cluster_size" {
  description = "etcd control plane size."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.etcd_cluster_size == null ||
      (
        floor(var.etcd_cluster_size) == var.etcd_cluster_size &&
        var.etcd_cluster_size >= 1
      )
    )
    error_message = "etcd_cluster_size must be an integer >= 1 when provided."
  }
}

variable "mk8s_cluster_public_endpoint" {
  description = "Enable public endpoint for MK8s control plane."
  type        = bool
  default     = false
  nullable    = false
}

variable "kube_network_service_cidrs" {
  description = "CIDR blocks for Kubernetes Service ClusterIP allocation. Defaults to a smaller /20 allocation to avoid Nebius' broad /16 implicit default on single-pool subnets."
  type        = list(string)
  default     = ["/20"]
  nullable    = false
  validation {
    condition = (
      length(var.kube_network_service_cidrs) == 1 &&
      alltrue([
        for cidr in var.kube_network_service_cidrs : length(trimspace(cidr)) > 0
      ])
    )
    error_message = "kube_network_service_cidrs must contain exactly one non-empty CIDR or prefix-length string."
  }
}

variable "cpu_nodes_count" {
  description = "Fixed node count for CPU node group. Set explicitly in callers that want the baseline CPU node pool."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.cpu_nodes_count == null ||
      (
        floor(var.cpu_nodes_count) == var.cpu_nodes_count &&
        var.cpu_nodes_count >= 0
      )
    )
    error_message = "cpu_nodes_count must be null or an integer >= 0."
  }
}

variable "cpu_nodes_platform" {
  description = "Default CPU node group resources.platform. Required when CPU node group creation is enabled unless override template.resources sets it."
  type        = string
  default     = ""
  nullable    = false
}

variable "cpu_nodes_preset" {
  description = "Default CPU node group resources.preset. Required when CPU node group creation is enabled unless override template.resources sets it."
  type        = string
  default     = ""
  nullable    = false
}

variable "cpu_nodes_os" {
  description = "Default CPU node group template.os. When null, provider defaults or override template.os apply."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.cpu_nodes_os == null ||
      length(trimspace(var.cpu_nodes_os)) > 0
    )
    error_message = "cpu_nodes_os cannot be empty when provided."
  }
}

variable "cpu_nodes_boot_disk_size_gib" {
  description = "Default CPU node group template.boot_disk.size_gibibytes. When null, provider defaults or override template.boot_disk.size_* apply."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.cpu_nodes_boot_disk_size_gib == null ||
      (
        floor(var.cpu_nodes_boot_disk_size_gib) == var.cpu_nodes_boot_disk_size_gib &&
        var.cpu_nodes_boot_disk_size_gib >= 1
      )
    )
    error_message = "cpu_nodes_boot_disk_size_gib must be null or an integer >= 1."
  }
}

variable "cpu_nodes_boot_disk_type" {
  description = "Default CPU node group template.boot_disk.type. When null, provider defaults or override template.boot_disk.type apply."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.cpu_nodes_boot_disk_type == null ||
      contains(
        [
          "NETWORK_SSD",
          "NETWORK_HDD",
          "NETWORK_SSD_NON_REPLICATED",
          "NETWORK_SSD_IO_M3",
        ],
        trimspace(var.cpu_nodes_boot_disk_type)
      )
    )
    error_message = "cpu_nodes_boot_disk_type must be null or one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "cpu_nodes_preemptible" {
  description = "Use preemptible CPU nodes."
  type        = bool
  default     = false
  nullable    = false
}

variable "cpu_nodes_public_ips" {
  description = "Attach public IPs to CPU nodes."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_enabled" {
  description = "Enable GPU node groups."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_node_groups" {
  description = "Number of GPU node groups."
  type        = number
  default     = 0
  nullable    = false
  validation {
    condition = (
      floor(var.gpu_node_groups) == var.gpu_node_groups &&
      var.gpu_node_groups >= 0
    )
    error_message = "gpu_node_groups must be an integer >= 0."
  }
}

variable "gpu_nodes_count_per_group" {
  description = "Fixed nodes per GPU node group."
  type        = number
  default     = 0
  nullable    = false
  validation {
    condition = (
      floor(var.gpu_nodes_count_per_group) == var.gpu_nodes_count_per_group &&
      var.gpu_nodes_count_per_group >= 0
    )
    error_message = "gpu_nodes_count_per_group must be an integer >= 0."
  }
}

variable "gpu_nodes_platform" {
  description = "Default GPU node group resources.platform. Required when gpu_enabled=true unless override template.resources sets it."
  type        = string
  default     = ""
  nullable    = false
}

variable "gpu_nodes_preset" {
  description = "Default GPU node group resources.preset. Required when gpu_enabled=true unless override template.resources sets it."
  type        = string
  default     = ""
  nullable    = false
}

variable "gpu_nodes_os" {
  description = "Default GPU node group template.os. When null, provider defaults or override template.os apply."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_nodes_os == null ||
      length(trimspace(var.gpu_nodes_os)) > 0
    )
    error_message = "gpu_nodes_os cannot be empty when provided."
  }
}

variable "gpu_nodes_boot_disk_size_gib" {
  description = "Default GPU node group template.boot_disk.size_gibibytes. When null, provider defaults or override template.boot_disk.size_* apply."
  type        = number
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_nodes_boot_disk_size_gib == null ||
      (
        floor(var.gpu_nodes_boot_disk_size_gib) == var.gpu_nodes_boot_disk_size_gib &&
        var.gpu_nodes_boot_disk_size_gib >= 1
      )
    )
    error_message = "gpu_nodes_boot_disk_size_gib must be null or an integer >= 1."
  }
}

variable "gpu_nodes_boot_disk_type" {
  description = "Default GPU node group template.boot_disk.type. When null, provider defaults or override template.boot_disk.type apply."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_nodes_boot_disk_type == null ||
      contains(
        [
          "NETWORK_SSD",
          "NETWORK_HDD",
          "NETWORK_SSD_NON_REPLICATED",
          "NETWORK_SSD_IO_M3",
        ],
        trimspace(var.gpu_nodes_boot_disk_type)
      )
    )
    error_message = "gpu_nodes_boot_disk_type must be null or one of NETWORK_SSD, NETWORK_HDD, NETWORK_SSD_NON_REPLICATED, NETWORK_SSD_IO_M3."
  }
}

variable "gpu_nodes_preemptible" {
  description = "Use preemptible GPU nodes."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_nodes_public_ips" {
  description = "Attach public IPs to GPU nodes."
  type        = bool
  default     = false
  nullable    = false
}

variable "gpu_stack_preset" {
  description = "Nebius GPU software-stack preset for GPU node groups when gpu_stack_source=nebius_image. This maps to the Nebius API field template.gpu_settings.drivers_preset."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_stack_preset == null ||
      length(trimspace(var.gpu_stack_preset)) > 0
    )
    error_message = "gpu_stack_preset cannot be empty when provided."
  }
}

variable "gpu_stack_source" {
  description = "How the GPU software stack reaches MK8s nodes: nebius_image renders template.gpu_settings.drivers_preset so Managed Kubernetes preinstalls the Nebius image stack; operator_managed omits gpu_settings so operators or other tooling can manage drivers, toolkit, and kernels."
  type        = string
  default     = "nebius_image"
  nullable    = false
  validation {
    condition     = contains(["nebius_image", "operator_managed"], trimspace(var.gpu_stack_source))
    error_message = "gpu_stack_source must be 'nebius_image' or 'operator_managed'."
  }
}

variable "infiniband_fabric" {
  description = "GPU fabric name for optional GPU cluster creation."
  type        = string
  default     = ""
  nullable    = false
}

variable "mk8s_cluster_overrides" {
  description = "Optional provider-aligned cluster override object."
  type        = map(any)
  default     = {}
  nullable    = false
}

variable "mk8s_cpu_node_group_overrides" {
  description = "Optional provider-aligned CPU node group override object."
  type        = map(any)
  default     = {}
  nullable    = false
}

variable "mk8s_gpu_node_group_overrides" {
  description = "Optional provider-aligned GPU node group override object."
  type        = map(any)
  default     = {}
  nullable    = false
}
