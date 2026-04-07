module "sfs" {
  source = "../.."

  parent_id      = "project-xxxxxxxx"
  name           = "example-sfs"
  size_gib       = 500
  block_size_kib = 4
}
