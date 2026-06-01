module "ssh_jump_host" {
  source = "../.."

  parent_id           = "project-xxxxxxxx"
  network_id          = "vpcnetwork-xxxxxxxx"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  name                = "example-ssh-jh"
  platform            = "cpu-d3"
  preset              = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  boot_disk_size_gib  = 64
  ssh_user_name       = "ubuntu"
  ssh_public_key      = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
