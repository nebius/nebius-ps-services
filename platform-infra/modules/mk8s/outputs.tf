output "cluster_id" {
  description = "MK8s cluster ID."
  value       = nebius_mk8s_v1_cluster.this.id
}

output "cluster_name" {
  description = "MK8s cluster name."
  value       = nebius_mk8s_v1_cluster.this.name
}

output "cluster_network_id" {
  description = "Configured VPC network ID for the MK8s cluster."
  value       = var.cluster.network_id
}

output "cluster_subnet_id" {
  description = "Configured control-plane subnet ID for the MK8s cluster."
  value       = var.cluster.subnet_id
}

output "cluster_k8s_version" {
  description = "Configured Kubernetes version."
  value       = var.cluster.k8s_version
}

output "cluster_public_endpoint_enabled" {
  description = "Whether the MK8s public control-plane endpoint is enabled."
  value       = var.cluster.public_endpoint
}

output "node_group_ids" {
  description = "Node group IDs keyed by canonical node_groups key."
  value       = { for key, item in nebius_mk8s_v1_node_group.this : key => item.id }
}

output "node_groups" {
  description = "Configured node-group metadata keyed by canonical node_groups key."
  value = {
    for key, group in local.node_groups : key => {
      id                  = nebius_mk8s_v1_node_group.this[key].id
      name                = nebius_mk8s_v1_node_group.this[key].name
      gpu                 = try(group.gpu, false)
      platform            = group.platform
      preset              = group.preset
      node_count          = try(group.node_count, null)
      labels              = try(group.labels, {})
      node_labels         = try(group.node_labels, {})
      service_account_id  = local.node_group_service_account_ids[key]
      gpu_cluster_id      = local.node_group_gpu_cluster_ids[key]
      sfs_filesystem_keys = try(group.sfs_filesystem_keys, [])
    }
  }
}

output "gpu_cluster_ids" {
  description = "GPU cluster IDs keyed by gpu_clusters key."
  value       = { for key, item in nebius_compute_v1_gpu_cluster.this : key => item.id }
}

output "service_account_ids" {
  description = "Node-group service account IDs keyed by node_groups key when configured."
  value = {
    for key, id in local.node_group_service_account_ids : key => id
    if id != null
  }
}

output "sfs_filesystem_keys_by_node_group" {
  description = "SFS filesystem keys requested by each node group."
  value = {
    for key, group in local.node_groups : key => try(group.sfs_filesystem_keys, [])
  }
}

output "control_plane_private_endpoint" {
  description = "Private MK8s control-plane endpoint."
  value = try(
    nebius_mk8s_v1_cluster.this.status.control_plane.endpoints.private_endpoint,
    null
  )
}

output "control_plane_public_endpoint" {
  description = "Public MK8s control-plane endpoint when enabled."
  value = try(
    nebius_mk8s_v1_cluster.this.status.control_plane.endpoints.public_endpoint,
    null
  )
}

output "cluster_ca_certificate" {
  description = "PEM-encoded cluster CA certificate."
  value = try(
    nebius_mk8s_v1_cluster.this.status.control_plane.auth.cluster_ca_certificate,
    null
  )
  sensitive = true
}
