module "mk8s" {
  source = "../.."

  cluster = {
    parent_id       = "project-xxxxxxxx"
    cluster_name    = "example-mk8s-gpu"
    network_id      = "vpcnetwork-xxxxxxxx"
    subnet_id       = "vpcsubnet-xxxxxxxx"
    k8s_version     = "1.33"
    public_endpoint = true
    kube_network = {
      service_cidrs = ["/20"]
    }
  }

  node_groups = {
    worker = {
      node_count       = 1
      gpu              = true
      platform         = "gpu-b200-sxm"
      preset           = "8gpu-180gb"
      os               = "ubuntu24.04"
      gpu_stack_source = "nebius_image"
      gpu_stack_preset = "cuda13.0"
      node_labels = {
        "nebius.com/gpu" = "true"
      }
      taints = [{
        key    = "nvidia.com/gpu"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }
}
