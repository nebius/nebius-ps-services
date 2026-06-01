variable "cluster" {
  description = "Canonical MK8s cluster configuration. The subnet is the provider control-plane subnet; network_id is retained for caller and wizard validation that the subnet belongs to the selected VPC."
  type = object({
    parent_id         = string
    cluster_name      = string
    network_id        = string
    subnet_id         = string
    k8s_version       = string
    public_endpoint   = bool
    etcd_cluster_size = optional(number)
    labels            = optional(map(string), {})
    control_plane     = optional(any, {})
    kube_network = optional(object({
      service_cidrs = optional(list(string), ["/20"])
    }), {})
  })
  nullable = false

  validation {
    condition     = length(trimspace(var.cluster.parent_id)) > 0
    error_message = "cluster.parent_id cannot be empty."
  }
  validation {
    condition     = length(trimspace(var.cluster.cluster_name)) > 0
    error_message = "cluster.cluster_name cannot be empty."
  }
  validation {
    condition     = length(trimspace(var.cluster.network_id)) > 0
    error_message = "cluster.network_id cannot be empty."
  }
  validation {
    condition     = length(trimspace(var.cluster.subnet_id)) > 0
    error_message = "cluster.subnet_id cannot be empty."
  }
  validation {
    condition     = length(trimspace(var.cluster.k8s_version)) > 0
    error_message = "cluster.k8s_version cannot be empty."
  }
  validation {
    condition = (
      try(var.cluster.etcd_cluster_size, null) == null ||
      (
        floor(var.cluster.etcd_cluster_size) == var.cluster.etcd_cluster_size &&
        var.cluster.etcd_cluster_size >= 1
      )
    )
    error_message = "cluster.etcd_cluster_size must be an integer >= 1 when provided."
  }
  validation {
    condition = alltrue([
      for cidr in try(var.cluster.kube_network.service_cidrs, ["/20"]) :
      length(trimspace(cidr)) > 0
    ])
    error_message = "cluster.kube_network.service_cidrs must contain only non-empty CIDR or prefix-length strings."
  }
}

variable "gpu_clusters" {
  description = "Optional named Nebius GPU clusters keyed by caller-owned logical name. Node groups attach with gpu_cluster_key or gpu_cluster_id."
  type = map(object({
    enabled           = optional(bool, true)
    parent_id         = optional(string)
    name              = optional(string)
    labels            = optional(map(string), {})
    infiniband_fabric = optional(string)
  }))
  default  = {}
  nullable = false

  validation {
    condition = alltrue([
      for key, cluster in var.gpu_clusters : (
        length(trimspace(key)) > 0 &&
        (
          try(cluster.enabled, true) == false ||
          length(trimspace(try(cluster.infiniband_fabric != null ? cluster.infiniband_fabric : "", ""))) > 0
        )
      )
    ])
    error_message = "Each enabled gpu_clusters entry must have a non-empty key and infiniband_fabric."
  }
}

