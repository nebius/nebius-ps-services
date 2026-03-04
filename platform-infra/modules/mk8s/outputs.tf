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
