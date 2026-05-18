locals {
  data_disk_name            = coalesce(var.data_disk_name, "${var.name}-data")
  data_disk_filesystem_type = lower(var.data_disk_filesystem_type)
  effective_export_options = coalesce(var.export_options, [
    "rw",
    "sync",
    "no_subtree_check",
    "root_squash",
    "anonuid=${var.storage_uid}",
    "anongid=${var.storage_gid}",
  ])

  effective_vm_labels = merge(var.labels, {
    component = "nfs"
    name      = var.name
    role      = "nfs"
  })

  nfs_data_disk_labels = {
    role = "nfs-data"
  }

  vm_filesystem_attachments = [
    for filesystem in var.filesystems : {
      id          = filesystem.id
      mount_tag   = filesystem.mount_tag
      attach_mode = upper(try(filesystem.attach_mode, "READ_WRITE"))
    }
  ]

  cloud_init_user_data = templatefile("${path.module}/nfs-cloud-init.yaml.tftpl", {
    ssh_user_name        = var.ssh_user_name
    ssh_public_key       = trimspace(var.ssh_public_key)
    export_path          = var.export_path
    storage_uid          = var.storage_uid
    storage_gid          = var.storage_gid
    export_permissions   = var.export_permissions
    client_cidrs_json    = jsonencode(var.client_cidrs)
    export_options       = join(",", local.effective_export_options)
    filesystems_json     = jsonencode(var.filesystems)
    data_disk_enabled    = var.data_disk_enabled
    data_disk_device_id  = var.data_disk_device_id
    data_disk_filesystem = local.data_disk_filesystem_type
  })
}
