module "managed_postgresql" {
  source = "../.."

  parent_id  = "project-xxxxxxxx"
  network_id = "vpcnetwork-xxxxxxxx"
  name       = "example-pg"

  tier        = "medium"
  storage_gib = 100
}
