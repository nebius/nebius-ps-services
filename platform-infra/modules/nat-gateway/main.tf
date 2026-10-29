data "nebius_vpc_v1_network" "selected" {
  id = var.network_id
}
locals {
  create_subnet          = var.gateway_subnet_mode == "create"
  subnet_id              = local.create_subnet ? nebius_vpc_v1_subnet.gateway[0].id : data.nebius_vpc_v1_subnet.gateway[0].id
  gateway_route_table_id = local.create_subnet ? nebius_vpc_v1_route_table.gateway[0].id : data.nebius_vpc_v1_subnet.gateway[0].status.route_table.id
  labels                 = merge(var.labels, { "cxcli-component" = "nat-gateway" })
  preserved_routes = merge({}, [for policy, routes in var.routing_policies : {
    for route in routes : "${policy}:${route.destination_cidr}" => merge(route, { policy = policy })
  }]...)
  guest_config = jsonencode({
    source_cidrs            = var.source_cidrs
    security_profile        = "key-only-fail2ban-v1"
    ssh_user_name           = var.ssh_user_name
    ssh_public_key          = var.ssh_public_key
    guest_program_sha256    = sha256(trimspace(file("${path.module}/nat_guest.py")))
    security_program_sha256 = sha256(trimspace(file("${path.module}/nat_security.py")))
  })
}
resource "nebius_vpc_v1_route_table" "gateway" {
  count      = local.create_subnet ? 1 : 0
  parent_id  = var.parent_id
  name       = "${var.name}-egress"
  network_id = var.network_id
  labels     = local.labels
}
resource "nebius_vpc_v1_route" "gateway_egress" {
  count       = local.create_subnet ? 1 : 0
  parent_id   = nebius_vpc_v1_route_table.gateway[0].id
  name        = "${var.name}-direct-egress"
  destination = { cidr = "0.0.0.0/0" }
  next_hop    = { default_egress_gateway = true }
  labels      = local.labels
}
resource "nebius_vpc_v1_subnet" "gateway" {
  count          = local.create_subnet ? 1 : 0
  parent_id      = var.parent_id
  name           = "${var.name}-subnet"
  network_id     = var.network_id
  route_table_id = nebius_vpc_v1_route_table.gateway[0].id
  ipv4_private_pools = {
    use_network_pools = false
    pools             = [{ cidrs = [{ cidr = var.gateway_subnet_cidr }] }]
  }
  ipv4_public_pools = { use_network_pools = true }
  labels            = local.labels
  lifecycle {
    precondition {
      condition     = data.nebius_vpc_v1_network.selected.parent_id == var.parent_id
      error_message = "The gateway network must belong to parent_id."
    }
  }
}
data "nebius_vpc_v1_subnet" "gateway" {
  count = local.create_subnet ? 0 : 1
  id    = var.gateway_subnet_id
  lifecycle {
    postcondition {
      condition     = self.parent_id == var.parent_id && self.network_id == var.network_id
      error_message = "The reused gateway subnet must belong to the selected project and network."
    }
    postcondition {
      condition     = length(self.status.ipv4_private_pools) > 0 && length(self.status.ipv4_public_pools) > 0
      error_message = "The reused gateway subnet must support private and public allocations."
    }
  }
}

resource "nebius_vpc_v1_allocation" "private" {
  parent_id    = var.parent_id
  name         = "${var.name}-private"
  ipv4_private = { subnet_id = local.subnet_id }
  labels       = local.labels
}
resource "nebius_vpc_v1_allocation" "public" {
  parent_id   = var.parent_id
  name        = "${var.name}-public"
  ipv4_public = { subnet_id = local.subnet_id }
  labels      = local.labels
}
module "vm" {
  source                   = "../vm"
  parent_id                = var.parent_id
  network_id               = var.network_id
  subnet_id                = local.subnet_id
  name                     = var.name
  platform                 = var.platform
  preset                   = var.preset
  source_image_id          = var.source_image_id
  boot_disk_size_gib       = var.boot_disk_size_gib
  boot_disk_type           = var.boot_disk_type
  ssh_user_name            = var.ssh_user_name
  ssh_public_key           = var.ssh_public_key
  private_ip_allocation_id = nebius_vpc_v1_allocation.private.id
  public_ip_mode           = "allocation"
  public_ip_allocation_id  = nebius_vpc_v1_allocation.public.id
  labels                   = local.labels
  cloud_init_user_data_override = templatefile("${path.module}/cloud-init.tftpl", {
    ssh_user_name  = var.ssh_user_name
    ssh_public_key = var.ssh_public_key
    # Public field names preserve the old empty-map sensitivity; values stay secret.
    ssh_host_keys    = { for key in nonsensitive(keys(var.ssh_host_keys)) : key => var.ssh_host_keys[key] }
    guest_config     = local.guest_config
    guest_program    = file("${path.module}/nat_guest.py")
    security_program = file("${path.module}/nat_security.py")
  })
  depends_on = [nebius_vpc_v1_route.gateway_egress]
}
resource "nebius_vpc_v1_route_table" "workload" {
  for_each   = var.routing_policies
  parent_id  = var.parent_id
  name       = "${var.name}-${substr(sha256(each.key), 0, 16)}"
  network_id = var.network_id
  labels     = local.labels
}
resource "nebius_vpc_v1_route" "default" {
  for_each    = var.routing_policies
  parent_id   = nebius_vpc_v1_route_table.workload[each.key].id
  name        = "default-nat"
  destination = { cidr = "0.0.0.0/0" }
  next_hop    = { allocation = { id = nebius_vpc_v1_allocation.private.id } }
  labels      = local.labels
}
resource "nebius_vpc_v1_route" "preserved" {
  for_each    = local.preserved_routes
  parent_id   = nebius_vpc_v1_route_table.workload[each.value.policy].id
  name        = "preserved-${substr(sha256(each.value.destination_cidr), 0, 16)}"
  destination = { cidr = each.value.destination_cidr }
  next_hop    = { allocation = { id = each.value.allocation_id } }
  labels      = local.labels
}
