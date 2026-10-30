data "nebius_vpc_v1_network" "selected" {
  id = var.network_id
}

data "nebius_vpc_v1_subnet" "selected" {
  id = var.subnet_id
}

resource "nebius_vpc_v1_allocation" "wireguard_public" {
  count = var.create_public_ip_allocation && var.public_ip_allocation_id == null ? 1 : 0

  parent_id = var.parent_id
  name      = local.effective_public_ip_allocation_name

  ipv4_public = {
    subnet_id = var.subnet_id
  }

  labels = local.effective_labels

  lifecycle {
    precondition {
      condition     = data.nebius_vpc_v1_network.selected.parent_id == var.parent_id
      error_message = "network_id must identify a VPC network in parent_id."
    }

    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected.parent_id == var.parent_id
      error_message = "subnet_id must identify a VPC subnet in parent_id."
    }

    precondition {
      condition     = data.nebius_vpc_v1_subnet.selected.network_id == var.network_id
      error_message = "subnet_id must belong to network_id."
    }
  }
}

module "vm" {
  source = "../vm"

  parent_id           = var.parent_id
  network_id          = var.network_id
  subnet_id           = var.subnet_id
  name                = var.name
  platform            = var.platform
  preset              = var.preset
  ssh_user_name       = var.ssh_user_name
  ssh_public_key      = var.access_bootstrap["login_public_key"]
  service_account_id  = var.access_bootstrap["service_account_id"]
  source_image_family = var.source_image_family

  boot_disk_size_gib            = var.boot_disk_size_gib
  boot_disk_block_size_bytes    = var.boot_disk_block_size_bytes
  boot_disk_type                = var.boot_disk_type
  boot_disk_encryption_enabled  = var.boot_disk_encryption_enabled
  boot_disk_deletion_protection = var.boot_disk_deletion_protection

  public_ip_mode          = "allocation"
  public_ip_allocation_id = local.effective_public_ip_allocation_id
  labels                  = local.effective_labels

  cloud_init_user_data_override = var.bootstrap_user_data != null ? var.bootstrap_user_data : local.cloud_init_user_data
}
