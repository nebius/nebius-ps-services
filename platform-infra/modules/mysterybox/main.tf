locals {
  normalized_secrets = {
    for secret_id, secret in var.secrets :
    secret_id => {
      name                = secret.name
      description         = try(secret.description, null)
      version_description = try(secret.version_description, null)
      labels              = try(secret.labels, {})
      set_primary         = try(secret.set_primary, true)
      payload_keys        = secret.payload_keys
    }
  }

  secret_payload_values = {
    for secret_id, secret in local.normalized_secrets :
    secret_id => {
      for payload_key in secret.payload_keys :
      payload_key => try(var.secret_values[secret_id][payload_key], null)
    }
  }

  missing_payload_entries = flatten([
    for secret_id, secret in local.normalized_secrets : [
      for payload_key in secret.payload_keys :
      "${secret_id}.${payload_key}" if(
        try(var.secret_values[secret_id][payload_key], null) == null ||
        trimspace(try(var.secret_values[secret_id][payload_key], "")) == ""
      )
    ]
  ])

  sensitive_versions = {
    for secret_id, payload in local.secret_payload_values :
    secret_id => sha256(jsonencode(payload))
  }

  payload_items = {
    for secret_id, payload in local.secret_payload_values :
    secret_id => [
      for payload_key, payload_value in payload : {
        key          = payload_key
        string_value = payload_value
      }
    ]
  }
}

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
