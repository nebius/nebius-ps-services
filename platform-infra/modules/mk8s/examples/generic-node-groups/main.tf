module "mk8s" {
  source = "../.."

  parent_id    = var.parent_id
  cluster_name = var.cluster_name
  subnet_id    = var.subnet_id

  cpu_nodes_count    = 0
  cpu_nodes_platform = var.cpu_nodes_platform
  cpu_nodes_preset   = var.cpu_nodes_preset
  cpu_nodes_os       = var.cpu_nodes_os

  gpu_enabled               = true
  gpu_node_groups           = 0
  gpu_nodes_count_per_group = 0
  gpu_nodes_platform        = var.gpu_nodes_platform
  gpu_nodes_preset          = var.gpu_nodes_preset
  gpu_nodes_os              = var.gpu_nodes_os
  gpu_stack_source          = "nebius_image"
  gpu_stack_preset          = var.gpu_stack_preset

  node_groups  = var.node_groups
  gpu_clusters = var.gpu_clusters
}

variable "parent_id" {
  type        = string
  description = "Nebius project ID."
}

variable "cluster_name" {
  type        = string
  description = "MK8s cluster name."
  default     = "example-generic-node-groups"
}

variable "subnet_id" {
  type        = string
  description = "Nebius subnet ID."
}

variable "cpu_nodes_platform" {
  type        = string
  description = "Default CPU node platform inherited by non-GPU generic node groups."
  default     = "cpu-d3"
}

variable "cpu_nodes_preset" {
  type        = string
  description = "Default CPU node preset inherited by non-GPU generic node groups."
  default     = "4vcpu-16gb"
}

variable "cpu_nodes_os" {
  type        = string
  description = "Default CPU node OS inherited by non-GPU generic node groups."
  default     = "ubuntu24.04"
}

variable "gpu_nodes_platform" {
  type        = string
  description = "Default GPU node platform inherited by GPU generic node groups."
  default     = "gpu-b200-sxm"
}

variable "gpu_nodes_preset" {
  type        = string
  description = "Default GPU node preset inherited by GPU generic node groups."
  default     = "8gpu-180gb"
}

variable "gpu_nodes_os" {
  type        = string
  description = "Default GPU node OS inherited by GPU generic node groups."
  default     = "ubuntu24.04"
}

variable "gpu_stack_preset" {
  type        = string
  description = "Nebius GPU image stack preset for GPU generic node groups."
  default     = "cuda13.0"
}

variable "gpu_clusters" {
  type        = any
  description = "Optional named GPU clusters for generic node groups."
  default     = {}
}

variable "node_groups" {
  type        = any
  description = "Caller-owned MK8s node groups keyed by any logical name."
  default = {
    core = {
      fixed_node_count = 2
      workload         = "platform"
      jail             = true
    }
    scheduler = {
      fixed_node_count = 1
      workload         = "scheduler"
      jail             = true
      taints = [{
        key    = "example.nebius.ai/workload"
        value  = "scheduler"
        effect = "NO_SCHEDULE"
      }]
    }
    "gpu-workers-a" = {
      fixed_node_count = 1
      workload         = "worker"
      nodeset_name     = "gpu-workers"
      gpu              = true
      jail             = true
      taints = [{
        key    = "nvidia.com/gpu"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }
}

output "cluster_id" {
  description = "MK8s cluster ID from the example root."
  value       = module.mk8s.cluster_id
}

output "node_group_ids" {
  description = "All node group IDs keyed by logical node group name."
  value       = module.mk8s.node_group_ids
}
