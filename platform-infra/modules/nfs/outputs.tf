output "instance_id" {
  description = "NFS VM instance ID."
  value       = module.vm.instance_id
}

output "boot_disk_id" {
  description = "NFS VM boot disk ID."
  value       = module.vm.boot_disk_id
}

output "data_disk_ids" {
  description = "NFS VM managed data disk IDs created by the upstream VM module."
  value       = module.vm.data_disk_ids
}

output "server_ip" {
  description = "Private IP address clients should use for NFS mounts."
  value       = module.vm.private_ip
}

output "public_ip" {
  description = "Public IP address when public_ip_mode is not none."
  value       = module.vm.public_ip
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
  value       = local.effective_export_options
}

output "export_spec" {
  description = "Structured NFS export metadata for cxcli and Helm values."
  value = {
    server_ip     = module.vm.private_ip
    export_path   = var.export_path
    mount_options = var.mount_options
  }
}
