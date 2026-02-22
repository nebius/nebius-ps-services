locals {
  cluster_overrides = var.mk8s_cluster_overrides

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

  cluster_parent_id        = try(local.cluster_overrides.parent_id, var.parent_id)
  cluster_name_effective   = try(local.cluster_overrides.name, var.cluster_name)
  cluster_labels_effective = try(local.cluster_overrides.labels, {})

  cpu_overrides         = var.mk8s_cpu_node_group_overrides
  cpu_template_override = try(local.cpu_overrides.template, {})

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
    preemptible = var.cpu_nodes_preemptible ? {
      on_preemption = "STOP"
      priority      = 3
    } : null
  }

  cpu_template = merge(local.cpu_template_base, local.cpu_template_override)

  gpu_overrides         = var.mk8s_gpu_node_group_overrides
  gpu_template_override = try(local.gpu_overrides.template, {})
  gpu_template_override_without_metadata = {
    for key, value in local.gpu_template_override : key => value
    if key != "metadata"
  }

  gpu_base_labels = merge(
    var.mig_parted_config != null ? { "nvidia.com/mig.config" = var.mig_parted_config } : {},
    var.mig_strategy != null ? { "nvidia.com/mig.strategy" = var.mig_strategy } : {}
  )

  gpu_override_labels = try(local.gpu_template_override.metadata.labels, {})
  gpu_metadata_labels = merge(local.gpu_base_labels, local.gpu_override_labels)

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
    preemptible = var.gpu_nodes_preemptible ? {
      on_preemption = "STOP"
      priority      = 3
    } : null
    gpu_settings = var.gpu_driverfull_image ? {
      drivers_preset = lookup(
        {
          "gpu-b200-sxm"   = "cuda12.8"
          "gpu-b200-sxm-a" = "cuda12.8"
        },
        var.gpu_nodes_platform,
        "cuda13.0"
      )
    } : null
    gpu_cluster = try(one(nebius_compute_v1_gpu_cluster.this), null)
  }

  gpu_template_merged_no_metadata = merge(
    local.gpu_template_base,
    local.gpu_template_override_without_metadata
  )

  gpu_template = length(local.gpu_metadata_labels) > 0 ? merge(
    local.gpu_template_merged_no_metadata,
    {
      metadata = {
        labels = local.gpu_metadata_labels
      }
    }
  ) : local.gpu_template_merged_no_metadata

  gpu_group_name_prefix = try(local.gpu_overrides.name, "${var.cluster_name}-ng-gpu")
}

resource "nebius_mk8s_v1_cluster" "this" {
  parent_id     = local.cluster_parent_id
  name          = local.cluster_name_effective
  labels        = length(local.cluster_labels_effective) > 0 ? local.cluster_labels_effective : null
  control_plane = local.cluster_control_plane
  kube_network  = try(local.cluster_overrides.kube_network, null)
}

resource "nebius_compute_v1_gpu_cluster" "this" {
  count = var.gpu_enabled && trimspace(var.infiniband_fabric) != "" ? 1 : 0

  parent_id         = var.parent_id
  name              = "${var.cluster_name}-gpu-cluster"
  infiniband_fabric = var.infiniband_fabric
}

resource "nebius_mk8s_v1_node_group" "cpu" {
  count = var.cpu_nodes_count > 0 ? 1 : 0

  parent_id = try(local.cpu_overrides.parent_id, nebius_mk8s_v1_cluster.this.id)
  name      = try(local.cpu_overrides.name, "${var.cluster_name}-ng-cpu")
  labels    = length(try(local.cpu_overrides.labels, {})) > 0 ? try(local.cpu_overrides.labels, {}) : null
  version   = try(local.cpu_overrides.version, var.k8s_version)

  fixed_node_count = try(local.cpu_overrides.fixed_node_count, var.cpu_nodes_count)
  autoscaling      = try(local.cpu_overrides.autoscaling, null)
  auto_repair      = try(local.cpu_overrides.auto_repair, null)
  strategy         = try(local.cpu_overrides.strategy, null)

  template = local.cpu_template
}

resource "nebius_mk8s_v1_node_group" "gpu" {
  count = var.gpu_enabled ? max(var.gpu_node_groups, 0) : 0

  parent_id = try(local.gpu_overrides.parent_id, nebius_mk8s_v1_cluster.this.id)
  name      = var.gpu_node_groups > 1 ? "${local.gpu_group_name_prefix}-${count.index}" : local.gpu_group_name_prefix
  labels    = length(try(local.gpu_overrides.labels, {})) > 0 ? try(local.gpu_overrides.labels, {}) : null
  version   = try(local.gpu_overrides.version, var.k8s_version)

  fixed_node_count = try(local.gpu_overrides.fixed_node_count, var.gpu_nodes_count_per_group)
  autoscaling      = try(local.gpu_overrides.autoscaling, null)
  auto_repair      = try(local.gpu_overrides.auto_repair, null)
  strategy         = try(local.gpu_overrides.strategy, null)

  template = local.gpu_template
}
