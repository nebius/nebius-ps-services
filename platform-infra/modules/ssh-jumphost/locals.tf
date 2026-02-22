locals {
  region_defaults = {
    eu-west1 = {
      platform = "cpu-d3"
      preset   = "4vcpu-16gb"
    }
    eu-north1 = {
      platform = "cpu-d3"
      preset   = "4vcpu-16gb"
    }
    eu-north2 = {
      platform = "cpu-d3"
      preset   = "4vcpu-16gb"
    }
    us-central1 = {
      platform = "cpu-d3"
      preset   = "4vcpu-16gb"
    }
  }

  current_region_defaults = lookup(
    local.region_defaults,
    var.region,
    {
      platform = "cpu-d3"
      preset   = "4vcpu-16gb"
    }
  )

  effective_platform = coalesce(var.platform, local.current_region_defaults.platform)
  effective_preset   = coalesce(var.preset, local.current_region_defaults.preset)

  effective_public_ip_allocation_name = coalesce(
    var.public_ip_allocation_name,
    "${var.name}-public-ip"
  )

  effective_public_ip_allocation_id = (
    var.public_ip_allocation_id != null
    ? var.public_ip_allocation_id
    : (var.create_public_ip_allocation ? nebius_vpc_v1_allocation.ssh_jumphost_public[0].id : null)
  )

  public_ip_address = (
    local.effective_public_ip_allocation_id != null
    ? { allocation_id = local.effective_public_ip_allocation_id }
    : {}
  )
}
