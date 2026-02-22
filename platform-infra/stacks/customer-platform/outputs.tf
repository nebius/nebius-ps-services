output "mk8s_cluster_id" {
  description = "MK8s cluster ID."
  value       = module.mk8s.cluster_id
}

output "managed_postgresql_cluster_id" {
  description = "Managed PostgreSQL cluster ID."
  value       = module.managed_postgresql.cluster_id
}

output "sfs_filesystem_id" {
  description = "Shared filesystem ID."
  value       = module.sfs.filesystem_id
}

output "inventory_bucket_name" {
  description = "Inventory bucket name."
  value = coalesce(
    try(module.object_storage.bucket_names["inventory"], null),
    var.inventory_bucket_name,
  )
}

output "mysterybox_secret_ids" {
  description = "MysteryBox secret IDs keyed by logical secret ID."
  value       = try(one(module.mysterybox[*].secret_ids), {})
}

output "mysterybox_secret_names" {
  description = "MysteryBox secret names keyed by logical secret ID."
  value       = try(one(module.mysterybox[*].secret_names), {})
}

output "wireguard_instance_id" {
  description = "WireGuard instance ID."
  value       = try(one(module.wireguard_jumphost[*].instance_id), null)
}

output "wireguard_public_ip" {
  description = "WireGuard public IP address."
  value       = try(one(module.wireguard_jumphost[*].public_ip), null)
}

output "wireguard_listen_port" {
  description = "WireGuard UDP listen port."
  value       = try(one(module.wireguard_jumphost[*].wireguard_listen_port), null)
}

output "wireguard_clients_path" {
  description = "Path on the WireGuard VM where generated client configs are stored."
  value       = try(one(module.wireguard_jumphost[*].wireguard_clients_path), null)
}

output "ssh_jumphost_instance_id" {
  description = "SSH jump-host instance ID."
  value       = try(one(module.ssh_jumphost[*].instance_id), null)
}

output "ssh_jumphost_public_ip" {
  description = "SSH jump-host public IP address."
  value       = try(one(module.ssh_jumphost[*].public_ip), null)
}
