locals {
  single_filesystem_specs = length(var.filesystems) == 0 ? {
    default = {
      name            = var.name
      existing_id     = null
      size_gib        = var.size_gib
      block_size_kib  = var.block_size_kib
      type            = var.type
      mount_tag       = try(coalesce(var.mount_tag, var.name), null)
      forbid_deletion = var.forbid_deletion
    }
  } : {}

  mapped_filesystem_specs = {
    for key, filesystem in var.filesystems : key => {
      name            = coalesce(try(filesystem.name, null), key)
      existing_id     = try(filesystem.existing_id, null)
      size_gib        = try(filesystem.size_gib, null)
      block_size_kib  = coalesce(try(filesystem.block_size_kib, null), var.block_size_kib)
      type            = coalesce(try(filesystem.type, null), var.type)
      mount_tag       = coalesce(try(filesystem.mount_tag, null), key)
      forbid_deletion = coalesce(try(filesystem.forbid_deletion, null), var.forbid_deletion)
    }
  }

  filesystem_specs = length(var.filesystems) > 0 ? local.mapped_filesystem_specs : local.single_filesystem_specs

  managed_filesystems = {
    for key, filesystem in local.filesystem_specs : key => filesystem
    if filesystem.existing_id == null
  }

  existing_filesystems = {
    for key, filesystem in local.filesystem_specs : key => filesystem
    if filesystem.existing_id != null
  }

  filesystem_output = {
    for key, filesystem in local.filesystem_specs : key => {
      id = try(
        nebius_compute_v1_filesystem.this[key].id,
        data.nebius_compute_v1_filesystem.existing[key].id,
      )
      name = filesystem.name
      size_bytes = try(
        nebius_compute_v1_filesystem.this[key].status.size_bytes,
        data.nebius_compute_v1_filesystem.existing[key].status.size_bytes,
        filesystem.size_gib * 1024 * 1024 * 1024,
        null,
      )
      size_gib  = filesystem.size_gib
      mount_tag = filesystem.mount_tag
      type      = filesystem.type
    }
  }

  first_filesystem_key = try(keys(local.filesystem_output)[0], null)
}
