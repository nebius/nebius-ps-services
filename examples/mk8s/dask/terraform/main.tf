locals {
  mount_point = "/mnt/data-${var.run_version}"
  mount_tag   = "data-${var.run_version}"
  user_data = <<EOF
#cloud-config
users:
  - name: ${var.ssh_user_name}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${var.ssh_public_key}
runcmd:
  - mkdir -p ${local.mount_point}
  - mount -t virtiofs ${local.mount_tag} ${local.mount_point}
  - printf "%s %s virtiofs defaults,nofail 0 2\n" "${local.mount_tag}" "${local.mount_point}" | tee -a /etc/fstab
EOF
}

resource "nebius_registry_v1_registry" "mda_registry" {
  name = join("-", ["mda-registry", var.run_version])
  parent_id = var.parent_id
}

data "nebius_iam_v1_group" "admins" {
  count = 1
  name = "admins"
  parent_id = var.tenant_id
}

resource "nebius_iam_v1_service_account" "mda_sa" {
  count = 1
  name = join("-", ["mda-sa", var.run_version])
  parent_id = var.parent_id
}

resource "nebius_iam_v1_group_membership" "mda_sa_admin" {
  count = 1
  parent_id = data.nebius_iam_v1_group.admins[0].id
  member_id = nebius_iam_v1_service_account.mda_sa[count.index].id
}

resource "nebius_mk8s_v1_cluster" "mda_mk8s" {
  name = join("-", ["mda-mk8s", var.run_version])
  parent_id = var.parent_id
  control_plane = {
    endpoints = {
      public_endpoint = {}
    }
    subnet_id = var.subnet_id
    version = "1.32"
  }
}

resource "nebius_compute_v1_filesystem" "mda_filesystem" {
  name = join("-", ["mda-filesystem", var.run_version])
  parent_id = var.parent_id
  size_gibibytes = 64
  type = "NETWORK_SSD"
  block_size_bytes = 4096
}

resource "nebius_mk8s_v1_node_group" "cpu_node_group" {
  fixed_node_count = 5
  name = join("-", ["cpu-node-group", var.run_version])
  parent_id = nebius_mk8s_v1_cluster.mda_mk8s.id
  version = "1.32"
  template = {
    boot_disk = {
      size_gibibytes = 64
      type = "NETWORK_SSD"
    }
    service_account_id = nebius_iam_v1_service_account.mda_sa[0].id
    network_interfaces = [
      {
        public_ip_address = {}
        subnet_id = var.subnet_id
      }
    ]
    resources = {
      platform = "cpu-d3"
      preset = "4vcpu-16gb"
    }
    filesystems = [
      {
        existing_filesystem = {
          id = nebius_compute_v1_filesystem.mda_filesystem.id
        }
      attach_mode = "READ_WRITE"
      mount_tag = local.mount_tag
      }
    ]
    cloud_init_user_data = local.user_data
  }
}

output "mount_tag" {
  value = "data-${var.run_version}"
}

output "registry_id" {
  value = nebius_registry_v1_registry.mda_registry.id
}

output "nb_cluster_id" {
  value = nebius_mk8s_v1_cluster.mda_mk8s.id
}
