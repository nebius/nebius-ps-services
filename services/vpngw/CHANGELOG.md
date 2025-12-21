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

## [v0.4.4] - 2025-12-21

- Added `create-from-peer-config` command to generate YAML from vendor peer files; removed `--peer-config-file` from `apply`.

## [v0.4.3] - 2025-12-20

- Enforced nested `gateway_group.external_ips` (list of lists) in schema and removed legacy flat-list handling.
- Static routing now requires `remote_prefixes` (connection-level or per-tunnel); example configs updated accordingly.
- Firewall setup aligned with XFRM BGP: TCP/179 not exposed on eth0, with tunnel-interface allowances and ICMP handling clarified.
- Embedded config template/docs refreshed; redundant template file removed and `*.config.yaml` ignored.

## [v0.4.2] - 2025-12-19

- Secrets file logging no longer prints the file path (CodeQL clear-text logging).

## [v0.4.1] - 2025-12-19

- Deployment no longer attempts Poetry builds; wheel build uses `python -m build --wheel` only.
- Hardened strongSwan secrets write: atomic file update, 0600 perms, CodeQL justification.
- SSH push install now verifies installed version via import metadata and uses a concise success log.
- Ruff lint configuration added with project-specific ignores/exclusions (including generated `_version.py`).
- Release script changelog update keeps a blank line between release headers and content.

## [v0.4.0] - 2025-12-19

- fix changelog update issue in the release.sh script

## [v0.3.0] - 2025-12-19

- Added new flags to the release.sh script (get --help please)
- Reformated README.md and doc/design.doc
- Removed the Poetry build path so nebius-vpngw apply always builds with python -m build --wheel. This avoids the likely Poetry failure with the current pyproject.toml.

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
