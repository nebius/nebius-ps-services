data "nebius_vpc_v1_subnet" "mk8s" {
  id = var.subnet_id
}

module "mk8s" {
  source = "../../modules/mk8s"

  parent_id                    = var.parent_id
  cluster_name                 = var.cluster_name
  subnet_id                    = var.subnet_id
  k8s_version                  = var.k8s_version
  etcd_cluster_size            = var.etcd_cluster_size
  mk8s_cluster_public_endpoint = var.mk8s_cluster_public_endpoint

  ssh_user_name  = var.ssh_user_name
  ssh_public_key = var.ssh_public_key

  cpu_nodes_count       = var.cpu_nodes_count
  cpu_nodes_platform    = var.cpu_nodes_platform
  cpu_nodes_preset      = var.cpu_nodes_preset
  cpu_nodes_preemptible = var.cpu_nodes_preemptible
  cpu_nodes_public_ips  = var.cpu_nodes_public_ips

  gpu_enabled               = var.gpu_enabled
  gpu_node_groups           = var.gpu_node_groups
  gpu_nodes_count_per_group = var.gpu_nodes_count_per_group
  gpu_nodes_platform        = var.gpu_nodes_platform
  gpu_nodes_preset          = var.gpu_nodes_preset
  gpu_nodes_preemptible     = var.gpu_nodes_preemptible
  gpu_nodes_public_ips      = var.gpu_nodes_public_ips
  gpu_driverfull_image      = var.gpu_driverfull_image
  infiniband_fabric         = var.infiniband_fabric

  mig_strategy      = var.mig_strategy
  mig_parted_config = var.mig_parted_config

  mk8s_cluster_overrides        = var.mk8s_cluster_overrides
  mk8s_cpu_node_group_overrides = var.mk8s_cpu_node_group_overrides
  mk8s_gpu_node_group_overrides = var.mk8s_gpu_node_group_overrides
}

module "managed_postgresql" {
  source = "../../modules/managed-postgresql"

  enabled            = var.managed_postgresql_enabled
  parent_id          = var.parent_id
  network_id         = data.nebius_vpc_v1_subnet.mk8s.network_id
  name               = var.managed_postgresql_name
  tier               = var.managed_postgresql_tier
  storage_gib        = var.managed_postgresql_storage_gib
  postgresql_version = var.managed_postgresql_postgresql_version
  public_access      = var.managed_postgresql_public_access
}

module "sfs" {
  source = "../../modules/sfs"

  enabled        = var.sfs_enabled
  parent_id      = var.parent_id
  name           = var.sfs_name
  size_gib       = var.sfs_size_gib
  block_size_kib = var.sfs_block_size_kib
  type           = var.sfs_type
}

locals {
  object_storage_buckets = merge(
    var.state_bucket_manage ? {
      state = {
        name                 = var.state_bucket_name
        versioning_policy    = var.state_bucket_versioning_policy
        object_audit_logging = var.state_bucket_object_audit_logging
        protect_from_destroy = var.state_bucket_protect_from_destroy
      }
    } : {},
    var.inventory_bucket_manage ? {
      inventory = {
        name                 = var.inventory_bucket_name
        versioning_policy    = var.inventory_bucket_versioning_policy
        object_audit_logging = var.inventory_bucket_object_audit_logging
        protect_from_destroy = var.inventory_bucket_protect_from_destroy
      }
    } : {}
  )

  mysterybox_secrets_by_id = {
    for secret in var.mysterybox_secrets :
    secret.id => {
      name                = secret.name
      description         = try(secret.description, null)
      version_description = try(secret.version_description, null)
      labels = merge(
        try(secret.labels, {}),
        {
          "nebius-cxcli.io/scope"     = try(secret.scope, "platform")
          "nebius-cxcli.io/secret-id" = secret.id
        }
      )
      set_primary  = try(secret.set_primary, true)
      payload_keys = secret.payload_keys
    }
  }
}

module "object_storage" {
  source = "../../modules/object-storage"

  parent_id = var.parent_id
  buckets   = local.object_storage_buckets
}

module "mysterybox" {
  count  = var.mysterybox_enabled ? 1 : 0
  source = "../../modules/mysterybox"

  parent_id     = var.parent_id
  secrets       = local.mysterybox_secrets_by_id
  secret_values = var.mysterybox_secret_values
}

module "wireguard_jumphost" {
  count  = var.wireguard_enabled ? 1 : 0
  source = "../../modules/wireguard-jumphost"

  parent_id = var.parent_id
  region    = var.region
  subnet_id = var.subnet_id

  name           = var.wireguard_name
  ssh_user_name  = var.ssh_user_name
  ssh_public_key = var.ssh_public_key

  platform = var.wireguard_platform
  preset   = var.wireguard_preset

  create_public_ip_allocation = var.wireguard_create_public_ip_allocation
  public_ip_allocation_id     = var.wireguard_public_ip_allocation_id
  public_ip_allocation_name   = var.wireguard_public_ip_allocation_name

  boot_disk_size_gib         = var.wireguard_boot_disk_size_gib
  boot_disk_block_size_bytes = var.wireguard_boot_disk_block_size_bytes
  boot_disk_type             = var.wireguard_boot_disk_type
  source_image_family        = var.wireguard_source_image_family
  wireguard_tunnel_cidr      = var.wireguard_tunnel_cidr
  wireguard_listen_port      = var.wireguard_listen_port
  nat_mode                   = var.wireguard_nat_mode
  endpoint_host              = var.wireguard_endpoint_host
  clients                    = var.wireguard_clients
}

module "ssh_jumphost" {
  count  = var.ssh_jumphost_enabled ? 1 : 0
  source = "../../modules/ssh-jumphost"

  parent_id = var.parent_id
  region    = var.region
  subnet_id = var.subnet_id

  name           = var.ssh_jumphost_name
  ssh_user_name  = var.ssh_user_name
  ssh_public_key = var.ssh_public_key

  platform = var.ssh_jumphost_platform
  preset   = var.ssh_jumphost_preset

  allowed_cidrs = var.ssh_jumphost_allowed_cidrs

  create_public_ip_allocation = var.ssh_jumphost_create_public_ip_allocation
  public_ip_allocation_id     = var.ssh_jumphost_public_ip_allocation_id
  public_ip_allocation_name   = var.ssh_jumphost_public_ip_allocation_name

  boot_disk_size_gib         = var.ssh_jumphost_boot_disk_size_gib
  boot_disk_block_size_bytes = var.ssh_jumphost_boot_disk_block_size_bytes
  boot_disk_type             = var.ssh_jumphost_boot_disk_type
  source_image_family        = var.ssh_jumphost_source_image_family
}
