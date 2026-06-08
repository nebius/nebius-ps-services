# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added `modules/vpc`, a reusable Nebius VPC module that can create a new
  VPC network with optional subnets or create declared subnets under an
  existing VPC network for planned-resource wiring through `nebius-cxcli`.
- Added `modules/vm`, a reusable Nebius Compute VM module with explicit
  platform/preset selection, support for regular and preemptible GPU VMs,
  optional GPU cluster creation/attachment, optional data disks/filesystems,
  and an optional Docker-based container bootstrap path for
  `nebius-cxcli` onboarding.
- Added direct Terraform examples for flexible Soperator-adjacent module
  surfaces: `modules/mk8s/examples/generic-node-groups` demonstrates an
  arbitrary caller-owned `node_groups` map, and
  `modules/sfs/examples/named-filesystems` and
  `modules/nfs/examples/with-filesystems` demonstrate caller-owned filesystem
  maps/lists.

### Changed

- Switched every Terraform module and example root to the official public
  Nebius Terraform provider source `nebius/nebius` with the shared constraint
  `>= 0.6.8, < 0.7.0`, and refreshed checked-in example lockfiles to provider
  `0.6.8`.
- Added required `network_id` inputs to the subnet-attached VM-style modules
  (`modules/vm`, `modules/nfs`, `modules/wireguard-gw`, and
  `modules/ssh-jumphost`) and added Terraform data-source preconditions so
  direct Terraform plans fail when the selected VPC subnet does not belong to
  the selected VPC network/project. The affected subnet-attached
  compute/MK8s modules and examples now share the Nebius provider floor
  `>= 0.6.8, < 0.7.0`.
- Tightened `modules/mk8s` VPC validation: the module now validates the
  selected cluster network/subnet relationship, checks node-group subnet
  overrides against that network, and requires explicit
  `network_interfaces[*].subnet_id` values when callers provide explicit
  network-interface mappings.
- Aligned `modules/vpc` with Nebius VPC addressing semantics: new networks now
  require `network.ipv4_private_cidrs` or `network.ipv4_private_pool_ids`, the
  module creates and attaches private pools for declared network CIDRs, accepts
  `network.ipv4_private_source_pool_id` for source-pool-backed managed pools,
  and validates supplied private and public pool IDs. Every declared subnet now
  uses explicit private child CIDRs with `use_network_private_pools=false`;
  public pool IDs remain optional because Nebius attaches the default public
  pool to new networks when none is specified. Explicit child subnet CIDRs are
  checked during planning so they fit declared network CIDRs or attached
  existing private-pool CIDRs, including private pools on
  `network.existing_id`, and do not overlap each other. Plans with declared
  subnets now fail when parent private CIDR ranges cannot be resolved from the
  selected network or pools. The VPC module now also exposes Nebius-reported
  default route-table and effective network pool metadata in outputs.
- Removed the deprecated Compute preemptible priority field from `modules/vm`:
  preemptible VM instances now render `on_preemption = "STOP"` without exposing
  or setting `preemptible_priority`, and the VM module now requires Nebius
  Terraform provider `>= 0.6.8`.
- Aligned `modules/mk8s` disabled object semantics and GPU stack defaults:
  disabled `node_groups` / `gpu_clusters` entries may now omit enabled-only
  fields, and GPU node groups default to the Nebius image stack while requiring
  `gpu_stack_preset` only for enabled GPU groups on that path.
- Tightened `modules/mk8s` node-group autoscaling typing and normalization:
  enabled node groups now require either `node_count` or enabled autoscaling,
  autoscaling validates integer min/max bounds, and an explicit
  `autoscaling.enabled = false` helper block is normalized away before provider
  rendering.
- Typed `modules/mk8s` node-group `strategy` objects to match the Nebius
  provider schema, so callers can set a temporary strategy on one node group
  without Terraform rejecting the heterogeneous `node_groups` map.
- Redesigned `modules/mk8s` around required typed `cluster` and `node_groups`
  inputs. The module now separates cluster provisioning from node-group shape,
  reservation, SSH, service account, GPU cluster, and filesystem attachment
  data, and removes the previous CPU/GPU shortcut variables without a
  compatibility shim.
- Clarified that `modules/nfs` is a non-HA, single-VM NFS bridge intended for
  tests, demos, short-lived environments, or explicit NFS compatibility cases;
  production or long-lived Kubernetes RWX storage should use direct Nebius SFS.
