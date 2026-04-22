# vm module

Reusable Terraform module that creates one Nebius Compute virtual machine with
explicit platform/preset selection.

Resources managed:

- `nebius_compute_v1_instance` (VM)
- optional `nebius_compute_v1_disk` resources (boot disk and managed data disks)
- optional `nebius_compute_v1_gpu_cluster` (when creating a new GPU cluster)

Out of scope:

- application orchestration beyond a single optional bootstrap container
- auto-mounting and filesystem configuration inside the guest OS
- image-family discovery and platform/preset availability checks beyond the
  validation exposed directly by the Terraform provider

## Usage

### Local path source

```hcl
module "vm" {
  source = "./platform-infra/modules/vm"

  parent_id      = "project-xxxxxxxx"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-vm"
  platform       = "cpu-d3"
  preset         = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"
}
```

### Git tag source

```hcl
module "vm" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/vm?ref=v0.1.0"

  parent_id      = "project-xxxxxxxx"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-vm"
  platform       = "cpu-d3"
  preset         = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"
}
```

### Registry source (when published)

```hcl
module "vm" {
  source  = "nebius/vm/nebius"
  version = "~> 0.1"

  parent_id      = "project-xxxxxxxx"
  subnet_id      = "vpcsubnet-xxxxxxxx"
  name           = "example-vm"
  platform       = "cpu-d3"
  preset         = "4vcpu-16gb"
  source_image_family = "ubuntu24.04-driverless"
  ssh_user_name  = "ubuntu"
  ssh_public_key = "ssh-ed25519 AAAA... user@example"
}
```

## Examples

- `examples/minimal`: baseline regular CPU VM with a dynamic public IP.
- `examples/gpu-preemptible`: preemptible GPU VM using an explicit GPU
  platform/preset.
- `examples/containerized`: regular GPU VM that bootstraps Docker and runs one
  container workload.

## Inputs Summary

- Required:
  - `parent_id`
  - `subnet_id`
  - `name`
  - `platform`
  - `preset`
  - `ssh_public_key`
- Common optional controls:
  - `ssh_user_name`
  - `source_image_family`
  - `public_ip_mode`
  - `hostname`
  - `service_account_id`
  - `stopped`
- Boot/data storage:
  - set exactly one boot source: `source_image_family`, `source_image_id`, or `boot_disk_existing_id`
  - `boot_disk_existing_id`
  - `source_image_id`
  - `boot_disk_size_gib`
  - `boot_disk_type`
  - `data_disks`
  - `existing_data_disks`
  - `filesystems`
- GPU and lifecycle:
  - `preemptible_enabled`
  - `preemptible_priority`
  - `recovery_policy`
  - `gpu_cluster_enabled`
  - `gpu_cluster_id`
  - `gpu_cluster_infiniband_fabric`
- Container bootstrap:
  - `container_enabled`
  - `container_image`
  - `container_entrypoint`
  - `container_args`
  - `container_env`
  - `container_ports`
  - `container_mounts`
  - `container_use_gpu`

## Platform And Preset Contract

`platform` and `preset` are explicit required inputs in this module.

That is intentional:

- Nebius regular vs preemptible behavior depends on the selected shape.
- GPU-cluster eligibility depends on the selected platform/preset.
- `nebius-cxcli` can guide these fields from the live Nebius project inventory,
  but the Terraform contract itself stays explicit instead of hiding those
  decisions behind region defaults.

Direct callers should use the current Nebius platform and preset names from:

- <https://docs.nebius.com/compute/virtual-machines/types>
- the current Nebius compute platform inventory for the target project

## VM Type Semantics

- Regular VMs: supported for CPU and GPU platforms.
- Preemptible VMs: the module exposes these with `preemptible_enabled=true`,
  but only for GPU platforms and only with `recovery_policy=FAIL`, matching the
  Nebius documented contract.
- GPU clusters: `gpu_cluster_enabled=true` requires an `8gpu-*` preset and then
  either:
  - `gpu_cluster_id` to attach an existing cluster
  - `gpu_cluster_infiniband_fabric` to create a new cluster in the selected
    fabric

## Container Bootstrap Contract

When `container_enabled=true`, the module still provisions a standard Compute
VM and then bootstraps Docker with cloud-init/systemd to run one container.

This is intentionally described as a VM bootstrap path, not a native
Terraform-managed Nebius "Containers over VMs" resource, because the current
Nebius Terraform provider exposes VM primitives rather than a separate
containers-over-VM resource.

Operational notes:

- Container bootstrap uses Ubuntu-oriented install steps, so use an Ubuntu boot
  image when the module creates the boot disk.
- `container_use_gpu=true` requires a GPU platform and assumes the selected VM
  image already includes compatible NVIDIA drivers. Use a current public GPU
  image family that matches the selected platform's driver requirement, for
  example `ubuntu24.04-cuda13.0` for the current RTX6000/H200/B200 `580.x`
  driver line. Verify current image families from the Nebius public image
  inventory for the selected region.
- Containerized VMs stay regular. The module does not combine
  `container_enabled=true` with `preemptible_enabled=true`.

## `nebius-cxcli` Usage

- The bundled `vm` component is intended to map directly into
  `infra.components[].inputs`.
- `platform` and `preset` can be driven by live Nebius project inventory in the
  `nebius-cxcli` wizard.
- `source_image_family` is explicit in the module contract. The bundled
  `nebius-cxcli` VM wizard auto-materializes it from the live Nebius public
  image inventory for the selected platform and region, ordered by catalog
  preferences.
- The shared admin SSH username is materialized into
  `infra.components[].inputs.ssh_user_name` the same way the bundled
  jump-host modules do.
- Advanced disk/filesystem/container attachment shapes remain Terraform-native
  object/list inputs so they can still be edited as YAML/JSON in `config.yaml`
  when needed.

## Outputs Summary

- `instance_id`
- `boot_disk_id`
- `data_disk_ids`
- `gpu_cluster_id`
- `private_ip`
- `public_ip`
- `public_ip_allocation_id`
- `ssh_connect_command`

## Validation Commands

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
terraform -chdir=examples/gpu-preemptible init -backend=false
terraform -chdir=examples/gpu-preemptible validate
terraform -chdir=examples/containerized init -backend=false
terraform -chdir=examples/containerized validate
```
