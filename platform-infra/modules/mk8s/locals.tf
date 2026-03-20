locals {
  cluster_overrides = coalesce(try(var.mk8s_cluster_overrides, null), {})

  cluster_control_plane_override = coalesce(try(local.cluster_overrides.control_plane, null), {})
  cluster_control_plane_override_without_endpoints = {
    for key, value in local.cluster_control_plane_override : key => value
    if key != "endpoints"
  }

  cluster_control_plane_endpoints = merge(
    var.mk8s_cluster_public_endpoint ? { public_endpoint = {} } : {},
    coalesce(try(local.cluster_control_plane_override.endpoints, null), {})
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

  cluster_kube_network_override = coalesce(try(local.cluster_overrides.kube_network, null), {})
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
  cluster_labels_effective = coalesce(try(local.cluster_overrides.labels, null), {})

  cpu_overrides         = coalesce(try(var.mk8s_cpu_node_group_overrides, null), {})
  cpu_template_override = coalesce(try(local.cpu_overrides.template, null), {})
  cpu_labels            = coalesce(try(local.cpu_overrides.labels, null), {})

  cpu_template_base = {
    resources = {
      platform = var.cpu_nodes_platform
      preset   = var.cpu_nodes_preset
    }
    network_interfaces = [
      {
        subnet_id         = var.subnet_id
        public_ip_address = var.cpu_nodes_public_ips ? {} : null
      }
    ]
    preemptible = var.cpu_nodes_preemptible ? {} : null
  }

  cpu_template           = merge(local.cpu_template_base, local.cpu_template_override)
  cpu_effective_platform = try(trimspace(local.cpu_template.resources.platform), "")
  cpu_effective_preset   = try(trimspace(local.cpu_template.resources.preset), "")
  cpu_autoscaling        = try(local.cpu_overrides.autoscaling, null)
  cpu_fixed_node_count = (
    local.cpu_autoscaling != null
    ? try(local.cpu_overrides.fixed_node_count, null)
    : try(local.cpu_overrides.fixed_node_count, var.cpu_nodes_count)
  )
  cpu_node_group_enabled = (
    var.cpu_nodes_count > 0 ||
    local.cpu_autoscaling != null
  )

  gpu_overrides         = coalesce(try(var.mk8s_gpu_node_group_overrides, null), {})
  gpu_template_override = coalesce(try(local.gpu_overrides.template, null), {})
  gpu_template_override_without_metadata = {
    for key, value in local.gpu_template_override : key => value
    if key != "metadata"
  }
  gpu_template_override_metadata = coalesce(try(local.gpu_template_override.metadata, null), {})

  gpu_base_labels = merge(
    var.mig_parted_config != null ? { "nvidia.com/mig.config" = var.mig_parted_config } : {},
    var.mig_strategy != null ? { "nvidia.com/mig.strategy" = var.mig_strategy } : {}
  )

  gpu_override_labels = coalesce(try(local.gpu_template_override_metadata.labels, null), {})
  gpu_metadata = merge(
    local.gpu_template_override_metadata,
    length(merge(local.gpu_base_labels, local.gpu_override_labels)) > 0 ? {
      labels = merge(local.gpu_base_labels, local.gpu_override_labels)
    } : {}
  )

  gpu_effective_drivers_preset = coalesce(
    var.gpu_drivers_preset,
    lookup(var.gpu_driver_preset_map, var.gpu_nodes_platform, null),
    var.gpu_default_drivers_preset
  )

  gpu_template_base = {
    resources = {
      platform = var.gpu_nodes_platform
      preset   = var.gpu_nodes_preset
    }
    network_interfaces = [
      {
        subnet_id         = var.subnet_id
        public_ip_address = var.gpu_nodes_public_ips ? {} : null
      }
    ]
    preemptible = var.gpu_nodes_preemptible ? {} : null
    gpu_settings = local.gpu_effective_drivers_preset != null ? {
      drivers_preset = local.gpu_effective_drivers_preset
    } : null
    gpu_cluster = try(one(nebius_compute_v1_gpu_cluster.this), null)
  }

  gpu_template_merged_no_metadata = merge(
    local.gpu_template_base,
    local.gpu_template_override_without_metadata
  )

  gpu_template = merge(
    local.gpu_template_merged_no_metadata,
    length(local.gpu_metadata) > 0 ? { metadata = local.gpu_metadata } : {}
  )
  gpu_effective_platform = try(trimspace(local.gpu_template.resources.platform), "")
  gpu_effective_preset   = try(trimspace(local.gpu_template.resources.preset), "")

  gpu_group_name_prefix = try(local.gpu_overrides.name, "${var.cluster_name}-ng-gpu")
  gpu_labels            = coalesce(try(local.gpu_overrides.labels, null), {})
  gpu_autoscaling       = try(local.gpu_overrides.autoscaling, null)
  gpu_fixed_node_count = (
    local.gpu_autoscaling != null
    ? try(local.gpu_overrides.fixed_node_count, null)
    : try(local.gpu_overrides.fixed_node_count, var.gpu_nodes_count_per_group)
  )
}
