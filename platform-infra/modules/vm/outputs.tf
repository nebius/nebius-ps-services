output "instance_id" {
  description = "VM instance ID."
  value       = nebius_compute_v1_instance.vm.id
}

output "boot_disk_id" {
  description = "Boot disk ID attached to the VM."
  value       = local.effective_boot_disk_id
}

output "data_disk_ids" {
  description = "Managed data disk IDs created by the module."
  value = {
    for disk_name, disk in nebius_compute_v1_disk.data : disk_name => disk.id
  }
}

output "gpu_cluster_id" {
  description = "GPU cluster ID attached to the VM, if any."
  value       = local.effective_gpu_cluster_id
}

output "private_ip" {
  description = "VM private IPv4 address."
  value = try(
    trimsuffix(nebius_compute_v1_instance.vm.status.network_interfaces[0].ip_address.address, "/32"),
    null
  )
}

output "public_ip" {
  description = "VM public IPv4 address, if attached."
  value = try(
    trimsuffix(nebius_compute_v1_instance.vm.status.network_interfaces[0].public_ip_address.address, "/32"),
    null
  )
}

output "public_ip_allocation_id" {
  description = "Existing public IP allocation ID passed into the module when public_ip_mode=allocation."
  value       = lower(var.public_ip_mode) == "allocation" ? var.public_ip_allocation_id : null
}

output "ssh_connect_command" {
  description = "Convenience SSH command using the configured SSH username and public IP."
  value = try(
    "ssh ${var.ssh_user_name}@${trimsuffix(nebius_compute_v1_instance.vm.status.network_interfaces[0].public_ip_address.address, "/32")}",
    null
  )
}
