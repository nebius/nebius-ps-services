resource "nebius_compute_v1_disk" "boot" {
  parent_id = var.parent_id
  name      = "${var.name}-boot"

  block_size_bytes = var.boot_disk_block_size_bytes
  size_gibibytes   = var.boot_disk_size_gib
  type             = var.boot_disk_type
  source_image_id  = var.source_image_id
  source_image_family = var.source_image_id == null ? {
    image_family = var.source_image_family
  } : null

  labels = merge(var.labels, { role = "boot" })
}

resource "nebius_compute_v1_disk" "data" {
  count = local.create_data_disk ? 1 : 0

  parent_id = var.parent_id
  name      = local.data_disk_name

  block_size_bytes = try(var.data_disk.block_size_bytes, 4096)
  size_gibibytes   = try(var.data_disk.size_gib, 128)
  type             = upper(try(var.data_disk.type, "NETWORK_SSD"))

  labels = merge(var.labels, { role = "nfs-data" })
}

resource "nebius_compute_v1_instance" "this" {
  parent_id = var.parent_id
  name      = var.name

  boot_disk = {
    attach_mode = "READ_WRITE"
    existing_disk = {
      id = nebius_compute_v1_disk.boot.id
    }
  }

  network_interfaces = [
    {
      name              = "eth0"
      subnet_id         = var.subnet_id
      ip_address        = local.private_ip_address
      public_ip_address = local.public_ip_address
      security_groups = [
        for group_id in var.security_group_ids : {
          id = group_id
        }
      ]
    }
  ]

  resources = {
    platform = var.platform
    preset   = var.preset
  }

  secondary_disks      = local.secondary_disk_attachments
  filesystems          = local.filesystem_attachments
  cloud_init_user_data = local.cloud_init_user_data

  labels = merge(var.labels, { role = "nfs" })

  lifecycle {
    precondition {
      condition = !(
        var.source_image_id == null &&
        var.source_image_family == null
      )
      error_message = "Set source_image_family unless you supply source_image_id."
    }
    precondition {
      condition = !(
        var.source_image_id != null &&
        var.source_image_family != null
      )
      error_message = "Set only one of source_image_id or source_image_family."
    }
    precondition {
      condition = !(
        lower(var.public_ip_mode) == "allocation" &&
        var.public_ip_allocation_id == null
      )
      error_message = "public_ip_mode=allocation requires public_ip_allocation_id."
    }
    precondition {
      condition = !(
        lower(var.public_ip_mode) != "allocation" &&
        var.public_ip_allocation_id != null
      )
      error_message = "public_ip_allocation_id can only be used when public_ip_mode=allocation."
    }
    precondition {
      condition = (
        local.create_data_disk ||
        length(var.filesystems) > 0
      )
      error_message = "Enable data_disk or attach at least one filesystem before creating an NFS export."
    }
  }
}
