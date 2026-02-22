output "bucket_ids" {
  description = "Created bucket IDs keyed by logical bucket key."
  value = merge(
    { for key, bucket in nebius_storage_v1_bucket.protected : key => bucket.id },
    { for key, bucket in nebius_storage_v1_bucket.unprotected : key => bucket.id },
  )
}

output "bucket_names" {
  description = "Created bucket names keyed by logical bucket key."
  value = merge(
    { for key, bucket in nebius_storage_v1_bucket.protected : key => bucket.name },
    { for key, bucket in nebius_storage_v1_bucket.unprotected : key => bucket.name },
  )
}
