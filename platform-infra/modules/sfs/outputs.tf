output "filesystem_id" {
  description = "Primary SFS ID for single-filesystem callers."
  value = (
    local.first_filesystem_key == null
    ? null
    : local.filesystem_output[local.first_filesystem_key].id
  )
}

output "size_bytes" {
  description = "Primary SFS size in bytes for single-filesystem callers."
  value = (
    local.first_filesystem_key == null
    ? null
    : local.filesystem_output[local.first_filesystem_key].size_bytes
  )
}

output "mount_tag" {
  description = "Primary SFS mount tag for single-filesystem callers."
  value = (
    local.first_filesystem_key == null
    ? null
    : local.filesystem_output[local.first_filesystem_key].mount_tag
  )
}

output "filesystems" {
  description = "Named filesystem metadata keyed by input map key."
  value       = local.filesystem_output
}