- Refactored `modules/nfs` into a thin wrapper around `modules/vm`: the shared
  VM module now owns the Compute instance, boot disk, secondary data disk,
  network interface, filesystem attachments, and disk security controls, while
  the NFS module owns only NFS-specific cloud-init and export metadata. The old
  nested `data_disk` object is replaced by first-class `data_disk_*` inputs with
  no compatibility shim.
- Hardened the default NFS export model for Kubernetes CSI use: exported paths
  now use numeric storage UID/GID ownership, setgid permissions, and
  `root_squash` with anon UID/GID values derived from the module storage
  identity instead of permissive client-root defaults.
- Added optional `kubernetes_target_ref` metadata for cxcli-managed configs
  that need to bind one VM-backed NFS export to one MK8s target when multiple
  NFS exports are enabled.
- Added first-class guided single secondary-disk inputs to `modules/vm`
  (`data_disk_enabled`, `data_disk_size_gib`, `data_disk_type`, encryption, and
  deletion protection) while keeping `data_disks` for explicit multi-disk
  Terraform callers. High-performance secondary disk sizes should follow the
  selected disk type's allocation unit.
- Changed `modules/vm` standalone observability collector identity handling so
  the module can create the dedicated VM-attached service account, IAM permit
  group, group membership, and configured project role grants during Terraform
  apply when callers omit `service_account_id`.
- Added boot/data disk security controls to `modules/vm`: module-created disks
  can set Nebius managed encryption for SSD NRD / SSD IO M3 and deletion
  protection. SSH and WireGuard VPN gateway wrappers pass the same boot-disk
  controls through to the shared VM module.
- The WireGuard Terraform module directory is now `modules/wireguard-gw` to
  match its role as a point-to-site VPN gateway. The module README, examples,
  labels, and cxcli-facing source path now use the gateway name with no
  Terraform compatibility shim.
- Removed the static `60` GiB boot-disk default from the VM, SSH jump-host, and
  WireGuard VPN gateway module contracts. Direct Terraform callers now set
  `boot_disk_size_gib` explicitly, while `nebius-cxcli` renders a
  platform/preset-based recommendation from its shared Compute boot-disk policy.
- Refactored `modules/ssh-jumphost` and `modules/wireguard-gw` into
  thin wrappers around `modules/vm`: the shared VM module now owns Compute
  disk/instance/network behavior, while the wrappers own only their
  static public IP allocation plus SSH bastion or WireGuard VPN gateway
  cloud-init payloads. Wrapper callers must now set `platform`, `preset`, and
  `source_image_family` explicitly, matching the generic VM contract and
  avoiding module-local hardcoded image or shape defaults.
- Removed Terraform `moved` compatibility blocks from the SSH and WireGuard
  public-access wrappers so new deployments have one canonical VM-module-backed
  state address without legacy resource-address adoption.
- Refactored `modules/wireguard-gw` so day-2 WireGuard client generation
  is handled by a VM-local command instead of fixed first-boot cloud-init peer
  rendering. Client seed fields are now
  `client_wg_tunnel_address`/`local_subnets`, with automatic tunnel address
  allocation when the address is omitted.
- Added VM-local WireGuard day-2 add/remove commands for default
  `local_subnets`, and renamed the top-level default input from
  `client_default_local_subnets` to `local_subnets` without a compatibility
  shim.
- Refactored `modules/ssh-jumphost` to install a VM-local
  `nebius-ssh-jumphost` helper for day-2 SSH source CIDR add/remove/list
  operations. Cloud-init now seeds the initial allowlist from `allowed_cidrs`
  and leaves later changes in `/var/lib/nebius-ssh-jumphost/` instead of
  treating source CIDR changes as fixed first-boot script content.
- Hardened the public WireGuard VPN gateway cloud-init path to match the SSH
  jump-host security posture where applicable: key-only admin SSH, no SSH
  forwarding on the WireGuard host, fail2ban, auditd watches, unattended
  security upgrades, stricter IPv4 forwarding sysctls, and a narrower routed
  UFW policy that preserves client-initiated private VPC access.
- Changed the WireGuard VPN gateway default tunnel CIDR to `10.8.0.1/22`,
  defaulted generated client DNS to `1.1.1.1` and `1.0.0.1`, and added
  automatic `component`/`name` labels to SSH and WireGuard VPN gateway wrapper
  resources.
