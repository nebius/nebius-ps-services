# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added `modules/vm`, a reusable Nebius Compute VM module with explicit
  platform/preset selection, support for regular and preemptible GPU VMs,
  optional GPU cluster creation/attachment, optional data disks/filesystems,
  and an optional Docker-based container bootstrap path for
  `nebius-cxcli` onboarding.

### Changed

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
