# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added `modules/vm`, a reusable Nebius Compute VM module with explicit
  platform/preset selection, support for regular and preemptible GPU VMs,
  optional GPU cluster creation/attachment, optional data disks/filesystems,
  and an optional Docker-based container bootstrap path for
  `nebius-cxcli` onboarding.

### Fixed

- Fixed the `modules/vm` container-image-family precondition so live
  `terraform apply` correctly validates Ubuntu-based image family names instead
  of failing on Terraform string handling.
- Fixed the `modules/vm` container bootstrap cloud-init template so Docker repo
  setup uses real shell command substitution for guest architecture detection
  and works on live Ubuntu 24.04 VMs.
- Updated the `modules/vm` GPU examples and README guidance to use current live
  Nebius GPU platform/image combinations, including `ubuntu24.04-cuda13.0` for
  the current `580.x` driver line.
