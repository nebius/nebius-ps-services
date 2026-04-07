resource "nebius_storage_v1_bucket" "protected" {
  count = var.protect_from_destroy ? 1 : 0

  parent_id = var.parent_id
  name      = var.name

  versioning_policy    = var.versioning_policy
  object_audit_logging = var.object_audit_logging
  labels               = var.labels

  lifecycle {
    prevent_destroy = true
  }
}

resource "nebius_storage_v1_bucket" "unprotected" {
  count = var.protect_from_destroy ? 0 : 1

  parent_id = var.parent_id
  name      = var.name

  versioning_policy    = var.versioning_policy
  object_audit_logging = var.object_audit_logging
  labels               = var.labels
}
