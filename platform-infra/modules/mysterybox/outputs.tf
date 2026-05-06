output "secret_ids" {
  description = "Created MysteryBox secret IDs keyed by secret name."
  value = {
    for secret_name, secret in nebius_mysterybox_v1_secret.this :
    secret_name => secret.id
  }
}

output "secret_names" {
  description = "Created MysteryBox secret names keyed by secret name."
  value = {
    for secret_name, secret in nebius_mysterybox_v1_secret.this :
    secret_name => secret.name
  }
}

output "secret_version_ids" {
  description = "Created initial MysteryBox secret-version IDs keyed by secret name."
  value = {
    for secret_name, version in nebius_mysterybox_v1_secret_version.this :
    secret_name => version.id
  }
}

output "primary_secret_version_ids" {
  description = "Current primary MysteryBox secret-version IDs keyed by secret name."
  value = merge(
    {
      for secret_name, version in nebius_mysterybox_v1_secret_version.this :
      secret_name => version.id
    },
    local.configured_primary_version_ids,
  )
}
