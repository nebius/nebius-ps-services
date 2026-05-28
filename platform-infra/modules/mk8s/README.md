# mk8s module

Reusable Terraform module for Nebius Managed Kubernetes clusters and typed node
groups.

This module has one public API shape:

- `cluster`: the MK8s control-plane and network contract.
- `node_groups`: a map of logical node-group definitions.
- `gpu_clusters`: optional GPU-cluster / InfiniBand attachments referenced by
  GPU node groups.

There are no CPU/GPU shortcut variables and no compatibility shims. Existing
callers must migrate configuration and state intentionally.

## Resources

- `nebius_mk8s_v1_cluster`
- `nebius_mk8s_v1_node_group`
- `nebius_compute_v1_gpu_cluster` when `gpu_clusters` is set
- `nebius_iam_v1_service_account` only for node groups that request a new
  service account name

The module does not install in-cluster applications, grant IAM roles, create SFS
filesystems, or manage kubeconfig. Those remain caller or orchestration
responsibilities.

## Usage

```hcl
module "mk8s" {
  source = "./platform-infra/modules/mk8s"

  cluster = {
    parent_id       = "project-xxxxxxxx"
    cluster_name    = "client-a-prod"
    network_id      = "vpcnetwork-xxxxxxxx"
    subnet_id       = "vpcsubnet-xxxxxxxx"
    k8s_version     = "1.31"
    public_endpoint = true
    kube_network = {
      service_cidrs = ["/20"]
    }
  }

  node_groups = {
    system = {
      node_count = 2
      platform   = "cpu-d3"
      preset     = "4vcpu-16gb"
      os         = "ubuntu24.04"
      boot_disk = {
        type           = "NETWORK_SSD"
        size_gibibytes = 128
      }
      node_labels = {
        "slurm.nebius.ai/nodeset-name" = "system"
      }
    }
  }
}
```

GPU node groups use the same map. Reservations are explicit:

```hcl
gpu_clusters = {
  workers = {
    infiniband_fabric = "fabric-name"
  }
}

node_groups = {
  worker = {
    gpu             = true
    node_count      = 2
    platform        = "gpu-h100-sxm"
    preset          = "8gpu-128vcpu-1600gb"
    os              = "ubuntu22.04"
    gpu_stack_source = "nebius_image"
    gpu_stack_preset = "cuda12.8"
    gpu_cluster_key  = "workers"

    reservation = {
      policy          = "STRICT"
      reservation_ids = ["capacity-block-group-id"]
    }

    node_labels = {
      "slurm.nebius.ai/nodeset-name" = "worker"
      "nebius.com/gpu"               = "true"
    }
    taints = [{
      key    = "nvidia.com/gpu"
      value  = "true"
      effect = "NO_SCHEDULE"
    }]
  }
}
```

## Cluster Input

`cluster` requires:

- `parent_id`
- `cluster_name`
- `network_id`
- `subnet_id`
- `k8s_version`
- `public_endpoint`

Optional cluster fields include labels, `etcd_cluster_size`, control-plane
overrides, and `kube_network.service_cidrs`. The default service CIDR is `["/20"]`
so callers do not accidentally consume an entire small subnet with the provider
default.

## Node Groups

Each `node_groups` entry is keyed by the canonical logical group name. The key is
used for Terraform addressing and outputs. A node group accepts:

- `node_count` or `autoscaling`, but not both.
- `gpu`, `platform`, `preset`, `os`, `boot_disk`, `preemptible`, and optional
  public IP/network-interface settings.
- Kubernetes `node_labels` and `taints`.
- `reservation.policy` and `reservation.reservation_ids` for GPU reservations.
- `service_account.id` or `service_account.name`, never both.
- `ssh.username` and `ssh.public_keys`; cloud-init is rendered only when SSH
  keys are configured.
- `sfs_filesystem_keys` for same-session SFS module attachment wiring in
  `nebius-cxcli`.
- Raw provider-style `filesystems` for explicit existing filesystem IDs.

Disabled `gpu_clusters` entries may omit `infiniband_fabric`, and disabled
`node_groups` entries may omit shape fields; enabled entries require
`platform` and `preset`. GPU node groups default to
`gpu_stack_source = "nebius_image"` and must set `gpu_stack_preset` for that
host-driver image path. Set `gpu_stack_source = "operator_managed"` when the
GPU Operator should install and manage the host GPU stack instead.

GPU node groups default to `reservation.policy = "FORBID"` unless the caller
sets a reservation policy. When `reservation_ids` are provided, use
`policy = "STRICT"`.

The module does not grant IAM roles for service accounts. Attach the service
account here, and grant roles explicitly elsewhere.

## SFS Attachments

SFS filesystems are created or looked up by the SFS component, not by this
module. This module accepts rendered filesystem attachment blocks through
`node_groups[*].filesystems`.

When used through `nebius-cxcli`, `sfs_filesystem_keys` can reference same-session
SFS entries by key. The renderer turns those keys into provider `filesystems`
attachments with the selected filesystem ID and mount tag.

## Outputs

- `cluster_id`
- `cluster_name`
- `cluster_parent_id`
- `cluster_network_id`
- `cluster_subnet_id`
- `cluster_k8s_version`
- `cluster_public_endpoint_enabled`
- `control_plane_private_endpoint`
- `control_plane_public_endpoint`
- `cluster_ca_certificate` (sensitive)
- `node_group_ids`
- `node_groups`
- `gpu_cluster_ids`
- `service_account_ids`
- `sfs_filesystem_keys_by_node_group`

Node-group outputs are keyed by the canonical `node_groups` map key.

## nebius-cxcli Contract

`nebius-cxcli` writes this module through `inputs.cluster`,
`inputs.node_groups`, and optional `inputs.gpu_clusters`. The bundled MK8s
wizard resolves VPC networks, subnets, Kubernetes versions, platforms, presets,
OS choices, disk types, GPU stack presets, and fabrics from provider/catalog
option sources instead of hardcoding them.

Soperator profiles are data in `component_cli_settings.yaml`. The default
Nebius production profile materializes five logical node groups:

- `system`
- `controller`
- `login`
- `accounting`
- `worker`

Mixed profiles may still use separate `worker-cpu` and `worker-gpu` NodeSets.
Those are profile choices, not module constants.

## References

The module shape follows the current Nebius Terraform provider resources for
`nebius_mk8s_v1_cluster` and `nebius_mk8s_v1_node_group`, plus Nebius guidance
for node-group reservations and Compute filesystem attachment.

## Validation

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
terraform -chdir=examples/generic-node-groups init -backend=false
terraform -chdir=examples/generic-node-groups validate
terraform -chdir=examples/gpu init -backend=false
terraform -chdir=examples/gpu validate
```
