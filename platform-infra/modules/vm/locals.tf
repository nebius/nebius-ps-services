locals {
  create_boot_disk   = var.boot_disk_existing_id == null
  create_gpu_cluster = var.gpu_cluster_enabled && var.gpu_cluster_id == null && var.gpu_cluster_infiniband_fabric != null

  effective_boot_disk_id = (
    local.create_boot_disk
    ? nebius_compute_v1_disk.boot[0].id
    : var.boot_disk_existing_id
  )

  effective_gpu_cluster_name = coalesce(var.gpu_cluster_name, "${var.name}-gpu-cluster")
  effective_gpu_cluster_id = (
    !var.gpu_cluster_enabled
    ? null
    : (
      var.gpu_cluster_id != null
      ? var.gpu_cluster_id
      : nebius_compute_v1_gpu_cluster.vm[0].id
    )
  )

  data_disk_specs = {
    for disk in var.data_disks : disk.name => {
      name             = disk.name
      size_gib         = disk.size_gib
      type             = upper(try(disk.type, "NETWORK_SSD"))
      block_size_bytes = try(disk.block_size_bytes, 4096)
      attach_mode      = upper(try(disk.attach_mode, "READ_WRITE"))
      device_id        = try(disk.device_id, null)
      labels           = merge(var.labels, try(disk.labels, {}))
    }
  }

  existing_secondary_disk_attachments = [
    for disk in var.existing_data_disks : {
      attach_mode = upper(try(disk.attach_mode, "READ_WRITE"))
      device_id   = try(disk.device_id, null)
      existing_disk = {
        id = disk.id
      }
    }
  ]

  managed_secondary_disk_attachments = [
    for disk_name, disk in local.data_disk_specs : {
      attach_mode = disk.attach_mode
      device_id   = disk.device_id
      existing_disk = {
        id = nebius_compute_v1_disk.data[disk_name].id
      }
    }
  ]

  secondary_disk_attachments = concat(
    local.managed_secondary_disk_attachments,
    local.existing_secondary_disk_attachments,
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

  private_ip_address = (
    var.private_ip_allocation_id != null
    ? { allocation_id = var.private_ip_allocation_id }
    : {}
  )

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

  container_name = substr(replace("${var.name}-container", "_", "-"), 0, 63)
  cloud_init_user_data = templatefile("${path.module}/vm-cloud-init.tftpl", {
    ssh_user_name                                   = var.ssh_user_name
    ssh_public_key                                  = var.ssh_public_key
    vm_name                                         = var.name
    project_id                                      = var.parent_id
    container_enabled                               = var.container_enabled
    container_name                                  = local.container_name
    container_image                                 = var.container_image
    container_entrypoint                            = var.container_entrypoint
    container_args                                  = var.container_args
    container_env                                   = var.container_env
    container_ports                                 = var.container_ports
    container_mounts                                = var.container_mounts
    container_use_gpu                               = var.container_use_gpu
    container_restart_policy                        = lower(var.container_restart_policy)
    observability_collector_enabled                 = var.observability_collector_enabled
    observability_collector_region_id               = var.observability_collector_region_id
    observability_collector_package_name            = var.observability_collector_package_name
    observability_collector_package_version         = var.observability_collector_package_version
    observability_collector_apt_repository          = var.observability_collector_apt_repository
    observability_collector_apt_key_url             = var.observability_collector_apt_key_url
    observability_collector_apt_suite               = var.observability_collector_apt_suite
    observability_collector_apt_component           = var.observability_collector_apt_component
    observability_collector_apt_origin              = var.observability_collector_apt_origin
    observability_collector_prometheus_package_name = var.observability_collector_prometheus_package_name
    observability_collector_iam_token_file          = var.observability_collector_iam_token_file
    observability_collector_logs_enabled            = var.observability_collector_logs_enabled
    observability_collector_logs_systemd_units      = var.observability_collector_logs_systemd_units
    observability_collector_metrics_enabled         = var.observability_collector_metrics_enabled
    observability_collector_metrics_export_port     = var.observability_collector_metrics_export_port
    observability_collector_prometheus_agent_port   = var.observability_collector_prometheus_agent_port
  })
}
