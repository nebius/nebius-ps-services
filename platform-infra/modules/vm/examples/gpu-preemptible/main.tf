module "vm" {
  source = "../.."

  parent_id      = "project-xxxxxxxx"
  network_id     = "vpcnetwork-xxxxxxxx"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-gpu-preemptible-vm"
  platform       = "gpu-rtx6000"
  preset         = "1gpu-24vcpu-218gb"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  source_image_family = "ubuntu24.04-cuda13.0"
  recovery_policy     = "FAIL"
  preemptible_enabled = true
  public_ip_mode      = "none"
}
