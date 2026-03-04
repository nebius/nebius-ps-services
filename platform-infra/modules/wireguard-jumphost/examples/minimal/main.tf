module "wireguard_jump_host" {
  source = "../.."

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-wg"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  wireguard_tunnel_cidr = "10.8.0.1/24"
  wireguard_listen_port = 51820
  nat_mode              = true
}
