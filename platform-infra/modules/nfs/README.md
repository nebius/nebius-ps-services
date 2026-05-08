# nfs module

Reusable Terraform module that creates a VM-based Nebius NFS server.

Resources managed:

- `nebius_compute_v1_disk` for the boot disk
- optional `nebius_compute_v1_disk` for exported data
- `nebius_compute_v1_instance`

Out of scope:

- Kubernetes `PersistentVolume` and `PersistentVolumeClaim` objects
- Helm releases or in-cluster NFS provisioners
- Soperator `SlurmCluster` or `NodeSet` resources

Those in-cluster references are Helm/cxcli-owned.

## Usage

```hcl
module "nfs" {
  source = "./platform-infra/modules/nfs"

  parent_id           = "project-xxxxxxxx"
  name                = "client-a-nfs"
  subnet_id           = "vpcsubnet-xxxxxxxx"
  source_image_family = "ubuntu24.04"
  ssh_public_key      = var.ssh_public_key

  export_path = "/srv/nfs/home"
  client_cidrs = [
    "10.0.0.0/8",
  ]
}
```

The module exports a private NFS endpoint by default. Use security groups and
`client_cidrs` to restrict client access to the intended VPC/subnet ranges.

The module is VM-based and does not create an MK8s node group. It has no
Soperator-specific node-role names baked in. Callers choose the VM name,
export path, client CIDRs, optional data disk, and any Nebius shared
filesystems to attach through variables. `nebius-cxcli` can then bind the
`server_ip` and `export_path` outputs into Helm values.

## Inputs summary

- Required:
  - `parent_id`
  - `name`
  - `subnet_id`
  - `source_image_family` or `source_image_id`
  - `ssh_public_key`
- VM shape:
  - `platform`
  - `preset`
  - `boot_disk_size_gib`
  - `boot_disk_type`
- Network:
  - `public_ip_mode`
  - `public_ip_allocation_id`
  - `private_ip_allocation_id`
  - `security_group_ids`
- NFS:
  - `export_path`
  - `client_cidrs`
  - `mount_options`
  - `export_options`
  - `data_disk`
  - `filesystems`

## Outputs summary

- `instance_id`
- `server_ip`
- `public_ip`
- `export_path`
- `mount_options`
- `export_options`
- `export_spec`

`server_ip`, `export_path`, and `mount_options` are the stable outputs intended
for cxcli/chart value binding.

## Validation commands

Runnable examples:

- `examples/minimal`: NFS VM with a module-managed data disk.
- `examples/with-filesystems`: NFS VM that exports caller-provided attached
  Nebius shared filesystems.

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
terraform -chdir=examples/with-filesystems init -backend=false
terraform -chdir=examples/with-filesystems validate
```
