resource "nebius_compute_v1_disk" "ssh_jumphost_boot_disk" {
  parent_id = var.parent_id
  name      = "${var.name}-boot-disk"

  block_size_bytes = var.boot_disk_block_size_bytes
  size_gibibytes   = var.boot_disk_size_gib
  type             = var.boot_disk_type

  source_image_family = {
    image_family = var.source_image_family
  }

  labels = var.labels
}

resource "nebius_vpc_v1_allocation" "ssh_jumphost_public" {
  count = var.create_public_ip_allocation && var.public_ip_allocation_id == null ? 1 : 0

  parent_id = var.parent_id
  name      = local.effective_public_ip_allocation_name

  ipv4_public = {
    subnet_id = var.subnet_id
  }

  labels = var.labels
}

resource "nebius_compute_v1_instance" "ssh_jumphost" {
  parent_id = var.parent_id
  name      = var.name

  boot_disk = {
    attach_mode   = "READ_WRITE"
    existing_disk = nebius_compute_v1_disk.ssh_jumphost_boot_disk
  }

  network_interfaces = [
    {
      name              = "eth0"
      subnet_id         = var.subnet_id
      ip_address        = {}
      public_ip_address = local.public_ip_address
    }
  ]

  resources = {
    platform = local.effective_platform
    preset   = local.effective_preset
  }

  cloud_init_user_data = templatefile("${path.module}/ssh-jumphost-cloud-init.tftpl", {
    ssh_user_name  = var.ssh_user_name
    ssh_public_key = var.ssh_public_key
    allowed_cidrs  = var.allowed_cidrs
  })

  labels = var.labels
}
