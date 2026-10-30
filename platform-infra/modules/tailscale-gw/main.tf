locals {
  members   = toset(slice(["a", "b"], 0, var.replicas))
  labels    = merge(var.labels, { "cxcli-component" = "tailscale-gw" })
  bootstrap = var.bootstrap_user_data == null ? {} : jsondecode(var.bootstrap_user_data)
}

resource "nebius_vpc_v1_security_group" "gateway" {
  parent_id  = var.parent_id
  network_id = var.network_id
  name       = "${var.name}-management"
  labels     = local.labels
}
resource "nebius_vpc_v1_security_rule" "ssh" {
  for_each  = toset(var.management_cidrs)
  parent_id = nebius_vpc_v1_security_group.gateway.id
  name      = "ssh-${substr(sha256(each.key), 0, 12)}"
  protocol  = "TCP"
  access    = "ALLOW"
  type      = "STATEFUL"
  priority  = 100
  ingress   = { source_cidrs = [each.key], destination_ports = [22] }
}
resource "nebius_vpc_v1_security_rule" "tailscale" {
  parent_id = nebius_vpc_v1_security_group.gateway.id
  name      = "tailscale-direct"
  protocol  = "UDP"
  access    = "ALLOW"
  type      = "STATEFUL"
  priority  = 100
  ingress   = { source_cidrs = ["0.0.0.0/0"], destination_ports = [41641] }
}
resource "nebius_vpc_v1_security_rule" "icmp" {
  parent_id = nebius_vpc_v1_security_group.gateway.id
  name      = "icmp-path-mtu"
  protocol  = "ICMP"
  access    = "ALLOW"
  type      = "STATEFUL"
  priority  = 100
  ingress   = { source_cidrs = ["0.0.0.0/0"] }
}
resource "nebius_vpc_v1_security_rule" "egress" {
  parent_id = nebius_vpc_v1_security_group.gateway.id
  name      = "outbound"
  protocol  = "ANY"
  access    = "ALLOW"
  type      = "STATEFUL"
  priority  = 100
  egress    = { destination_cidrs = ["0.0.0.0/0"] }
}
resource "nebius_vpc_v1_allocation" "public" {
  for_each    = local.members
  parent_id   = var.parent_id
  name        = "${var.name}-${each.key}-public"
  ipv4_public = { subnet_id = var.subnet_id }
  labels      = merge(local.labels, { "cxcli-member" = each.key })
}
module "member" {
  for_each = local.members
  source   = "../vm"

  parent_id               = var.parent_id
  network_id              = var.network_id
  subnet_id               = var.subnet_id
  name                    = "${var.name}-${each.key}"
  platform                = coalesce(try(var.member_overrides[each.key].platform, null), var.platform)
  preset                  = coalesce(try(var.member_overrides[each.key].preset, null), var.preset)
  source_image_family     = coalesce(try(var.member_overrides[each.key].source_image_family, null), var.source_image_family)
  boot_disk_size_gib      = coalesce(try(var.member_overrides[each.key].boot_disk_size_gib, null), var.boot_disk_size_gib)
  boot_disk_type          = var.boot_disk_type
  ssh_user_name           = var.ssh_user_name
  ssh_public_key          = var.ssh_public_key
  public_ip_mode          = "allocation"
  public_ip_allocation_id = nebius_vpc_v1_allocation.public[each.key].id
  security_group_ids      = [nebius_vpc_v1_security_group.gateway.id]
  recovery_policy         = "RECOVER"
  labels                  = merge(local.labels, { "cxcli-member" = each.key })
  cloud_init_user_data_override = lookup(local.bootstrap, each.key, templatefile("${path.module}/cloud-init.tftpl", {
    ssh_user      = var.ssh_user_name
    ssh_key       = var.ssh_public_key
    host_keys     = lookup(var.ssh_host_keys, each.key, {})
    version       = var.tailscale_version
    helper        = base64gzip(file("${path.module}/tailscale_guest.py"))
    member_config = jsonencode({ schema = 1, project = var.parent_id, name = var.name, member = each.key })
  }))
  depends_on = [nebius_vpc_v1_security_rule.ssh, nebius_vpc_v1_security_rule.tailscale, nebius_vpc_v1_security_rule.icmp, nebius_vpc_v1_security_rule.egress]
}
