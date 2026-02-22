output "cluster_id" {
  description = "Managed PostgreSQL cluster ID."
  value       = try(one(nebius_msp_postgresql_v1alpha1_cluster.this[*].id), null)
}

output "private_read_write_endpoint" {
  description = "Private read/write endpoint."
  value = try(
    one(nebius_msp_postgresql_v1alpha1_cluster.this[*].status.connection_endpoints.private_read_write),
    null
  )
}
