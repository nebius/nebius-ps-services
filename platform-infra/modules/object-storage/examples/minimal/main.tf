module "object_storage" {
  source = "../.."

  parent_id = "project-xxxxxxxx"
  name      = "tfstate-customer-123"

  versioning_policy    = "ENABLED"
  object_audit_logging = "ALL"
  protect_from_destroy = true
  labels = {
    purpose = "terraform-state"
  }
}
