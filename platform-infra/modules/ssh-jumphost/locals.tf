locals {
  effective_labels = merge(
    {
      component = "ssh-jumphost"
      name      = var.name
    },
    var.labels,
  )

  effective_public_ip_allocation_name = coalesce(
    var.public_ip_allocation_name,
    "${var.name}-public-ip"
  )

  effective_public_ip_allocation_id = (
    var.public_ip_allocation_id != null
    ? var.public_ip_allocation_id
    : (var.create_public_ip_allocation ? nebius_vpc_v1_allocation.ssh_jumphost_public[0].id : null)
  )
}
