# vm module

Reusable Terraform module that creates one Nebius Compute virtual machine with
explicit platform/preset selection.

This module requires Nebius Terraform provider `>= 0.5.217, < 0.6.0` so
preemptible VM instances can omit the deprecated Compute preemptible priority
field.

Resources managed:

- `nebius_compute_v1_instance` (VM)
- optional `nebius_compute_v1_disk` resources (boot disk and managed data disks)
- optional `nebius_compute_v1_gpu_cluster` (when creating a new GPU cluster)

Out of scope:

- application orchestration beyond a single optional bootstrap container
- observability agents, collectors, service accounts, or write endpoints;
  Nebius installs the built-in VM Monitoring agent outside this module
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
  - `boot_disk_size_gib` is required when the module creates the boot disk
  - `boot_disk_type`
  - `boot_disk_encryption_enabled`
  - `boot_disk_deletion_protection`
  - `data_disk_enabled`
  - `data_disk_size_gib`
  - `data_disk_type`
  - `data_disk_encryption_enabled`
  - `data_disk_deletion_protection`
  - `data_disks`
  - `existing_data_disks`
  - `filesystems`
- GPU and lifecycle:
  - `preemptible_enabled`
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

The module does not calculate a boot-disk size from `preset`. Direct Terraform
callers set `boot_disk_size_gib` explicitly; `nebius-cxcli` resolves the live
platform/preset metadata, applies its shared `compute.boot_disk_defaults`
policy, and renders the recommended value into generated Terraform.

Disk security controls map directly to the Nebius disk API fields. Set
`boot_disk_encryption_enabled=true` only for module-created
`NETWORK_SSD_NON_REPLICATED` or `NETWORK_SSD_IO_M3` boot disks; `NETWORK_SSD`
is encrypted by the platform. Set `boot_disk_deletion_protection=true` to
enable provider-side deletion protection on the module-created boot disk. Both
controls are invalid with `boot_disk_existing_id` because the module is not
creating that disk.

For the common single secondary-disk case, set `data_disk_enabled=true` and
choose `data_disk_size_gib` plus `data_disk_type`. The module creates and
attaches that disk through the same managed-disk path as `data_disks`.
`data_disk_encryption_enabled` and `data_disk_deletion_protection` map to the
same Nebius disk API fields as the boot disk. Explicit encryption is valid only
for `NETWORK_SSD_NON_REPLICATED` and `NETWORK_SSD_IO_M3`; `NETWORK_SSD` is
encrypted by the platform. Nebius high-performance SSD types use 93 GiB
allocation units, so choose data-disk sizes that match the selected disk type.
Use `data_disks` only when you need more than one managed disk or per-disk
object-level customization, and use
`existing_data_disks` for caller-owned disks.

## VM Type Semantics

- Regular VMs: supported for CPU and GPU platforms.
- Preemptible VMs: the module exposes these with `preemptible_enabled=true`,
  but only for GPU platforms and only with `recovery_policy=FAIL`, matching the
  Nebius documented contract. The module intentionally omits the deprecated
  Compute preemptible priority field; Nebius derives actual preemption priority
  outside the instance spec.
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
  `nebius-cxcli` VM and public-access wrapper wizards auto-materialize it from
  the live Nebius public image inventory for the selected platform and region,
  ranking families marked by Nebius as recommended ahead of other compatible
  families.
- The shared admin SSH username is materialized into
  `infra.components[].inputs.ssh_user_name` the same way the bundled
  public-access wrappers do.
- `inputs.ssh_public_key` may be entered as inline public key text or a readable
  local `.pub` path when using `nebius-cxcli`; direct Terraform module callers
  must pass inline OpenSSH public key text.
- `cloud_init_user_data_override` is intended for wrapper modules such as
  `ssh-jumphost`, `wireguard-gw`, and `nfs` that should reuse this module's VM
  resource model while owning a specialized cloud-init payload.
- Nebius does not allow `cloud_init_user_data` to be updated on a running VM.
  The module ignores in-place `cloud_init_user_data` diffs and uses a separate
  cloud-init hash trigger so future rendered cloud-init changes replace the
  module-created boot disk and instance instead of attempting a rejected update
  or reusing old cloud-init state.
- `nebius-cxcli` prompts the guided single secondary-disk fields directly.
  Advanced multi-disk, existing-disk, filesystem, and container attachment
  shapes remain Terraform-native object/list inputs so they can still be edited
  as YAML/JSON in `config.yaml` when needed.
- VM observability in `nebius-cxcli` uses Nebius' built-in VM Monitoring agent:
  service-provider metrics are collected automatically by Nebius, and journald
  logs are enabled by cxcli-managed VM labels when
  `deploy.observability.vm.logs.enabled=true`. This module does not install a
  collector, create observability service accounts, or configure public write
  endpoints.

## Outputs Summary

- `instance_id`
- `boot_disk_id`
- `data_disk_ids`
- `gpu_cluster_id`
- `private_ip`
- `public_ip`
- `public_ip_allocation_id`
- `service_account_id`
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
