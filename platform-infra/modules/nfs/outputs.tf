output "instance_id" {
  description = "NFS VM instance ID."
  value       = nebius_compute_v1_instance.this.id
}

output "server_ip" {
  description = "Private IP address clients should use for NFS mounts."
  value = try(
    trimsuffix(nebius_compute_v1_instance.this.status.network_interfaces[0].ip_address.address, "/32"),
    null
  )
}

output "public_ip" {
  description = "Public IP address when public_ip_mode is not none."
  value = try(
    trimsuffix(nebius_compute_v1_instance.this.status.network_interfaces[0].public_ip_address.address, "/32"),
    null
  )
}

output "export_path" {
  description = "NFS export path."
  value       = var.export_path
}

output "mount_options" {
  description = "Recommended Kubernetes NFS mount options."
  value       = var.mount_options
}

output "export_options" {
  description = "Server-side /etc/exports options."
  value       = var.export_options
}

output "export_spec" {
  description = "Structured NFS export metadata for cxcli and Helm values."
  value = {
    server_ip     = try(trimsuffix(nebius_compute_v1_instance.this.status.network_interfaces[0].ip_address.address, "/32"), null)
    export_path   = var.export_path
    mount_options = var.mount_options
  }
}
