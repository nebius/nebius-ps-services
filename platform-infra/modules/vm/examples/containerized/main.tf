module "vm" {
  source = "../.."

  parent_id      = "project-xxxxxxxx"
  network_id     = "vpcnetwork-xxxxxxxx"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-container-vm"
  platform       = "gpu-h200-sxm"
  preset         = "1gpu-16vcpu-200gb"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  source_image_family      = "ubuntu24.04-cuda13.0"
  container_enabled        = true
  container_image          = "nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda11.7.1-ubuntu20.04"
  container_use_gpu        = true
  container_restart_policy = "unless-stopped"
  container_ports = [
    {
      host_port      = 8080
      container_port = 8080
      protocol       = "tcp"
    }
  ]
}
