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
