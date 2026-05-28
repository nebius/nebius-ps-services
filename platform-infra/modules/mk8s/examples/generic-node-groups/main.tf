module "mk8s" {
  source = "../.."

  cluster = {
    parent_id       = var.parent_id
    cluster_name    = var.cluster_name
    network_id      = var.network_id
    subnet_id       = var.subnet_id
    k8s_version     = var.k8s_version
    public_endpoint = var.public_endpoint
    kube_network = {
      service_cidrs = ["/20"]
    }
  }

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

variable "network_id" {
  type        = string
  description = "Nebius VPC network ID."
}

variable "subnet_id" {
  type        = string
  description = "Nebius subnet ID."
}

variable "k8s_version" {
  type        = string
  description = "Kubernetes version."
  default     = "1.33"
}

variable "public_endpoint" {
  type        = bool
  description = "Enable the public control-plane endpoint."
  default     = true
}

variable "gpu_clusters" {
  type = map(object({
    enabled           = optional(bool, true)
    parent_id         = optional(string)
    name              = optional(string)
    labels            = optional(map(string), {})
    infiniband_fabric = optional(string)
  }))
  description = "Optional named GPU clusters for node groups."
  default     = {}
}

variable "node_groups" {
  description = "Caller-owned MK8s node groups keyed by logical name."
  type = map(object({
    enabled          = optional(bool, true)
    name             = optional(string)
    node_count       = optional(number)
    autoscaling      = optional(any)
    auto_repair      = optional(any)
    gpu              = optional(bool, false)
    platform         = optional(string)
    preset           = optional(string)
    os               = optional(string)
    node_labels      = optional(map(string), {})
    labels           = optional(map(string), {})
    taints           = optional(list(object({ key = string, value = string, effect = string })), [])
    boot_disk        = optional(any)
    preemptible      = optional(bool, false)
    public_ips       = optional(bool, false)
    gpu_cluster_key  = optional(string)
    gpu_cluster_id   = optional(string)
    gpu_stack_source = optional(string, "nebius_image")
    gpu_stack_preset = optional(string)
    reservation = optional(object({
      policy          = optional(string)
      reservation_ids = optional(list(string), [])
    }))
    service_account = optional(object({
      id          = optional(string)
      name        = optional(string)
      description = optional(string)
      labels      = optional(map(string), {})
    }))
    ssh = optional(object({
      username    = string
      public_keys = list(string)
    }))
    filesystems         = optional(any, [])
    sfs_filesystem_keys = optional(list(string), [])
  }))
  default = {
    system = {
      node_count = 2
      gpu        = false
      platform   = "cpu-d3"
      preset     = "4vcpu-16gb"
      os         = "ubuntu24.04"
      node_labels = {
        "example.nebius.ai/role" = "system"
      }
    }
    scheduler = {
      node_count = 1
      gpu        = false
      platform   = "cpu-d3"
      preset     = "4vcpu-16gb"
      os         = "ubuntu24.04"
      node_labels = {
        "example.nebius.ai/role" = "scheduler"
      }
      taints = [{
        key    = "example.nebius.ai/workload"
        value  = "scheduler"
        effect = "NO_SCHEDULE"
      }]
    }
    worker = {
      node_count       = 1
      gpu              = true
      platform         = "gpu-b200-sxm"
      preset           = "8gpu-180gb"
      os               = "ubuntu24.04"
      gpu_stack_source = "nebius_image"
      gpu_stack_preset = "cuda13.0"
      node_labels = {
        "example.nebius.ai/role" = "worker"
        "nebius.com/gpu"         = "true"
      }
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
