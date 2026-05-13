resource "nebius_compute_v1_filesystem" "this" {
  for_each = local.managed_filesystems

  parent_id        = var.parent_id
  name             = each.value.name
  type             = each.value.type
  size_bytes       = each.value.size_gib * 1024 * 1024 * 1024
  block_size_bytes = each.value.block_size_kib * 1024
  forbid_deletion  = each.value.forbid_deletion

  lifecycle {
    precondition {
      condition     = length(trimspace(each.value.name)) > 0
      error_message = "filesystem name must be non-empty."
    }
    precondition {
      condition     = each.value.size_gib >= 1
      error_message = "filesystem size_gib must be >= 1."
    }
  }
}

data "nebius_compute_v1_filesystem" "existing" {
  for_each = local.existing_filesystems

  id = each.value.existing_id
}
