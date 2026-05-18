resource "terraform_data" "nfs_export_contract" {
  input = sha256(jsonencode({
    data_disk_enabled = var.data_disk_enabled
    filesystems       = var.filesystems
  }))

  lifecycle {
    precondition {
      condition     = var.data_disk_enabled || length(var.filesystems) > 0
      error_message = "Enable data_disk_enabled or attach at least one filesystem before creating an NFS export."
    }
  }
}

module "vm" {
  source = "../vm"

  depends_on = [terraform_data.nfs_export_contract]

  parent_id             = var.parent_id
  subnet_id             = var.subnet_id
  name                  = var.name
  platform              = var.platform
  preset                = var.preset
  ssh_user_name         = var.ssh_user_name
  ssh_public_key        = var.ssh_public_key
  source_image_family   = var.source_image_family
  source_image_id       = var.source_image_id
  boot_disk_existing_id = var.boot_disk_existing_id

  boot_disk_size_gib            = var.boot_disk_size_gib
  boot_disk_block_size_bytes    = var.boot_disk_block_size_bytes
  boot_disk_type                = upper(var.boot_disk_type)
  boot_disk_encryption_enabled  = var.boot_disk_encryption_enabled
  boot_disk_deletion_protection = var.boot_disk_deletion_protection
  boot_disk_device_id           = var.boot_disk_device_id

  public_ip_mode           = var.public_ip_mode
  public_ip_allocation_id  = var.public_ip_allocation_id
  private_ip_allocation_id = var.private_ip_allocation_id
  security_group_ids       = var.security_group_ids

  data_disk_enabled             = var.data_disk_enabled
  data_disk_name                = local.data_disk_name
  data_disk_size_gib            = var.data_disk_size_gib
  data_disk_type                = upper(var.data_disk_type)
  data_disk_block_size_bytes    = var.data_disk_block_size_bytes
  data_disk_encryption_enabled  = var.data_disk_encryption_enabled
  data_disk_deletion_protection = var.data_disk_deletion_protection
  data_disk_attach_mode         = "READ_WRITE"
  data_disk_device_id           = var.data_disk_device_id
  data_disk_labels              = local.nfs_data_disk_labels

  filesystems = local.vm_filesystem_attachments
  labels      = local.effective_vm_labels

  cloud_init_user_data_override = local.cloud_init_user_data
}
