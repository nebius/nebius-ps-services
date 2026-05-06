locals {
  normalized_secrets = {
    for secret in var.secrets :
    trimspace(secret.name) => {
      name        = trimspace(secret.name)
      description = try(secret.description, null)
      labels      = coalesce(try(secret.labels, null), {})
      version_id  = trimspace(coalesce(try(secret.version_id, null), "n/a"))
      payload = {
        for payload_key, payload in secret.payload :
        payload_key => {
          type = lower(coalesce(try(payload.type, null), "text"))
        }
      }
    }
  }

  configured_primary_version_ids = {
    for secret_name, secret in local.normalized_secrets :
    secret_name => secret.version_id
    if secret.version_id != "" && lower(secret.version_id) != "n/a"
  }

  # Non-secret value that is both non-empty text and valid RFC 4648 base64.
  omitted_payload_placeholder = "AA=="

  initial_secret_versions = {
    for secret_name, secret in local.normalized_secrets :
    secret_name => {
      secret_name = secret_name
      payload     = secret.payload
    }
  }

  version_payload_values = {
    for secret_name, version in local.initial_secret_versions :
    secret_name => {
      for payload_key in keys(version.payload) :
      payload_key => (
        trimspace(try(var.payload_values[secret_name][payload_key], "")) != ""
        ? var.payload_values[secret_name][payload_key]
        : (
          contains(keys(local.configured_primary_version_ids), secret_name)
          ? local.omitted_payload_placeholder
          : ""
        )
      )
    }
  }

  missing_payload_entries = nonsensitive(flatten([
    for secret_name, version in local.initial_secret_versions : [
      for payload_key in keys(version.payload) :
      "${secret_name}.${payload_key}" if !contains(keys(local.configured_primary_version_ids), secret_name) && (
        trimspace(try(var.payload_values[secret_name][payload_key], "")) == ""
      )
    ]
  ]))

  sensitive_versions = {
    for secret_name, version in local.initial_secret_versions :
    secret_name => sha256(jsonencode(version.payload))
  }

  payload_items = {
    for secret_name, payload in local.version_payload_values :
    secret_name => [
      for payload_key, payload_value in payload : {
        key          = payload_key
        string_value = local.initial_secret_versions[secret_name].payload[payload_key].type == "text" ? payload_value : null
        binary_value = local.initial_secret_versions[secret_name].payload[payload_key].type == "file" ? payload_value : null
      }
    ]
  }
}
