output "instance_id" {
  description = "SSH jump-host VM instance ID."
  value       = module.vm.instance_id
}

output "private_ip" {
  description = "SSH jump-host VM private IPv4 address."
  value       = module.vm.private_ip
}

output "public_ip" {
  description = "SSH jump-host VM public IPv4 address."
  value       = module.vm.public_ip
}

output "public_ip_allocation_id" {
  description = "Public IP allocation ID attached to the SSH jump-host VM."
  value       = local.effective_public_ip_allocation_id
}

output "ssh_connect_command" {
  description = "Convenience SSH command using the configured SSH username."
  value       = module.vm.ssh_connect_command
}
