# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/) with Git tags as the source of truth.

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before tagging, move items from `Unreleased` into a new
  `## [X.Y.Z] - YYYY-MM-DD` section, then leave an empty `Unreleased` section.
- Newer releases go above older ones; do not reorder entries within a release.
- The release script (`release.sh`) automates rolling `Unreleased` into a dated `## [vX.Y.Z] - YYYY-MM-DD` and re-adding an empty `Unreleased`.

## [Unreleased]

## [v0.2.0] - 2025-12-18
### Added

- Git tag–driven versioning via `setuptools-scm` with a `--version` CLI flag that surfaces the tagged release.
- Clear install paths for end users (pipx + GitHub release wheel) and developers (editable install).
- Release workflow guidance for tagging, building, and publishing wheels with GitHub CLI.

## [0.1.0] - 2025-12-17

### Added

- Initial public release of the Nebius VM-based VPN Gateway orchestrator and agent.
- YAML schema validation with embedded config template generation.
- Multi-cloud peer support (GCP HA VPN, AWS Site-to-Site, Azure VPN Gateway, Cisco IOS).
- Agent-side routing guard, XFRM interface management, and UFW synchronization.

### Changed

### Fixed
