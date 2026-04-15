module "mk8s" {
  source = "../.."

  parent_id    = "project-xxxxxxxx"
  cluster_name = "example-mk8s-gpu"
  subnet_id    = "vpcsubnet-xxxxxxxx"

  cpu_nodes_count = 0

  gpu_enabled               = true
  gpu_node_groups           = 1
  gpu_nodes_count_per_group = 1
  gpu_nodes_platform        = "gpu-b200-sxm"
  gpu_nodes_preset          = "8gpu-180gb"
  gpu_stack_source          = "nebius_image"
  gpu_stack_preset          = "cuda13.0"
  gpu_nodes_os              = "ubuntu24.04"
}
