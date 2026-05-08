output "cluster_id" {
  description = "MK8s cluster ID."
  value       = nebius_mk8s_v1_cluster.this.id
}

output "cluster_name" {
  description = "MK8s cluster name."
  value       = nebius_mk8s_v1_cluster.this.name
}

output "cpu_node_group_ids" {
  description = "CPU node group IDs."
  value       = [for item in nebius_mk8s_v1_node_group.cpu : item.id]
}

output "gpu_node_group_ids" {
  description = "GPU node group IDs."
  value       = [for item in nebius_mk8s_v1_node_group.gpu : item.id]
}

output "generic_node_group_ids" {
  description = "Generic named node group IDs keyed by node_groups key."
  value       = { for key, item in nebius_mk8s_v1_node_group.generic : key => item.id }
}

output "node_group_ids" {
  description = "All node group IDs keyed by logical node group name."
  value = merge(
    length(nebius_mk8s_v1_node_group.cpu) > 0 ? {
      cpu = nebius_mk8s_v1_node_group.cpu[0].id
    } : {},
    {
      for index, item in nebius_mk8s_v1_node_group.gpu :
      "gpu-${index}" => item.id
    },
    {
      for key, item in nebius_mk8s_v1_node_group.generic :
      key => item.id
    }
  )
}

output "generic_gpu_cluster_ids" {
  description = "Generic GPU cluster IDs keyed by gpu_clusters key."
  value       = { for key, item in nebius_compute_v1_gpu_cluster.generic : key => item.id }
}

output "gpu_cluster_ids" {
  description = "All GPU cluster IDs keyed by logical source."
  value = merge(
    length(nebius_compute_v1_gpu_cluster.this) > 0 ? {
      default = nebius_compute_v1_gpu_cluster.this[0].id
    } : {},
    {
      for key, item in nebius_compute_v1_gpu_cluster.generic :
      key => item.id
    }
  )
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