variable "node_groups" {
  description = "Canonical MK8s node groups keyed by logical name. Every node group declares its own shape, placement, optional service account, reservation, SSH, and filesystem attachments."
  type = map(object({
    enabled     = optional(bool, true)
    name        = optional(string)
    parent_id   = optional(string)
    version     = optional(string)
    labels      = optional(map(string), {})
    node_labels = optional(map(string), {})
    node_count  = optional(number)
    autoscaling = optional(object({
      enabled        = optional(bool, true)
      min_node_count = optional(number)
      max_node_count = optional(number)
    }))
    auto_repair = optional(any)
    strategy    = optional(any)
    gpu         = optional(bool, false)
    platform    = optional(string)
    preset      = optional(string)
    os          = optional(string)
    subnet_id   = optional(string)
    public_ips  = optional(bool, false)
    boot_disk = optional(object({
      size_bytes       = optional(number)
      size_kibibytes   = optional(number)
      size_mebibytes   = optional(number)
      size_gibibytes   = optional(number)
      block_size_bytes = optional(number)
      type             = optional(string)
    }))
    preemptible = optional(bool, false)
    taints = optional(list(object({
      key    = string
      value  = string
      effect = string
    })), [])
    network_interfaces = optional(any)
    local_disks        = optional(any)
    gpu_stack_source   = optional(string, "nebius_image")
    gpu_stack_preset   = optional(string)
    gpu_cluster_key    = optional(string)
    gpu_cluster_id     = optional(string)
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
    filesystems = optional(list(object({
      attach_mode = string
      mount_tag   = string
      existing_filesystem = optional(object({
        id = string
      }))
    })), [])
    sfs_filesystem_keys = optional(list(string), [])
  }))
  nullable = false

  validation {
    condition = alltrue([
      for key, group in var.node_groups : length(trimspace(key)) > 0
    ])
    error_message = "Each node_groups entry must have a non-empty key."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        (
          length(trimspace(try(group.platform != null ? group.platform : "", ""))) > 0 &&
          length(trimspace(try(group.preset != null ? group.preset : "", ""))) > 0
        )
      )
    ])
    error_message = "Each enabled node group requires platform and preset."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        !(
          try(group.node_count, null) != null &&
          try(group.autoscaling, null) != null &&
          try(group.autoscaling.enabled, true)
        )
      )
    ])
    error_message = "A node group cannot set both node_count and enabled autoscaling."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.node_count, null) == null ||
        (
          floor(group.node_count) == group.node_count &&
          group.node_count >= 0
        )
      )
    ])
    error_message = "node_groups[*].node_count must be an integer >= 0 when provided."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.node_count, null) != null ||
        (
          try(group.autoscaling, null) != null &&
          try(group.autoscaling.enabled, true)
        )
      )
    ])
    error_message = "Each enabled node group requires node_count or enabled autoscaling."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.autoscaling, null) == null ||
        try(group.autoscaling.enabled, true) == false ||
        (
          try(floor(group.autoscaling.min_node_count) == group.autoscaling.min_node_count, false) &&
          try(group.autoscaling.min_node_count >= 0, false) &&
          try(floor(group.autoscaling.max_node_count) == group.autoscaling.max_node_count, false) &&
          try(group.autoscaling.max_node_count >= group.autoscaling.min_node_count, false)
        )
      )
    ])
    error_message = "Enabled node_groups[*].autoscaling requires integer min_node_count >= 0 and max_node_count >= min_node_count."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.subnet_id, null) == null ||
        try(length(trimspace(group.subnet_id)) > 0, true)
      )
    ])
    error_message = "node_groups[*].subnet_id cannot be empty when provided."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.network_interfaces, null) == null ||
        try(
          length(group.network_interfaces) > 0 &&
          alltrue([
            for interface in group.network_interfaces :
            length(trimspace(try(interface.subnet_id, ""))) > 0
          ]),
          false
        )
      )
    ])
    error_message = "Explicit node_groups[*].network_interfaces entries must include non-empty subnet_id values."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : contains(
        ["nebius_image", "operator_managed"],
        trimspace(try(group.gpu_stack_source, "nebius_image"))
      )
    ])
    error_message = "node_groups[*].gpu_stack_source must be 'nebius_image' or 'operator_managed'."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.enabled, true) == false ||
        try(group.gpu, false) == false ||
        try(group.gpu_stack_source, "nebius_image") != "nebius_image" ||
        length(trimspace(try(group.gpu_stack_preset != null ? group.gpu_stack_preset : "", ""))) > 0
      )
    ])
    error_message = "GPU node groups require gpu_stack_preset when gpu_stack_source is 'nebius_image'."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : contains(
        ["AUTO", "FORBID", "STRICT"],
        trimspace(try(group.reservation.policy, group.gpu ? "FORBID" : "FORBID"))
      )
    ])
    error_message = "node_groups[*].reservation.policy must be AUTO, FORBID, or STRICT."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : !(
        try(group.reservation.policy, null) == "FORBID" &&
        length(try(group.reservation.reservation_ids, [])) > 0
      )
    ])
    error_message = "node_groups[*].reservation.reservation_ids cannot be set when reservation.policy is FORBID."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : (
        try(group.reservation, null) == null ||
        try(group.gpu, false)
      )
    ])
    error_message = "node_groups[*].reservation is supported only for GPU node groups."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : !(
        length(trimspace(try(group.service_account.id != null ? group.service_account.id : "", ""))) > 0 &&
        length(trimspace(try(group.service_account.name != null ? group.service_account.name : "", ""))) > 0
      )
    ])
    error_message = "node_groups[*].service_account can set only one of id or name."
  }
  validation {
    condition = alltrue([
      for key, group in var.node_groups : !(
        length(trimspace(try(group.gpu_cluster_key != null ? group.gpu_cluster_key : "", ""))) > 0 &&
        length(trimspace(try(group.gpu_cluster_id != null ? group.gpu_cluster_id : "", ""))) > 0
      )
    ])
    error_message = "node_groups[*] can set only one of gpu_cluster_key or gpu_cluster_id."
  }
}
