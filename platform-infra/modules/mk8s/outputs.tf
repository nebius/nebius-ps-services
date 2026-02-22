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
