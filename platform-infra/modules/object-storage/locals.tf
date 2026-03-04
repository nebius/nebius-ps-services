locals {
  protected_buckets = {
    for key, bucket in var.buckets :
    key => bucket
    if bucket.protect_from_destroy
  }

  unprotected_buckets = {
    for key, bucket in var.buckets :
    key => bucket
    if !bucket.protect_from_destroy
  }
}
