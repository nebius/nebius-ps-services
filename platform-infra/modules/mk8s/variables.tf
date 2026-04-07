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

variable "gpu_drivers_preset" {
  description = "Explicit drivers_preset for GPU node groups. If null, gpu_driver_preset_map lookup and gpu_default_drivers_preset are used."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.gpu_drivers_preset == null ||
      length(trimspace(var.gpu_drivers_preset)) > 0
    )
    error_message = "gpu_drivers_preset cannot be empty when provided."
  }
}

variable "gpu_default_drivers_preset" {
  description = "Fallback drivers_preset used when gpu_drivers_preset is null and gpu_nodes_platform is not found in gpu_driver_preset_map."
  type        = string
  default     = "cuda13.0"
  nullable    = false
  validation {
    condition     = length(trimspace(var.gpu_default_drivers_preset)) > 0
    error_message = "gpu_default_drivers_preset cannot be empty."
  }
}

variable "gpu_driver_preset_map" {
  description = "Fallback mapping from gpu_nodes_platform to drivers_preset when gpu_drivers_preset is null."
  type        = map(string)
  default = {
    "gpu-b200-sxm"   = "cuda12.8"
    "gpu-b200-sxm-a" = "cuda12.8"
  }
  nullable = false
  validation {
    condition = alltrue([
      for preset in values(var.gpu_driver_preset_map) : length(trimspace(preset)) > 0
    ])
    error_message = "gpu_driver_preset_map values must be non-empty strings."
  }
}

variable "infiniband_fabric" {
  description = "GPU fabric name for optional GPU cluster creation."
  type        = string
  default     = ""
  nullable    = false
}

variable "mig_strategy" {
  description = "MIG strategy hint for upper layers (currently passthrough metadata only)."
  type        = string
  default     = null
  nullable    = true
}

variable "mig_parted_config" {
  description = "MIG partition profile mapped to node label nvidia.com/mig.config."
  type        = string
  default     = null
  nullable    = true
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
