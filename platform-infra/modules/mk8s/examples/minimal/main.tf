module "mk8s" {
  source = "../.."

  parent_id    = "project-xxxxxxxx"
  cluster_name = "example-mk8s"
  subnet_id    = "vpcsubnet-xxxxxxxx"

  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
}
