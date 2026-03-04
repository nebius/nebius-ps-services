resource "nebius_storage_v1_bucket" "protected" {
  for_each = local.protected_buckets

  parent_id = var.parent_id
  name      = each.value.name

  versioning_policy    = each.value.versioning_policy
  object_audit_logging = each.value.object_audit_logging
  labels               = each.value.labels

  lifecycle {
    prevent_destroy = true
  }
}

resource "nebius_storage_v1_bucket" "unprotected" {
  for_each = local.unprotected_buckets

  parent_id = var.parent_id
  name      = each.value.name

  versioning_policy    = each.value.versioning_policy
  object_audit_logging = each.value.object_audit_logging
  labels               = each.value.labels
}
