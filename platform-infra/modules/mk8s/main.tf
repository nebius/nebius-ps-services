data "nebius_vpc_v1_network" "cluster" {
  id = var.cluster.network_id
}

data "nebius_vpc_v1_subnet" "selected" {
  for_each = local.subnet_refs_to_validate

  id = each.value
}

resource "nebius_mk8s_v1_cluster" "this" {
  parent_id     = var.cluster.parent_id
  name          = var.cluster.cluster_name
  labels        = length(var.cluster.labels) > 0 ? var.cluster.labels : null
  control_plane = local.cluster_control_plane
  kube_network  = length(local.cluster_kube_network) > 0 ? local.cluster_kube_network : null

  lifecycle {
    precondition {
      condition     = data.nebius_vpc_v1_network.cluster.parent_id == var.cluster.parent_id
      error_message = "cluster.network_id must identify a VPC network in cluster.parent_id."
    }

    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected["cluster"].parent_id == var.cluster.parent_id
      error_message = "cluster.subnet_id must identify a VPC subnet in cluster.parent_id."
    }

    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected["cluster"].network_id == var.cluster.network_id
      error_message = "cluster.subnet_id must belong to cluster.network_id."
    }
  }
}

resource "nebius_compute_v1_gpu_cluster" "this" {
  for_each = local.gpu_clusters

  parent_id         = coalesce(try(each.value.parent_id, null), var.cluster.parent_id)
  name              = coalesce(try(each.value.name, null), "${var.cluster.cluster_name}-${each.key}-gpu-cluster")
  infiniband_fabric = each.value.infiniband_fabric
  labels            = length(each.value.labels) > 0 ? each.value.labels : null
}

resource "nebius_iam_v1_service_account" "node_group" {
  for_each = local.node_group_service_accounts_to_create

  parent_id   = var.cluster.parent_id
  name        = each.value.name
  description = try(each.value.description, null)
  labels      = length(try(each.value.labels, {})) > 0 ? each.value.labels : null
}

resource "nebius_mk8s_v1_node_group" "this" {
  for_each = local.node_groups

  parent_id = coalesce(try(each.value.parent_id, null), nebius_mk8s_v1_cluster.this.id)
  name      = coalesce(try(each.value.name, null), "${var.cluster.cluster_name}-${each.key}")
  labels    = length(each.value.labels) > 0 ? each.value.labels : null
  version   = coalesce(try(each.value.version, null), var.cluster.k8s_version)

  fixed_node_count = try(each.value.node_count, null)
  autoscaling      = local.node_group_autoscaling[each.key]
  auto_repair      = try(each.value.auto_repair, null)
  strategy         = try(each.value.strategy, null)

  template = local.node_group_templates[each.key]

  lifecycle {
    precondition {
      condition = !(
        try(each.value.node_count, null) != null &&
        local.node_group_autoscaling[each.key] != null
      )
      error_message = "Node group '${each.key}' cannot set both node_count and enabled autoscaling."
    }
    precondition {
      condition = (
        try(each.value.node_count, null) != null ||
        local.node_group_autoscaling[each.key] != null
      )
      error_message = "Node group '${each.key}' requires node_count or enabled autoscaling."
    }
    precondition {
      condition = (
        length(trimspace(each.value.platform)) > 0 &&
        length(trimspace(each.value.preset)) > 0
      )
      error_message = "Node group '${each.key}' requires platform and preset."
    }
    precondition {
      condition = (
        try(each.value.gpu, false) == false ||
        try(each.value.gpu_stack_source, "nebius_image") != "nebius_image" ||
        length(trimspace(try(each.value.gpu_stack_preset != null ? each.value.gpu_stack_preset : "", ""))) > 0
      )
      error_message = "Node group '${each.key}' requires gpu_stack_preset when gpu_stack_source is 'nebius_image'."
    }
    precondition {
      condition = !(
        length(trimspace(try(each.value.gpu_cluster_key != null ? each.value.gpu_cluster_key : "", ""))) > 0 &&
        length(trimspace(try(each.value.gpu_cluster_id != null ? each.value.gpu_cluster_id : "", ""))) > 0
      )
      error_message = "Node group '${each.key}' can set only one of gpu_cluster_key or gpu_cluster_id."
    }
    precondition {
      condition = (
        length(trimspace(try(each.value.gpu_cluster_key != null ? each.value.gpu_cluster_key : "", ""))) == 0 ||
        contains(keys(local.gpu_clusters), each.value.gpu_cluster_key)
      )
      error_message = "Node group '${each.key}' references unknown gpu_cluster_key."
    }
    precondition {
      condition = (
        try(each.value.reservation, null) == null ||
        try(each.value.gpu, false)
      )
      error_message = "Node group '${each.key}' can use reservation only when gpu=true."
    }
    precondition {
      condition = !(
        try(each.value.reservation.policy, null) == "FORBID" &&
        length(try(each.value.reservation.reservation_ids, [])) > 0
      )
      error_message = "Node group '${each.key}' cannot set reservation_ids when reservation.policy is FORBID."
    }
    precondition {
      condition = !(
        length(trimspace(try(each.value.service_account.id != null ? each.value.service_account.id : "", ""))) > 0 &&
        length(trimspace(try(each.value.service_account.name != null ? each.value.service_account.name : "", ""))) > 0
      )
      error_message = "Node group '${each.key}' can set only one of service_account.id or service_account.name."
    }
    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected["node_group:${each.key}"].parent_id == var.cluster.parent_id
      error_message = "Node group '${each.key}' subnet_id must identify a VPC subnet in cluster.parent_id."
    }
    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected["node_group:${each.key}"].network_id == var.cluster.network_id
      error_message = "Node group '${each.key}' subnet_id must belong to cluster.network_id."
    }
    precondition {
      condition = alltrue([
        for index, interface in local.node_group_network_interfaces[each.key] :
        data.nebius_vpc_v1_subnet.selected["node_group_interface:${each.key}:${index}"].parent_id == var.cluster.parent_id
      ])
      error_message = "Node group '${each.key}' network_interfaces subnet_id values must identify VPC subnets in cluster.parent_id."
    }
    precondition {
      condition = alltrue([
        for index, interface in local.node_group_network_interfaces[each.key] :
        data.nebius_vpc_v1_subnet.selected["node_group_interface:${each.key}:${index}"].network_id == var.cluster.network_id
      ])
      error_message = "Node group '${each.key}' network_interfaces subnet_id values must belong to cluster.network_id."
    }
  }
}
