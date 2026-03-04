resource "random_password" "bootstrap" {
  count = var.enabled && var.bootstrap_user_password == null ? 1 : 0

  length  = 24
  special = false
  upper   = true
  lower   = true
}

resource "nebius_msp_postgresql_v1alpha1_cluster" "this" {
  count = var.enabled ? 1 : 0

  parent_id  = var.parent_id
  network_id = var.network_id
  name       = var.name

  config = {
    version       = var.postgresql_version
    public_access = var.public_access
    template = {
      disk = {
        size_gibibytes = var.storage_gib
        type           = local.selected_profile.disk_type
      }
      hosts = {
        count = local.selected_profile.hosts
      }
      resources = {
        platform = local.selected_profile.platform
        preset   = local.selected_profile.preset
      }
    }
  }

  bootstrap = {
    db_name       = var.bootstrap_db_name
    user_name     = var.bootstrap_user_name
    user_password = coalesce(var.bootstrap_user_password, one(random_password.bootstrap[*].result))
  }

  lifecycle {
    precondition {
      condition     = length(trimspace(var.name)) > 0
      error_message = "name must be set when managed-postgresql is enabled."
    }
  }
}
