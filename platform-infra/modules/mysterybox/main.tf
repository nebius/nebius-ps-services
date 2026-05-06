resource "nebius_mysterybox_v1_secret" "this" {
  for_each = local.normalized_secrets

  parent_id   = var.parent_id
  name        = each.value.name
  description = each.value.description
  labels      = each.value.labels
}

resource "nebius_mysterybox_v1_secret_version" "this" {
  for_each = local.initial_secret_versions

  parent_id   = nebius_mysterybox_v1_secret.this[each.value.secret_name].id
  set_primary = true

  sensitive = {
    version = local.sensitive_versions[each.key]
    payload = local.payload_items[each.key]
  }

  lifecycle {
    ignore_changes = [sensitive]

    precondition {
      condition     = length(local.missing_payload_entries) == 0
      error_message = "Missing MysteryBox payload values for: ${join(", ", local.missing_payload_entries)}. Provide a runtime Terraform variable mapped to payload_values, for example TF_VAR_mysterybox_payload_values in the caller root."
    }
  }
}
