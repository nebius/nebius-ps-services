# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

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
