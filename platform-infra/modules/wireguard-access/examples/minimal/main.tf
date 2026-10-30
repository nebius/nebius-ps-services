module "access" {
  source         = "../.."
  parent_id      = "project-example"
  name           = "vpn-access"
  admin_group_id = "group-admins"
  secret_ids = {
    login      = "secret-login"
    host       = "secret-host"
    connection = "secret-connection"
  }
}
