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
      condition = (
        !var.gpu_enabled ||
        var.gpu_node_groups > 0 ||
        length(local.generic_gpu_node_group_keys) > 0
      )
      error_message = "gpu_enabled=true requires either gpu_node_groups > 0 or at least one generic node_groups entry with gpu=true."
    }
    precondition {
      condition = (
        !var.gpu_enabled ||
        var.gpu_node_groups == 0 ||
        local.gpu_autoscaling != null ||
        var.gpu_nodes_count_per_group > 0
      )
      error_message = "gpu_nodes_count_per_group must be > 0 when built-in GPU node-group shortcut is enabled and autoscaling is not configured."
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
  count = var.gpu_enabled && var.gpu_node_groups > 0 && trimspace(var.infiniband_fabric) != "" ? 1 : 0

  parent_id         = var.parent_id
  name              = "${var.cluster_name}-gpu-cluster"
  infiniband_fabric = var.infiniband_fabric
}

resource "nebius_compute_v1_gpu_cluster" "generic" {
  for_each = local.named_gpu_clusters

  parent_id         = try(each.value.parent_id, var.parent_id)
  name              = try(each.value.name, "${var.cluster_name}-${each.key}-gpu-cluster")
  infiniband_fabric = each.value.infiniband_fabric
  labels            = length(coalesce(try(each.value.labels, null), {})) > 0 ? each.value.labels : null
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

resource "nebius_mk8s_v1_node_group" "generic" {
  for_each = local.named_node_groups

  parent_id = try(each.value.parent_id, nebius_mk8s_v1_cluster.this.id)
  name      = try(each.value.name, "${var.cluster_name}-${each.key}")
  labels    = length(local.named_node_group_labels[each.key]) > 0 ? local.named_node_group_labels[each.key] : null
  version   = try(each.value.version, var.k8s_version)

  fixed_node_count = try(each.value.fixed_node_count, null)
  autoscaling      = try(each.value.autoscaling, null)
  auto_repair      = try(each.value.auto_repair, null)
  strategy         = try(each.value.strategy, null)

  template = local.named_node_group_templates[each.key]

  lifecycle {
    precondition {
      condition = !(
        try(each.value.fixed_node_count, null) != null &&
        try(each.value.autoscaling, null) != null
      )
      error_message = "Generic node group '${each.key}' cannot set both fixed_node_count and autoscaling."
    }
    precondition {
      condition = (
        try(length(trimspace(local.named_node_group_templates[each.key].resources.platform)) > 0, false) &&
        try(length(trimspace(local.named_node_group_templates[each.key].resources.preset)) > 0, false)
      )
      error_message = "Generic node group '${each.key}' requires platform and preset, either at node_groups.${each.key}.platform/preset or node_groups.${each.key}.template.resources."
    }
    precondition {
      condition = !(
        try(each.value.gpu_cluster_key, null) != null &&
        try(each.value.gpu_cluster_id, null) != null
      )
      error_message = "Generic node group '${each.key}' can set only one of gpu_cluster_key or gpu_cluster_id."
    }
  }
}
