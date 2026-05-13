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
