output "members" {
  description = "Exact cloud identities and addresses, keyed by stable member name."
  value = { for key, vm in module.member : key => {
    network_id           = var.network_id
    subnet_id            = var.subnet_id
    instance_id          = vm.instance_id
    boot_disk_id         = vm.boot_disk_id
    public_allocation_id = nebius_vpc_v1_allocation.public[key].id
    public_ip            = vm.public_ip
    private_ip           = vm.private_ip
    name                 = "${var.name}-${key}"
  } }
}
output "high_availability" {
  description = "Whether two routers were requested; this is not live failover proof."
  value       = var.replicas == 2
}
