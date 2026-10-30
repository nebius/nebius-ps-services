mock_provider "nebius" {}

run "narrow_grants" {
  command = plan
  variables {
    parent_id      = "project-example"
    name           = "vpn-access"
    admin_group_id = "group-admins"
    secret_ids = {
      login      = "secret-login"
      host       = "secret-host"
      connection = "secret-connection"
    }
  }
  assert {
    condition     = length(nebius_iam_v1_access_permit.operator) == 2 && nebius_iam_v1_access_permit.host.resource_id == "secret-host"
    error_message = "VM reads only its host secret; operators read only login and connection."
  }
  assert {
    condition = alltrue([for permit in nebius_iam_v1_access_permit.operator :
      permit.parent_id == "group-admins" && permit.role == "mysterybox.payload-viewer" && permit.resource_id != "secret-host"
    ])
    error_message = "Operator grants must be narrow payload-viewer permits."
  }
}
