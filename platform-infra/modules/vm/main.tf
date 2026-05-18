resource "nebius_compute_v1_disk" "boot" {
  count = local.create_boot_disk ? 1 : 0

  parent_id = var.parent_id
  name      = "${var.name}-boot-disk"

  block_size_bytes = var.boot_disk_block_size_bytes
  size_gibibytes   = var.boot_disk_size_gib
  type             = var.boot_disk_type
  disk_encryption = var.boot_disk_encryption_enabled ? {
    type = "DISK_ENCRYPTION_MANAGED"
  } : null
  forbid_deletion = var.boot_disk_deletion_protection
  source_image_id = var.source_image_id
  source_image_family = var.source_image_id == null ? {
    image_family = var.source_image_family
  } : null

  labels = merge(var.labels, { role = "boot" })

  lifecycle {
    precondition {
      condition     = var.boot_disk_size_gib != null
      error_message = "boot_disk_size_gib must be set when the module creates the boot disk."
    }

    precondition {
      condition = (
        !var.boot_disk_encryption_enabled ||
        contains(["NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"], upper(var.boot_disk_type))
      )
      error_message = "boot_disk_encryption_enabled can be true only for NETWORK_SSD_NON_REPLICATED or NETWORK_SSD_IO_M3 disks."
    }

    replace_triggered_by = [
      terraform_data.cloud_init_user_data_revision,
    ]
  }
}

resource "nebius_compute_v1_disk" "data" {
  for_each = local.data_disk_specs

  depends_on = [terraform_data.data_disk_contract]

  parent_id = var.parent_id
  name      = each.value.name

  block_size_bytes = each.value.block_size_bytes
  size_gibibytes   = each.value.size_gib
  type             = each.value.type
  disk_encryption = each.value.encryption_enabled ? {
    type = "DISK_ENCRYPTION_MANAGED"
  } : null
  forbid_deletion = each.value.deletion_protection

  labels = each.value.labels

  lifecycle {
    precondition {
      condition = (
        !each.value.encryption_enabled ||
        contains(["NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"], each.value.type)
      )
      error_message = "data_disks encryption_enabled can be true only for NETWORK_SSD_NON_REPLICATED or NETWORK_SSD_IO_M3 disks."
    }
  }
}

resource "terraform_data" "data_disk_contract" {
  input = sha256(jsonencode(local.data_disk_names))

  lifecycle {
    precondition {
      condition     = length(local.data_disk_names) == length(toset(local.data_disk_names))
      error_message = "The guided secondary data disk name and data_disks names must be unique."
    }
  }
}

resource "nebius_compute_v1_gpu_cluster" "vm" {
  count = local.create_gpu_cluster ? 1 : 0

  parent_id         = var.parent_id
  name              = local.effective_gpu_cluster_name
  infiniband_fabric = var.gpu_cluster_infiniband_fabric

  labels = var.labels
}

resource "terraform_data" "cloud_init_user_data_revision" {
  input = sha256(local.cloud_init_user_data)
}

resource "nebius_compute_v1_instance" "vm" {
  parent_id = var.parent_id
  name      = var.name

  boot_disk = {
    attach_mode = "READ_WRITE"
    device_id   = var.boot_disk_device_id
    existing_disk = {
      id = local.effective_boot_disk_id
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

  recovery_policy = upper(var.recovery_policy)
  preemptible = var.preemptible_enabled ? {
    on_preemption = "STOP"
    priority      = var.preemptible_priority
  } : null
  gpu_cluster = local.effective_gpu_cluster_id != null ? {
    id = local.effective_gpu_cluster_id
  } : null

  secondary_disks      = local.secondary_disk_attachments
  filesystems          = local.filesystem_attachments
  cloud_init_user_data = local.cloud_init_user_data
  hostname             = var.hostname
  service_account_id   = var.service_account_id
  stopped              = var.stopped

  labels = var.labels

  lifecycle {
    ignore_changes = [
      cloud_init_user_data,
    ]

    replace_triggered_by = [
      terraform_data.cloud_init_user_data_revision,
    ]

    precondition {
      condition = !(
        var.boot_disk_existing_id == null &&
        var.source_image_id == null &&
        var.source_image_family == null
      )
      error_message = "Set source_image_family when the module creates the boot disk, unless you supply source_image_id or boot_disk_existing_id."
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
        var.boot_disk_existing_id != null &&
        (
          var.source_image_id != null ||
          var.source_image_family != null
        )
      )
      error_message = "boot_disk_existing_id cannot be combined with source_image_id or source_image_family."
    }

    precondition {
      condition = !(
        var.boot_disk_existing_id != null &&
        (
          var.boot_disk_encryption_enabled ||
          var.boot_disk_deletion_protection
        )
      )
      error_message = "boot_disk_encryption_enabled and boot_disk_deletion_protection apply only when this module creates the boot disk."
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
      condition = !(
        var.preemptible_enabled &&
        !startswith(lower(var.platform), "gpu-")
      )
      error_message = "preemptible_enabled=true requires a GPU platform because Nebius only supports preemptible VMs on GPU platforms."
    }

    precondition {
      condition = !(
        var.preemptible_enabled &&
        upper(var.recovery_policy) != "FAIL"
      )
      error_message = "preemptible_enabled=true requires recovery_policy=FAIL."
    }

    precondition {
      condition = !(
        var.gpu_cluster_enabled &&
        !startswith(lower(var.platform), "gpu-")
      )
      error_message = "gpu_cluster_enabled=true requires a GPU platform."
    }

    precondition {
      condition = !(
        var.gpu_cluster_enabled &&
        !startswith(lower(var.preset), "8gpu-")
      )
      error_message = "gpu_cluster_enabled=true requires an 8-GPU preset because Nebius GPU clusters are used with 8-GPU VM shapes."
    }

    precondition {
      condition = !(
        var.gpu_cluster_enabled &&
        (
          (var.gpu_cluster_id == null && var.gpu_cluster_infiniband_fabric == null) ||
          (var.gpu_cluster_id != null && var.gpu_cluster_infiniband_fabric != null)
        )
      )
      error_message = "gpu_cluster_enabled=true requires exactly one of gpu_cluster_id or gpu_cluster_infiniband_fabric."
    }

    precondition {
      condition = !(
        !var.gpu_cluster_enabled &&
        (
          var.gpu_cluster_id != null ||
          var.gpu_cluster_infiniband_fabric != null ||
          var.gpu_cluster_name != null
        )
      )
      error_message = "Set gpu_cluster_enabled=true before configuring GPU cluster inputs."
    }

    precondition {
      condition = !(
        var.container_enabled &&
        var.container_image == null
      )
      error_message = "container_enabled=true requires container_image."
    }

    precondition {
      condition = !(
        var.container_enabled &&
        var.preemptible_enabled
      )
      error_message = "container_enabled=true requires a regular VM. Nebius containers-over-VM shapes are regular only."
    }

    precondition {
      condition = !(
        var.container_use_gpu &&
        !startswith(lower(var.platform), "gpu-")
      )
      error_message = "container_use_gpu=true requires a GPU platform."
    }

    precondition {
      condition = !(
        var.container_enabled &&
        var.boot_disk_existing_id == null &&
        var.source_image_id == null &&
        !strcontains(lower(var.source_image_family), "ubuntu")
      )
      error_message = "Container bootstrap expects an Ubuntu-based image when the module creates the boot disk; set source_image_family to an Ubuntu image or supply an existing boot disk/image ID."
    }
  }
}
