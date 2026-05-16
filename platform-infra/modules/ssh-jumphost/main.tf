resource "nebius_vpc_v1_allocation" "ssh_jumphost_public" {
  count = var.create_public_ip_allocation && var.public_ip_allocation_id == null ? 1 : 0

  parent_id = var.parent_id
  name      = local.effective_public_ip_allocation_name

  ipv4_public = {
    subnet_id = var.subnet_id
  }

  labels = local.effective_labels
}

module "vm" {
  source = "../vm"

  parent_id           = var.parent_id
  subnet_id           = var.subnet_id
  name                = var.name
  platform            = var.platform
  preset              = var.preset
  ssh_user_name       = var.ssh_user_name
  ssh_public_key      = var.ssh_public_key
  source_image_family = var.source_image_family

  boot_disk_size_gib            = var.boot_disk_size_gib
  boot_disk_block_size_bytes    = var.boot_disk_block_size_bytes
  boot_disk_type                = var.boot_disk_type
  boot_disk_encryption_enabled  = var.boot_disk_encryption_enabled
  boot_disk_deletion_protection = var.boot_disk_deletion_protection

  public_ip_mode          = "allocation"
  public_ip_allocation_id = local.effective_public_ip_allocation_id
  labels                  = local.effective_labels

  cloud_init_user_data_override = templatefile("${path.module}/ssh-jumphost-cloud-init.tftpl", {
    ssh_user_name                = var.ssh_user_name
    ssh_public_key               = var.ssh_public_key
    bootstrap_allowed_cidrs_json = jsonencode(var.allowed_cidrs)
  })
}
