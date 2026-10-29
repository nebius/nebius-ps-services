output "access_resources" {
  description = "Public identities for independent SDK readback and payload initialization."
  value = {
    login             = var.secret_ids.login
    host              = var.secret_ids.host
    connection        = var.secret_ids.connection
    account           = nebius_iam_v1_service_account.gateway.id
    group             = nebius_iam_v1_group.gateway.id
    membership        = nebius_iam_v1_group_membership.gateway.id
    host-permit       = nebius_iam_v1_access_permit.host.id
    login-permit      = nebius_iam_v1_access_permit.operator["login"].id
    connection-permit = nebius_iam_v1_access_permit.operator["connection"].id
  }
}
