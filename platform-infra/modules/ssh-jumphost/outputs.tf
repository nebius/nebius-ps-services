output "instance_id" {
  description = "SSH jump-host VM instance ID."
  value       = nebius_compute_v1_instance.ssh_jumphost.id
}

output "private_ip" {
  description = "SSH jump-host VM private IPv4 address."
  value = try(
    trimsuffix(nebius_compute_v1_instance.ssh_jumphost.status.network_interfaces[0].ip_address.address, "/32"),
    null
  )
}

output "public_ip" {
  description = "SSH jump-host VM public IPv4 address."
  value = try(
    trimsuffix(nebius_compute_v1_instance.ssh_jumphost.status.network_interfaces[0].public_ip_address.address, "/32"),
    null
  )
}

output "public_ip_allocation_id" {
  description = "Public IP allocation ID attached to the SSH jump-host VM."
  value       = local.effective_public_ip_allocation_id
}

output "ssh_connect_command" {
  description = "Convenience SSH command using the configured SSH username."
  value = try(
    "ssh ${var.ssh_user_name}@${trimsuffix(nebius_compute_v1_instance.ssh_jumphost.status.network_interfaces[0].public_ip_address.address, "/32")}",
    null
  )
}
