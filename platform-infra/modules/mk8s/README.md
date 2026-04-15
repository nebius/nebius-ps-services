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

When this module is consumed through `nebius-cxcli`, the CLI can perform the
post-apply kubeconfig handoff if the source catalog declares that contract.
The bundled catalog resolves handoff access from `mk8s_cluster_public_endpoint`,
so local app operations use the public or private control-plane endpoint
dynamically instead of hardcoding public access.

## What this module does

- Creates one MK8s cluster with control-plane settings.
- Creates one CPU node group when fixed count (`cpu_nodes_count > 0`) or autoscaling override is configured.
- Creates `gpu_node_groups` GPU node groups when `gpu_enabled = true`.
- Supports provider-aligned override objects for cluster/CPU/GPU node groups.
- Supports explicit Nebius-image vs manual GPU stack selection through `gpu_stack_source`.

## Usage

### Local path source

```hcl
module "mk8s" {
  source = "./platform-infra/modules/mk8s"

  parent_id    = "project-xxxxxxxx"
  cluster_name = "client-a-prod"
  subnet_id    = "vpcsubnet-xxxxxxxx"

  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
  cpu_nodes_os       = "ubuntu24.04"

  gpu_enabled = false
}
```

### Git tag source

```hcl
module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=v0.1.0"

  parent_id          = "project-xxxxxxxx"
  cluster_name       = "client-a-prod"
  subnet_id          = "vpcsubnet-xxxxxxxx"
  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
  cpu_nodes_os       = "ubuntu24.04"
}
```

### Registry source (when published)

```hcl
module "mk8s" {
  source  = "nebius/mk8s/nebius"
  version = "~> 0.1"

  parent_id          = "project-xxxxxxxx"
  cluster_name       = "client-a-prod"
  subnet_id          = "vpcsubnet-xxxxxxxx"
  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
  cpu_nodes_os       = "ubuntu24.04"
}
```

## Inputs summary

- Required core inputs:
  - `parent_id`
  - `cluster_name`
  - `subnet_id`
- Optional cluster controls:
  - `k8s_version`
  - `etcd_cluster_size`
  - `mk8s_cluster_public_endpoint`
  - `kube_network_service_cidrs`
- CPU node group controls:
  - `cpu_nodes_count`
  - `cpu_nodes_platform` (required when CPU node group is enabled)
  - `cpu_nodes_preset` (required when CPU node group is enabled)
  - `cpu_nodes_os`
  - `cpu_nodes_preemptible`
  - `cpu_nodes_public_ips`
- GPU controls:
  - `gpu_enabled`
  - `gpu_node_groups`
  - `gpu_nodes_count_per_group`
  - `gpu_nodes_platform`
  - `gpu_nodes_preset`
  - `gpu_nodes_os`
  - `gpu_nodes_preemptible`
  - `gpu_nodes_public_ips`
  - `gpu_stack_source`
  - `gpu_stack_preset`
  - `infiniband_fabric`
- Advanced provider-aligned passthrough objects:
  - `mk8s_cluster_overrides`
  - `mk8s_cpu_node_group_overrides`
  - `mk8s_gpu_node_group_overrides`

## Outputs summary

- `cluster_id`
- `cluster_name`
- `cpu_node_group_ids`
- `gpu_node_group_ids`
- `control_plane_private_endpoint`
- `control_plane_public_endpoint`
- `cluster_ca_certificate` (sensitive)

`cluster_id` is the required output consumed by `nebius-cxcli` when this module
is used as a kubeconfig handoff-capable cluster source for `deploy`/Flux flows.

`kube_network_service_cidrs` defaults to `["/20"]` in this module. That is
intentional. Nebius treats an omitted MK8s service CIDR as `["/16"]`, and on a
single-pool `/16` subnet that can consume the entire subnet pool and stall
control-plane provisioning before any node groups are created.

`cpu_nodes_count` does not have an internal module default. Callers should set
it explicitly when they want the baseline CPU node group. `nebius-cxcli` does
that through the bundled source catalog, so generated `config.yaml` files show
the chosen baseline count instead of inheriting a hidden Terraform default.

For direct Terraform usage, the example roots under `examples/` now re-expose
`cluster_id` and `cluster_name` as root outputs so `terraform output` can be
used directly from the example directory.

## Validation and fail-fast behavior

- `gpu_enabled = true` requires:
  - `gpu_node_groups > 0`
  - `gpu_nodes_count_per_group > 0` when autoscaling override is not set
  - non-empty effective `template.resources.platform` and
    `template.resources.preset` (from defaults or overrides)
  - non-empty `gpu_stack_preset` when `gpu_stack_source = "nebius_image"`
- CPU node group creation requires non-empty effective
  `template.resources.platform` and `template.resources.preset` (from defaults
  or overrides).
- Fixed-size and autoscaling settings are mutually exclusive per node group.

## nebius-cxcli usage

- `nebius-cxcli component add` prompts this module through
  `infra.components[].inputs`.
- `cpu_nodes_platform` and `cpu_nodes_preset` are treated as required by the
  CLI when the baseline CPU node group is enabled.
- `cpu_nodes_os`, `gpu_stack_source`, `gpu_stack_preset`, and `gpu_nodes_os`
  are intended to be materialized by `nebius-cxcli` from the live MK8s
  compatibility matrix instead of guessed in Terraform.
- If `mk8s_cluster_public_endpoint = false`, local `deploy` / `flux apply` /
  `flux bootstrap` / `destroy` app flows still work, but only from a machine
  that already has private network reachability to the MK8s control-plane
  endpoint. `nebius-cxcli` does not hardcode a specific jump-host or VPN
  product for that path.
- Complex inputs such as `kube_network_service_cidrs`,
  and the `mk8s_*_overrides` objects are meant to be
  provided as YAML/JSON values in the wizard or edited directly in
  `config.yaml`.

## Examples

- `examples/minimal`: CPU-only baseline.
- `examples/gpu`: GPU node group with explicit Nebius-managed stack preset selection.

Example output usage after apply:

```bash
terraform -chdir=examples/minimal output -raw cluster_id
terraform -chdir=examples/minimal output -raw cluster_name
```

## nebius-cxcli mapping

For Terraform roots generated by `nebius-cxcli`, mappings are:

- `infra.mk8s.*` -> base MK8s variables
- `infra.mk8s.cluster_overrides` -> `mk8s_cluster_overrides`
- `infra.mk8s.cpu_node_group_overrides` -> `mk8s_cpu_node_group_overrides`
- `infra.mk8s.gpu_node_group_overrides` -> `mk8s_gpu_node_group_overrides`

## Validation commands

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
terraform -chdir=examples/gpu init -backend=false
terraform -chdir=examples/gpu validate
```
