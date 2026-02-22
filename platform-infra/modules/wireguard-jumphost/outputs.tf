output "instance_id" {
  description = "WireGuard VM instance ID."
  value       = nebius_compute_v1_instance.wireguard.id
}

output "private_ip" {
  description = "WireGuard VM private IPv4 address."
  value = try(
    trimsuffix(nebius_compute_v1_instance.wireguard.status.network_interfaces[0].ip_address.address, "/32"),
    null
  )
}

output "public_ip" {
  description = "WireGuard VM public IPv4 address."
  value = try(
    trimsuffix(nebius_compute_v1_instance.wireguard.status.network_interfaces[0].public_ip_address.address, "/32"),
    null
  )
}

output "public_ip_allocation_id" {
  description = "Public IP allocation ID attached to the WireGuard VM."
  value       = local.effective_public_ip_allocation_id
}

output "wireguard_listen_port" {
  description = "Configured WireGuard UDP listen port."
  value       = var.wireguard_listen_port
}

output "wireguard_clients_path" {
  description = "Path on the VM where generated client configs are stored."
  value       = "/var/lib/wireguard/clients"
}
