module "mk8s" {
  source = "../.."

  cluster = {
    parent_id       = "project-xxxxxxxx"
    cluster_name    = "example-mk8s"
    network_id      = "vpcnetwork-xxxxxxxx"
    subnet_id       = "vpcsubnet-xxxxxxxx"
    k8s_version     = "1.33"
    public_endpoint = true
    kube_network = {
      service_cidrs = ["/20"]
    }
  }

  node_groups = {
    system = {
      node_count  = 2
      gpu         = false
      platform    = "cpu-d3"
      preset      = "4vcpu-16gb"
      node_labels = { "example.nebius.ai/role" = "system" }
    }
  }
}
