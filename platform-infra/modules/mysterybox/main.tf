resource "nebius_mysterybox_v1_secret" "this" {
  for_each = local.normalized_secrets

  parent_id   = var.parent_id
  name        = each.value.name
  description = each.value.description
  labels      = each.value.labels
}

resource "nebius_mysterybox_v1_secret_version" "this" {
  for_each = local.normalized_secrets

  parent_id   = nebius_mysterybox_v1_secret.this[each.key].id
  description = each.value.version_description
  set_primary = each.value.set_primary

  sensitive = {
    version = local.sensitive_versions[each.key]
    payload = local.payload_items[each.key]
  }

  lifecycle {
    precondition {
      condition     = length(local.missing_payload_entries) == 0
      error_message = "Missing MysteryBox payload values for: ${join(", ", local.missing_payload_entries)}. Provide values via TF_VAR_mysterybox_secret_values."
    }
  }
}
