module "managed_postgresql" {
  source = "../.."

  enabled    = true
  parent_id  = "project-xxxxxxxx"
  network_id = "vpcnetwork-xxxxxxxx"
  name       = "example-pg"

  tier        = "medium"
  storage_gib = 100
}
