locals {
  cluster_overrides = try(var.mk8s_cluster_overrides, {})

  cluster_control_plane_override = try(local.cluster_overrides.control_plane, {})
  cluster_control_plane_override_without_endpoints = {
    for key, value in local.cluster_control_plane_override : key => value
    if key != "endpoints"
  }

  cluster_control_plane_endpoints = merge(
    var.mk8s_cluster_public_endpoint ? { public_endpoint = {} } : {},
    try(local.cluster_control_plane_override.endpoints, {})
  )

  cluster_control_plane_base = merge(
    {
      subnet_id = var.subnet_id
    },
    var.k8s_version != null ? { version = var.k8s_version } : {},
    var.etcd_cluster_size != null ? { etcd_cluster_size = var.etcd_cluster_size } : {}
  )

  cluster_control_plane = merge(
    local.cluster_control_plane_base,
    local.cluster_control_plane_override_without_endpoints,
    length(local.cluster_control_plane_endpoints) > 0 ? { endpoints = local.cluster_control_plane_endpoints } : {}
  )

  cluster_kube_network_override = try(local.cluster_overrides.kube_network, {})
  cluster_kube_network_base = (
    length(var.kube_network_service_cidrs) > 0
    ? { service_cidrs = var.kube_network_service_cidrs }
    : {}
  )
  cluster_kube_network = merge(
    local.cluster_kube_network_base,
    local.cluster_kube_network_override
  )

  cluster_parent_id        = try(local.cluster_overrides.parent_id, var.parent_id)
  cluster_name_effective   = try(local.cluster_overrides.name, var.cluster_name)
  cluster_labels_effective = try(local.cluster_overrides.labels, {})

  cpu_overrides         = try(var.mk8s_cpu_node_group_overrides, {})
  cpu_template_override = try(local.cpu_overrides.template, {})
  cpu_template_override_without_boot_disk = {
    for key, value in local.cpu_template_override : key => value
    if key != "boot_disk"
  }
  cpu_template_boot_disk_override = try(local.cpu_template_override.boot_disk, {})
  cpu_labels                      = try(local.cpu_overrides.labels, {})
  cpu_template_boot_disk_base = merge(
    var.cpu_nodes_boot_disk_size_gib != null ? {
      size_gibibytes = var.cpu_nodes_boot_disk_size_gib
    } : {},
    var.cpu_nodes_boot_disk_type != null ? {
      type = var.cpu_nodes_boot_disk_type
    } : {}
  )
  cpu_template_boot_disk = merge(
    local.cpu_template_boot_disk_base,
    local.cpu_template_boot_disk_override
  )

  cpu_template_base = {
    resources = {
      platform = var.cpu_nodes_platform
      preset   = var.cpu_nodes_preset
    }
    os = var.cpu_nodes_os
    network_interfaces = [
      {
        subnet_id         = var.subnet_id
        public_ip_address = var.cpu_nodes_public_ips ? {} : null
      }
    ]
    preemptible = var.cpu_nodes_preemptible ? {} : null
  }

  cpu_template = merge(
    local.cpu_template_base,
    local.cpu_template_override_without_boot_disk,
    length(local.cpu_template_boot_disk) > 0 ? {
      boot_disk = local.cpu_template_boot_disk
    } : {}
  )
  cpu_effective_platform = try(trimspace(local.cpu_template.resources.platform), "")
  cpu_effective_preset   = try(trimspace(local.cpu_template.resources.preset), "")
  cpu_autoscaling        = try(local.cpu_overrides.autoscaling, null)
  cpu_fixed_node_count = (
    local.cpu_autoscaling != null
    ? try(local.cpu_overrides.fixed_node_count, null)
    : try(local.cpu_overrides.fixed_node_count, var.cpu_nodes_count)
  )
  cpu_node_group_enabled = (
    coalesce(local.cpu_fixed_node_count, 0) > 0 ||
    local.cpu_autoscaling != null
  )

  gpu_overrides         = try(var.mk8s_gpu_node_group_overrides, {})
  gpu_template_override = try(local.gpu_overrides.template, {})
  gpu_template_override_without_metadata = {
    for key, value in local.gpu_template_override : key => value
    if key != "metadata" && key != "boot_disk"
  }
  gpu_template_override_metadata  = try(local.gpu_template_override.metadata, {})
  gpu_effective_stack_preset      = var.gpu_stack_source == "nebius_image" ? var.gpu_stack_preset : null
  gpu_template_boot_disk_override = try(local.gpu_template_override.boot_disk, {})
  gpu_template_boot_disk_base = merge(
    var.gpu_nodes_boot_disk_size_gib != null ? {
      size_gibibytes = var.gpu_nodes_boot_disk_size_gib
    } : {},
    var.gpu_nodes_boot_disk_type != null ? {
      type = var.gpu_nodes_boot_disk_type
    } : {}
  )
  gpu_template_boot_disk = merge(
    local.gpu_template_boot_disk_base,
    local.gpu_template_boot_disk_override
  )

  gpu_template_base = {
    resources = {
      platform = var.gpu_nodes_platform
      preset   = var.gpu_nodes_preset
    }
    os = var.gpu_nodes_os
    network_interfaces = [
      {
        subnet_id         = var.subnet_id
        public_ip_address = var.gpu_nodes_public_ips ? {} : null
      }
    ]
    preemptible = var.gpu_nodes_preemptible ? {} : null
    gpu_settings = local.gpu_effective_stack_preset != null ? {
      drivers_preset = local.gpu_effective_stack_preset
    } : null
    gpu_cluster = try(one(nebius_compute_v1_gpu_cluster.this), null)
  }

  gpu_template_merged_no_metadata = merge(
    local.gpu_template_base,
    local.gpu_template_override_without_metadata
  )

  gpu_template = merge(
    local.gpu_template_merged_no_metadata,
    length(local.gpu_template_boot_disk) > 0 ? {
      boot_disk = local.gpu_template_boot_disk
    } : {},
    length(local.gpu_template_override_metadata) > 0 ? {
      metadata = local.gpu_template_override_metadata
    } : {}
  )
  gpu_effective_platform = try(trimspace(local.gpu_template.resources.platform), "")
  gpu_effective_preset   = try(trimspace(local.gpu_template.resources.preset), "")

  gpu_group_name_prefix = try(local.gpu_overrides.name, "${var.cluster_name}-ng-gpu")
  gpu_labels            = try(local.gpu_overrides.labels, {})
  gpu_autoscaling       = try(local.gpu_overrides.autoscaling, null)
  gpu_fixed_node_count = (
    local.gpu_autoscaling != null
    ? try(local.gpu_overrides.fixed_node_count, null)
    : try(local.gpu_overrides.fixed_node_count, var.gpu_nodes_count_per_group)
  )

  named_gpu_clusters = {
    for key, cluster in var.gpu_clusters : key => cluster
    if try(cluster.enabled, true)
  }

  named_node_groups = {
    for key, group in var.node_groups : key => group
    if try(group.enabled, true)
  }

  named_node_group_template_overrides = {
    for key, group in local.named_node_groups : key => try(group.template, {})
  }

  named_node_group_template_overrides_without_metadata = {
    for key, template in local.named_node_group_template_overrides : key => {
      for template_key, value in template : template_key => value
      if template_key != "metadata"
    }
  }

  named_node_group_gpu_enabled = {
    for key, group in local.named_node_groups : key => try(group.gpu, false)
  }

  generic_gpu_node_group_keys = [
    for key, enabled in local.named_node_group_gpu_enabled : key
    if enabled
  ]

  named_node_group_default_labels = {
    for key, group in local.named_node_groups : key => merge(
      {
        "slurm.nebius.ai/nodeset-name" = try(group.nodeset_name, key)
      },
      try(group.workload, "") != "" ? {
        "slurm.nebius.ai/workload" = group.workload
      } : {},
      try(group.jail, false) ? {
        "slurm.nebius.ai/jail" = "true"
      } : {},
      try(group.gpu, false) ? {
        "nebius.com/gpu" = "true"
      } : {},
      try(group.node_labels, {})
    )
  }

  named_node_group_template_metadata = {
    for key, template in local.named_node_group_template_overrides : key => merge(
      try(template.metadata, {}),
      {
        labels = merge(
          local.named_node_group_default_labels[key],
          try(template.metadata.labels, {})
        )
      }
    )
  }

  named_node_group_template_base = {
    for key, group in local.named_node_groups : key => {
      resources = {
        platform = coalesce(try(group.platform, null), local.named_node_group_gpu_enabled[key] ? var.gpu_nodes_platform : var.cpu_nodes_platform)
        preset   = coalesce(try(group.preset, null), local.named_node_group_gpu_enabled[key] ? var.gpu_nodes_preset : var.cpu_nodes_preset)
      }
      os = try(coalesce(try(group.os, null), local.named_node_group_gpu_enabled[key] ? try(local.gpu_template.os, null) : try(local.cpu_template.os, null)), null)
      network_interfaces = coalesce(
        try(group.network_interfaces, null),
        [
          {
            subnet_id         = try(group.subnet_id, var.subnet_id)
            public_ip_address = try(group.public_ips, false) ? {} : null
          }
        ]
      )
      boot_disk   = try(coalesce(try(group.boot_disk, null), local.named_node_group_gpu_enabled[key] ? try(local.gpu_template.boot_disk, null) : try(local.cpu_template.boot_disk, null)), null)
      preemptible = try(coalesce(try(group.preemptible, null), local.named_node_group_gpu_enabled[key] ? try(local.gpu_template.preemptible, null) : try(local.cpu_template.preemptible, null)), null)
      taints      = try(group.taints, null)
      filesystems = try(group.filesystems, null)
    }
  }

  named_node_group_templates = {
    for key, group in local.named_node_groups : key => merge(
      local.named_node_group_template_base[key],
      local.named_node_group_template_overrides_without_metadata[key],
      length(local.named_node_group_template_metadata[key]) > 0 ? {
        metadata = local.named_node_group_template_metadata[key]
      } : {},
      try(coalesce(try(group.gpu_stack_preset, null), local.named_node_group_gpu_enabled[key] ? local.gpu_effective_stack_preset : null), null) != null ? {
        gpu_settings = {
          drivers_preset = try(coalesce(try(group.gpu_stack_preset, null), local.gpu_effective_stack_preset), null)
        }
      } : {},
      try(group.gpu_cluster_key, null) != null ? {
        gpu_cluster = {
          id = nebius_compute_v1_gpu_cluster.generic[group.gpu_cluster_key].id
        }
      } : {},
      try(group.gpu_cluster_id, null) != null ? {
        gpu_cluster = {
          id = group.gpu_cluster_id
        }
      } : {}
    )
  }

  named_node_group_labels = {
    for key, group in local.named_node_groups : key => merge(
      local.named_node_group_default_labels[key],
      try(group.labels, {})
    )
  }
}
