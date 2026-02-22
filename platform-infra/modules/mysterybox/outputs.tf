output "secret_ids" {
  description = "Created MysteryBox secret IDs keyed by logical secret ID."
  value = {
    for secret_id, secret in nebius_mysterybox_v1_secret.this :
    secret_id => secret.id
  }
}

output "secret_names" {
  description = "Created MysteryBox secret names keyed by logical secret ID."
  value = {
    for secret_id, secret in nebius_mysterybox_v1_secret.this :
    secret_id => secret.name
  }
}

output "secret_version_ids" {
  description = "Created MysteryBox secret-version IDs keyed by logical secret ID."
  value = {
    for secret_id, version in nebius_mysterybox_v1_secret_version.this :
    secret_id => version.id
  }
}
