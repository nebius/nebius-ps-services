output "instance_id" {
  description = "WireGuard VPN gateway VM instance ID."
  value       = module.vm.instance_id
}

output "private_ip" {
  description = "WireGuard VPN gateway VM private IPv4 address."
  value       = module.vm.private_ip
}

output "public_ip" {
  description = "WireGuard VPN gateway VM public IPv4 address."
  value       = module.vm.public_ip
}

output "public_ip_allocation_id" {
  description = "Public IP allocation ID attached to the WireGuard VPN gateway VM."
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

output "wireguard_client_registry_path" {
  description = "Path on the VM where WireGuard client allocation metadata is stored."
  value       = "/var/lib/nebius-wireguard/clients.json"
}

output "wireguard_client_generator_path" {
  description = "Path on the VM for the day-2 WireGuard client generator command."
  value       = "/usr/local/sbin/nebius-wireguard-client"
}
