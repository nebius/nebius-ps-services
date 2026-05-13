output "bucket_id" {
  description = "Created bucket ID."
  value = (
    var.protect_from_destroy
    ? nebius_storage_v1_bucket.protected[0].id
    : nebius_storage_v1_bucket.unprotected[0].id
  )
}

output "bucket_name" {
  description = "Created bucket name."
  value = (
    var.protect_from_destroy
    ? nebius_storage_v1_bucket.protected[0].name
    : nebius_storage_v1_bucket.unprotected[0].name
  )
}

output "bucket_endpoint" {
  description = "HTTPS S3-compatible endpoint for the created bucket."
  value = format(
    "https://%s:443",
    var.protect_from_destroy
    ? nebius_storage_v1_bucket.protected[0].status.domain_name
    : nebius_storage_v1_bucket.unprotected[0].status.domain_name
  )
}
