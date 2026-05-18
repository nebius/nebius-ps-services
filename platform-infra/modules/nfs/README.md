# nfs module

Reusable Terraform module that creates a VM-based Nebius NFS server.

Resources managed:

- `../vm` module for the boot disk, optional managed data disk, network
  interface, and Compute instance
- NFS-specific cloud-init user data

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
  platform            = "cpu-d3"
  preset              = "2vcpu-8gb"
  source_image_family = "ubuntu24.04"
  boot_disk_size_gib  = 64
  ssh_public_key      = var.ssh_public_key

  export_path = "/srv/nfs/home"
  client_cidrs = [
    "10.0.0.0/8",
  ]

  data_disk_size_gib = 128
  data_disk_type     = "NETWORK_SSD"
}
```

The module exports a private NFS endpoint by default. Use security groups and
`client_cidrs` to restrict client access to the intended VPC/subnet ranges.

## Availability guidance

This module is intentionally a simple, single-VM NFS bridge. It does not create
an HA NFS cluster, a floating service IP, fencing, a replicated NFS server
control plane, or automatic failover. A replicated Nebius disk type such as
`NETWORK_SSD_IO_M3` can improve backing disk durability, but the NFS service,
guest OS, VM network interface, and exported `server_ip` still depend on one
Compute instance.

Use this module for tests, demos, short-lived environments, or compatibility
cases where an NFS protocol export is explicitly required. For production or
long-lived Kubernetes RWX storage, prefer direct Nebius Shared Filesystem
(SFS) with the [Nebius shared-filesystem CSI
path](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi) instead
of adding an NFS VM gateway in front of SFS.

The module is VM-based and does not create an MK8s node group. It has no
Soperator-specific node-role names baked in. Callers choose the VM name,
export path, client CIDRs, optional data disk, and any Nebius shared
filesystems to attach through variables. The module delegates Compute VM,
boot-disk, secondary-disk, network-interface, and filesystem attachment
resources to `../vm`, the same base module used by the SSH jump-host and
WireGuard gateway wrappers. `nebius-cxcli` can then bind the `server_ip` and
`export_path` outputs into Helm values.

For cxcli-managed MK8s consumption, set `kubernetes_target_ref` when several
NFS exports exist and one export should bind to one cluster target. If there is
only one enabled NFS component in a config, cxcli can use that export for every
enabled MK8s target without this hint.

The default data path creates one secondary Compute disk and mounts it by the
stable `/dev/disk/by-id/virtio-<data_disk_device_id>` path before exporting
it. This follows the Nebius storage guidance to use device IDs instead of
ephemeral `/dev/vd*` names when mounting attached disks. If you export existing
shared filesystems instead, set `data_disk_enabled = false` and provide
`filesystems`. For `NETWORK_SSD_NON_REPLICATED` or `NETWORK_SSD_IO_M3`, choose
`data_disk_size_gib` in 93 GiB allocation units.

The default export model is Kubernetes CSI friendly: the exported directory is
owned by numeric `storage_uid:storage_gid`, has setgid permissions, and uses
`root_squash` with `anonuid`/`anongid` derived from those same numeric IDs.
That lets the NFS CSI driver create PVC subdirectories without granting
client-side root full server-side root privileges. If workloads use different
numeric identities, adjust `storage_uid`, `storage_gid`, `export_permissions`,
or provide explicit `export_options`.

## Inputs summary

- Required:
  - `parent_id`
  - `name`
  - `subnet_id`
  - `platform`
  - `preset`
  - exactly one boot source: `source_image_family`, `source_image_id`, or `boot_disk_existing_id`
  - `boot_disk_size_gib` when the module creates the boot disk
  - `ssh_public_key`
- VM shape:
  - `platform`
  - `preset`
  - `boot_disk_size_gib`
  - `boot_disk_type`
  - `boot_disk_encryption_enabled`
  - `boot_disk_deletion_protection`
- Network:
  - `public_ip_mode`
  - `public_ip_allocation_id`
  - `private_ip_allocation_id`
  - `security_group_ids`
- NFS:
  - `export_path`
  - `storage_uid`
  - `storage_gid`
  - `export_permissions`
  - `client_cidrs`
  - `kubernetes_target_ref`
  - `mount_options`
  - `export_options`
  - `data_disk_enabled`
  - `data_disk_size_gib`
  - `data_disk_type`
  - `data_disk_encryption_enabled`
  - `data_disk_deletion_protection`
  - `filesystems`

## Outputs summary

- `instance_id`
- `boot_disk_id`
- `data_disk_ids`
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
