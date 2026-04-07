resource "nebius_compute_v1_filesystem" "this" {
  parent_id = var.parent_id
  name      = var.name
  type      = var.type

  size_bytes       = var.size_gib * 1024 * 1024 * 1024
  block_size_bytes = var.block_size_kib * 1024

  lifecycle {
    precondition {
      condition     = length(trimspace(var.name)) > 0
      error_message = "name must be non-empty."
    }
    precondition {
      condition     = var.size_gib >= 1
      error_message = "size_gib must be >= 1."
    }
  }
}
