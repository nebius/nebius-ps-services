output "instance_id" {
  description = "Gateway VM ID."
  value       = module.vm.instance_id
}
output "boot_disk_id" {
  description = "Gateway boot disk ID."
  value       = module.vm.boot_disk_id
}
output "subnet_id" {
  description = "Created or independently owned gateway subnet ID."
  value       = local.subnet_id
}
output "private_ip" {
  description = "Gateway private IPv4 address."
  value       = module.vm.private_ip
}
output "public_ip" {
  description = "Gateway static egress IPv4 address."
  value       = module.vm.public_ip
}
output "private_allocation_id" {
  description = "Stable next-hop private allocation ID."
  value       = nebius_vpc_v1_allocation.private.id
}
output "public_allocation_id" {
  description = "Stable public allocation ID."
  value       = nebius_vpc_v1_allocation.public.id
}
output "gateway_route_table_id" {
  description = "Direct egress route table for the gateway subnet only."
  value       = local.gateway_route_table_id
}
output "route_table_ids" {
  description = "Workload route table IDs by reviewed policy key; attach only after guest readiness."
  value       = { for key, table in nebius_vpc_v1_route_table.workload : key => table.id }
}
output "guest_config_sha256" {
  description = "Expected nonsecret guest configuration digest used by readiness verification."
  value       = sha256(local.guest_config)
}
