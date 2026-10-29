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
  # Prepare these public references with cxcli; no private key belongs in Terraform.
  access_bootstrap = {
    generation         = "00000000-0000-0000-0000-000000000000"
    service_account_id = "serviceaccount-placeholder"
    host_secret_id     = "mysteryboxsecret-placeholder"
    host_version_id    = "mysteryboxsecretversion-placeholder"
    host_public_key    = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    login_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  }

  wireguard_tunnel_cidr = "10.8.0.1/22"
  wireguard_listen_port = 51820
  nat_mode              = true
  local_subnets = [
    "10.0.0.0/8",
  ]
}
