variable "parent_id" {
  description = "Nebius project ID where MK8s resources are created."
  type        = string
  nullable    = false
}

variable "cluster_name" {
  description = "MK8s cluster name."
  type        = string
  nullable    = false
}

variable "subnet_id" {
  description = "VPC subnet ID for MK8s control plane and default node interfaces."
  type        = string
  nullable    = false
}

variable "k8s_version" {
  description = "Kubernetes version <major>.<minor> (for example 1.31)."
  type        = string
  default     = null
  nullable    = true
}

variable "etcd_cluster_size" {
  description = "etcd control plane size."
  type        = number
  default     = null
  nullable    = true
}

variable "mk8s_cluster_public_endpoint" {
  description = "Enable public endpoint for MK8s control plane."
  type        = bool
  default     = false
  nullable    = false
}

variable "ssh_user_name" {
  description = "SSH username for node cloud-init usage."
  type        = string
  default     = "ubuntu"
  nullable    = false
}

variable "ssh_public_key" {
  description = "Inline SSH public key."
  type        = string
  nullable    = false
}

variable "cpu_nodes_count" {
  description = "Fixed node count for CPU node group."
  type        = number
  default     = 2
  nullable    = false
}

variable "cpu_nodes_platform" {
  description = "CPU node group platform."
  type        = string
  nullable    = false
}

variable "cpu_nodes_preset" {
  description = "CPU node group preset."
  type        = string
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
}

variable "gpu_nodes_count_per_group" {
  description = "Fixed nodes per GPU node group."
  type        = number
  default     = 0
  nullable    = false
}

variable "gpu_nodes_platform" {
  description = "GPU node group platform."
  type        = string
  default     = ""
  nullable    = false
}

variable "gpu_nodes_preset" {
  description = "GPU node group preset."
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

variable "gpu_driverfull_image" {
  description = "Enable GPU driverfull image preset wiring."
  type        = bool
  default     = true
  nullable    = false
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
