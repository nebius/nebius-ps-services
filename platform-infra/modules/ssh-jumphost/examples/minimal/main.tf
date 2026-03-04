module "ssh_jump_host" {
  source = "../.."

  parent_id      = "project-xxxxxxxx"
  region         = "eu-north1"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-ssh-jh"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  allowed_cidrs = [
    "203.0.113.10/32",
  ]
}
