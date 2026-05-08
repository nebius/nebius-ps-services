locals {
  create_data_disk = try(var.data_disk.enabled, true)

  data_disk_name = coalesce(try(var.data_disk.name, null), "${var.name}-data")

  data_disk_device_id       = try(var.data_disk.device_id, "nfs-data")
  data_disk_filesystem_type = lower(try(var.data_disk.filesystem_type, "ext4"))

  public_ip_address = (
    lower(var.public_ip_mode) == "none"
    ? null
    : (
      lower(var.public_ip_mode) == "dynamic"
      ? {}
      : (
        lower(var.public_ip_mode) == "static"
        ? { static = true }
        : { allocation_id = var.public_ip_allocation_id }
      )
    )
  )

  private_ip_address = (
    var.private_ip_allocation_id != null
    ? { allocation_id = var.private_ip_allocation_id }
    : {}
  )

  filesystem_attachments = [
    for filesystem in var.filesystems : {
      attach_mode = upper(try(filesystem.attach_mode, "READ_WRITE"))
      mount_tag   = filesystem.mount_tag
      existing_filesystem = {
        id = filesystem.id
      }
    }
  ]

  secondary_disk_attachments = local.create_data_disk ? [
    {
      attach_mode = "READ_WRITE"
      device_id   = local.data_disk_device_id
      existing_disk = {
        id = nebius_compute_v1_disk.data[0].id
      }
    }
  ] : []

  cloud_init_user_data = templatefile("${path.module}/nfs-cloud-init.yaml.tftpl", {
    ssh_user_name        = var.ssh_user_name
    ssh_public_key       = trimspace(var.ssh_public_key)
    export_path          = var.export_path
    client_cidrs_json    = jsonencode(var.client_cidrs)
    export_options       = join(",", var.export_options)
    filesystems_json     = jsonencode(var.filesystems)
    data_disk_enabled    = local.create_data_disk
    data_disk_device_id  = local.data_disk_device_id
    data_disk_filesystem = local.data_disk_filesystem_type
  })
}
