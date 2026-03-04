locals {
  tier_profiles = {
    small = {
      platform  = "cpu-e2"
      preset    = "2vcpu-8gb"
      disk_type = "network-ssd"
      hosts     = 1
    }
    medium = {
      platform  = "cpu-d3"
      preset    = "4vcpu-16gb"
      disk_type = "network-ssd"
      hosts     = 1
    }
    large = {
      platform  = "cpu-d3"
      preset    = "8vcpu-32gb"
      disk_type = "network-ssd"
      hosts     = 2
    }
  }

  selected_profile = lookup(local.tier_profiles, lower(var.tier), local.tier_profiles.medium)
}