- Clarified the `mk8s`, `sfs`, and `nfs` module docs so Soperator names such
  as `system`, `controller`, `login`, `accounting`, `jail`, and
  `controller-spool` are documented as cxcli profile conventions, not
  hardcoded Terraform module resources.
- Removed the internal `cpu_nodes_count = 2` default from `modules/mk8s` so
  direct Terraform consumers must choose the baseline CPU node-group size
  explicitly.
- Removed internal `enabled` gates from `modules/managed-postgresql` and
  `modules/sfs` so callers control deployment by including or omitting the
  module instance from the generated Terraform root.
- Refactored `modules/object-storage` to manage one bucket per module instance
  instead of multiplexing buckets through one module call.
- Changed the greenfield `modules/mysterybox` contract from one optional
  `version` value per secret to a `versions` map, with runtime payload values
  keyed by secret id, version id, and payload key.
- Tightened `modules/mysterybox` to Terraform `>= 1.11.0` to match Nebius
  provider write-only payload fields, aligned its inputs to the MysteryBox
  secret/version/payload model, documented the product contract, and made
  `make validate` cover example roots with readonly lock files. The module now
  creates one initial primary version per secret, records the current primary
  `version_id` as metadata, accepts `text`/`file` payload entry types, and
  expects runtime payload values as `{secret_name={payload_key=value}}`. The
  minimal example now sets the Nebius provider `parent_id` explicitly and the
  module docs call out target-scoped provider profiles after live apply/destroy
  validation against Nebius Cloud.

### Fixed

- Tightened `modules/vpc`, `modules/ssh-jumphost`, and
  `modules/wireguard-gw` VPC validation so an existing VPC network and
  wrapper-owned public IP allocations validate the selected
  `network_id`/`subnet_id` relationship before dependent resources are
  created.
- Fixed `modules/mk8s` VPC validation so planned VPC subnet IDs produced by
  `modules/vpc` can be used during Terraform planning; subnet data sources
  are now keyed by stable logical references instead of apply-time subnet IDs.
- Fixed `modules/vm` lifecycle handling so rendered cloud-init changes are
  driven by a replacement trigger for the module-created boot disk and instance
  instead of an in-place `user_data` update that Nebius rejects on running
  instances.
- Fixed `modules/ssh-jumphost` cloud-init so CIDRs rendered through YAML block
  indentation are trimmed before UFW rules are applied.
- Fixed `modules/wireguard-gw` planning when `endpoint_host` is left null
  for cloud-init public endpoint auto-detection.
- Fixed `modules/wireguard-gw` cloud-init heredoc rendering so generated
  WireGuard client sections do not break under `set -u`.
- Fixed `modules/wireguard-gw` cloud-init SSH service detection under
  `set -o pipefail` and forced IPv4 forwarding after host sysctl defaults are
  applied so Ubuntu images finish setup and start `wg-quick`.
- Fixed VM, SSH jump-host, and WireGuard VPN gateway bootstrap timing by creating
  `/run/sshd` before validating SSH configuration on fresh Ubuntu images.
- Fixed `modules/wireguard-gw` cloud-init so the admin SSH user can run
  the documented `sudo` commands without a password and SSH password
  authentication is explicitly disabled.
- Fixed `modules/mk8s` so `infiniband_fabric` only creates the built-in GPU
  cluster when built-in `gpu_node_groups` are enabled. Generic Soperator GPU
  worker groups now use the caller-provided `gpu_clusters` map without creating
  an unused extra GPU cluster.
- Fixed `modules/mysterybox` rerun and destroy behavior after `version_id` is
  recorded so callers no longer need to re-supply original runtime
  `payload_values` only to satisfy provider validation for ignored write-only
  payload fields.
- Fixed the `modules/vm` container-image-family precondition so live
  `terraform apply` correctly validates Ubuntu-based image family names instead
  of failing on Terraform string handling.
- Fixed the `modules/vm` container bootstrap cloud-init template so Docker repo
  setup uses real shell command substitution for guest architecture detection
  and works on live Ubuntu 24.04 VMs.
- Updated the `modules/vm` GPU examples and README guidance to use current live
  Nebius GPU platform/image combinations, including `ubuntu24.04-cuda13.0` for
  the current `580.x` driver line.
