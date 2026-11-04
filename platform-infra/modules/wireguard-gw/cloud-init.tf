locals {
  # Keep source scripts readable; cloud-init decodes each file before writing it.
  wireguard_cloud_config = yamldecode(templatefile("${path.module}/wireguard-cloud-init.tftpl", {
    ssh_user_name      = var.ssh_user_name
    access_config_json = jsonencode(merge(var.access_bootstrap, { ssh_user = var.ssh_user_name }))
    network_helper     = indent(6, file("${path.module}/wireguard_network.py"))
    access_helper      = indent(6, file("${path.module}/wireguard_access.py"))
    wireguard_config_json = jsonencode({
      wireguard_tunnel_cidr               = var.wireguard_tunnel_cidr
      wireguard_listen_port               = var.wireguard_listen_port
      nat_mode                            = var.nat_mode
      wireguard_mtu                       = var.wireguard_mtu
      endpoint_host                       = var.endpoint_host
      local_subnets                       = var.local_subnets
      client_default_dns                  = var.client_default_dns
      client_default_persistent_keepalive = var.client_default_persistent_keepalive
    })
    bootstrap_clients_json = jsonencode(var.clients)
  }))

  cloud_init_user_data = "#cloud-config\n${yamlencode(merge(local.wireguard_cloud_config, {
    write_files = [for entry in local.wireguard_cloud_config.write_files : merge(entry, {
      encoding = "gz+b64"
      content  = base64gzip(entry.content)
    })]
  }))}"
}
