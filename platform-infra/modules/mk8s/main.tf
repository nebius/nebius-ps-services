resource "nebius_mk8s_v1_cluster" "this" {
  parent_id     = local.cluster_parent_id
  name          = local.cluster_name_effective
  labels        = length(local.cluster_labels_effective) > 0 ? local.cluster_labels_effective : null
  control_plane = local.cluster_control_plane
  kube_network  = length(local.cluster_kube_network) > 0 ? local.cluster_kube_network : null

  lifecycle {
    precondition {
      condition = (
        !local.cpu_node_group_enabled ||
        (
          length(local.cpu_effective_platform) > 0 &&
          length(local.cpu_effective_preset) > 0
        )
      )
      error_message = "CPU node group resources.platform and resources.preset must be set when CPU node group creation is enabled."
    }
    precondition {
      condition     = !var.gpu_enabled || var.gpu_node_groups > 0
      error_message = "gpu_node_groups must be > 0 when gpu_enabled=true."
    }
    precondition {
      condition = (
        !var.gpu_enabled ||
        local.gpu_autoscaling != null ||
        var.gpu_nodes_count_per_group > 0
      )
      error_message = "gpu_nodes_count_per_group must be > 0 when gpu_enabled=true and autoscaling is not configured."
    }
    precondition {
      condition = (
        !var.gpu_enabled ||
        (
          length(local.gpu_effective_platform) > 0 &&
          length(local.gpu_effective_preset) > 0
        )
      )
      error_message = "GPU node group resources.platform and resources.preset must be set when gpu_enabled=true."
    }
    precondition {
      condition = (
        !var.gpu_enabled ||
        var.gpu_stack_source == "operator_managed" ||
        (
          local.gpu_effective_stack_preset != null &&
          length(trimspace(local.gpu_effective_stack_preset)) > 0
        )
      )
      error_message = "gpu_stack_preset must be set when gpu_enabled=true and gpu_stack_source=nebius_image."
    }
  }
}

resource "nebius_compute_v1_gpu_cluster" "this" {
  count = var.gpu_enabled && trimspace(var.infiniband_fabric) != "" ? 1 : 0

  parent_id         = var.parent_id
  name              = "${var.cluster_name}-gpu-cluster"
  infiniband_fabric = var.infiniband_fabric
}

resource "nebius_mk8s_v1_node_group" "cpu" {
  count = local.cpu_node_group_enabled ? 1 : 0

  parent_id = try(local.cpu_overrides.parent_id, nebius_mk8s_v1_cluster.this.id)
  name      = try(local.cpu_overrides.name, "${var.cluster_name}-ng-cpu")
  labels    = length(local.cpu_labels) > 0 ? local.cpu_labels : null
  version   = try(local.cpu_overrides.version, var.k8s_version)

  fixed_node_count = local.cpu_fixed_node_count
  autoscaling      = local.cpu_autoscaling
  auto_repair      = try(local.cpu_overrides.auto_repair, null)
  strategy         = try(local.cpu_overrides.strategy, null)

  template = local.cpu_template

  lifecycle {
    precondition {
      condition = !(
        local.cpu_fixed_node_count != null &&
        local.cpu_autoscaling != null
      )
      error_message = "CPU node group cannot set both fixed_node_count and autoscaling."
    }
  }
}

resource "nebius_mk8s_v1_node_group" "gpu" {
  count = var.gpu_enabled ? max(var.gpu_node_groups, 0) : 0

  parent_id = try(local.gpu_overrides.parent_id, nebius_mk8s_v1_cluster.this.id)
  name      = var.gpu_node_groups > 1 ? "${local.gpu_group_name_prefix}-${count.index}" : local.gpu_group_name_prefix
  labels    = length(local.gpu_labels) > 0 ? local.gpu_labels : null
  version   = try(local.gpu_overrides.version, var.k8s_version)

  fixed_node_count = local.gpu_fixed_node_count
  autoscaling      = local.gpu_autoscaling
  auto_repair      = try(local.gpu_overrides.auto_repair, null)
  strategy         = try(local.gpu_overrides.strategy, null)

  template = local.gpu_template

  lifecycle {
    precondition {
      condition = !(
        local.gpu_fixed_node_count != null &&
        local.gpu_autoscaling != null
      )
      error_message = "GPU node group cannot set both fixed_node_count and autoscaling."
    }
  }
}
