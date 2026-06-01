module "wireguard_gw" {
  source = "../.."

  parent_id           = "project-xxxxxxxx"
  network_id          = "vpcnetwork-xxxxxxxx"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  name                = "example-wg"
  platform            = "cpu-d3"
  preset              = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  boot_disk_size_gib  = 64
  ssh_user_name       = "ubuntu"
  ssh_public_key      = "ssh-ed25519 AAAA... user@example"

  wireguard_tunnel_cidr = "10.8.0.1/22"
  wireguard_listen_port = 51820
  nat_mode              = true
  local_subnets = [
    "10.0.0.0/8",
  ]
}
