output "filesystem_id" {
  description = "SFS ID."
  value       = try(one(nebius_compute_v1_filesystem.this[*].id), null)
}

output "size_bytes" {
  description = "SFS size in bytes."
  value       = try(one(nebius_compute_v1_filesystem.this[*].status.size_bytes), null)
}
