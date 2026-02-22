# mk8s module

Reusable Terraform module that creates Nebius Managed Kubernetes infrastructure
(cluster + node groups).

Resources managed:

- `nebius_mk8s_v1_cluster`
- `nebius_mk8s_v1_node_group` (CPU and optional GPU)
- `nebius_compute_v1_gpu_cluster` (optional, when `infiniband_fabric` is set)

Out of scope:

- in-cluster app/operator lifecycle (Flux/GitOps responsibility)
- kubeconfig retrieval and local `kubectl` setup

## What this module does

- Creates one MK8s cluster with control-plane settings.
- Creates one CPU node group when `cpu_nodes_count > 0`.
- Creates `gpu_node_groups` GPU node groups when `gpu_enabled = true`.
- Supports provider-aligned override objects for cluster/CPU/GPU node groups.
- Applies MIG hints as node labels when provided:
  - `nvidia.com/mig.strategy`
  - `nvidia.com/mig.config`

## Usage

### Local path source

```hcl
module "mk8s" {
  source = "./platform-infra/modules/mk8s"

  parent_id    = "project-xxxxxxxx"
  cluster_name = "client-a-prod"
  subnet_id    = "vpcsubnet-xxxxxxxx"

  ssh_public_key = "ssh-ed25519 AAAA... user@example"

  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"

  gpu_enabled = false
}
```

### Git tag source

```hcl
module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=v0.1.0"

  parent_id       = "project-xxxxxxxx"
  cluster_name    = "client-a-prod"
  subnet_id       = "vpcsubnet-xxxxxxxx"
  ssh_public_key  = "ssh-ed25519 AAAA... user@example"
  cpu_nodes_count = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
}
```

### Registry source (when published)

```hcl
module "mk8s" {
  source  = "nebius/mk8s/nebius"
  version = "~> 0.1"

  parent_id       = "project-xxxxxxxx"
  cluster_name    = "client-a-prod"
  subnet_id       = "vpcsubnet-xxxxxxxx"
  ssh_public_key  = "ssh-ed25519 AAAA... user@example"
  cpu_nodes_count = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
}
```

## Inputs summary

- Required core inputs:
  - `parent_id`
  - `cluster_name`
  - `subnet_id`
  - `ssh_public_key`
  - `cpu_nodes_platform`
  - `cpu_nodes_preset`
- Optional cluster controls:
  - `k8s_version`
  - `etcd_cluster_size`
  - `mk8s_cluster_public_endpoint`
- CPU node group controls:
  - `cpu_nodes_count`
  - `cpu_nodes_preemptible`
  - `cpu_nodes_public_ips`
- GPU controls:
  - `gpu_enabled`
  - `gpu_node_groups`
  - `gpu_nodes_count_per_group`
  - `gpu_nodes_platform`
  - `gpu_nodes_preset`
  - `gpu_nodes_preemptible`
  - `gpu_nodes_public_ips`
  - `gpu_driverfull_image`
  - `infiniband_fabric`
  - `mig_strategy`
  - `mig_parted_config`
- Advanced provider-aligned passthrough objects:
  - `mk8s_cluster_overrides`
  - `mk8s_cpu_node_group_overrides`
  - `mk8s_gpu_node_group_overrides`

## Outputs summary

- `cluster_id`
- `cluster_name`
- `cpu_node_group_ids`
- `gpu_node_group_ids`

## nebius-cxcli mapping

When consumed through `platform-infra/stacks/customer-platform`,
`nebius-cxcli` maps:

- `infra.mk8s.*` -> base MK8s variables
- `infra.mk8s.cluster_overrides` -> `mk8s_cluster_overrides`
- `infra.mk8s.cpu_node_group_overrides` -> `mk8s_cpu_node_group_overrides`
- `infra.mk8s.gpu_node_group_overrides` -> `mk8s_gpu_node_group_overrides`

## Validation commands

```bash
terraform fmt -recursive
terraform init -backend=false
terraform validate
```
