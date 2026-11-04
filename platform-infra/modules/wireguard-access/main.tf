resource "nebius_iam_v1_service_account" "gateway" {
  parent_id = var.parent_id
  name      = "${var.name}-vm"
}
resource "nebius_iam_v1_group" "gateway" {
  parent_id = var.parent_id
  name      = "${var.name}-vm"
}
resource "nebius_iam_v1_group_membership" "gateway" {
  parent_id = nebius_iam_v1_group.gateway.id
  member_id = nebius_iam_v1_service_account.gateway.id
}
resource "nebius_iam_v1_access_permit" "host" {
  parent_id   = nebius_iam_v1_group.gateway.id
  resource_id = var.secret_ids.host
  role        = "mysterybox.payload-viewer"
}
resource "nebius_iam_v1_access_permit" "operator" {
  for_each    = toset(["login", "connection"])
  parent_id   = var.admin_group_id
  resource_id = var.secret_ids[each.key]
  role        = "mysterybox.payload-viewer"
}
