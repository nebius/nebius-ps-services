# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/) with Git tags as the source of truth.

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before tagging, move items from `Unreleased` into a new
  `## [nebius-vpngw-vX.Y.Z] - YYYY-MM-DD` section, then leave an empty `Unreleased` section.
- Newer releases go above older ones; do not reorder entries within a release.
- The release helper (`publish-release.sh`) automates rolling `Unreleased` into a dated `## [nebius-vpngw-vX.Y.Z] - YYYY-MM-DD` and re-adding an empty `Unreleased`.

## [Unreleased]

- Fixed route command SSH handling so `add-routes-local` BGP route discovery and
  `list-routes-remote` gateway queries honor configured SSH user and private key
  settings.

## [nebius-vpngw-v0.5.8] - 2026-04-15

- Fixed explicit `gateway_group.external_ips` reuse for pre-created public IP allocations:
  - `apply` and `prep-network` now resolve existing public allocations by the requested IP in the current project before attempting to create a new one
  - explicit-IP runs now fail fast when the found allocation is still attached to another resource instead of warning and continuing
  - stale CLI-owned allocation names no longer silently override a different requested IP from YAML
  - removed cross-subnet public-allocation migration attempts; Nebius marks public allocation `subnet_id`, `cidr`, and `pool_id` immutable, so explicit-IP runs now require the allocation to already belong to the target gateway subnet
- Aligned CI/release wheel builds with the local `Makefile` build path so GitHub Actions suppresses the known transient `GlobalOverrides` warning during `python -m build --wheel --no-isolation`.

## [nebius-vpngw-v0.5.7] - 2026-04-08

- Fixed `add-routes-local` safety checks and output:
  - skip remote prefixes that overlap the target network's private pools before
    the Nebius API rejects them
  - sanitize inherited subnet status CIDRs against explicit CIDRs owned by
    other subnets before matching route-table targets, to avoid the Nebius
    inherited-pool display/API status bug
  - only treat an existing route as satisfied when the destination CIDR also
    points to the expected gateway allocation
  - when rerunning without `--summarize`, prune broader `vpngw-*` summaries
    after the exact desired routes under them are confirmed installed, so the
    command does not leave both summarized and exact managed routes behind
  - report connection-scoped BGP route counts instead of the raw FRR table size
- Added `add-routes-local --swap-route-table`:
  - builds a fresh custom route table per selected subnet
  - copies preserved non-`vpngw-*` routes from the currently attached table
  - rebuilds managed VPN routes from the current YAML before cutover
  - validates the replacement table before reattaching the subnet
  - requires explicit confirmation and writes rollback spec files plus exact
    `nebius vpc subnet update --file ...` rollback commands
  - updated live CLI `--help` text so operators see the validation-before-cutover
    and rollback-command behavior directly in `add-routes-local --help`
  - ignores local `.nebius-vpngw-rollbacks/` recovery artifacts and trims the
    confirmation warning to the traffic-impact/rollback guidance
- Clarified the `add-routes-local --summarize` documentation in `README.md`
  and `doc/design.md` with plainer wording and concrete CIDR examples so the
  exact merge behavior is easier to understand.

## [nebius-vpngw-v0.5.6] - 2026-04-07

- Fixed BGP route scoping for multi-connection gateways: `list-routes-remote`
  now shows only the selected connection's learned paths on the owning gateway
  VM instead of repeating the full FRR table for every connection, and
  `add-routes-local` now filters learned paths to that connection's tunnel
  peers before deriving Nebius VPC routes.
- Added `add-routes-local --summarize` for exact prefix collapsing per gateway
  next-hop allocation so large remote route sets can reduce Nebius route-table
  entry count without inventing broader supernets.
- Updated versioning configuration to the current `setuptools-scm`
  `semver-pep440` scheme and aligned runtime resolution/tests so `make all`
  no longer emits the renamed-scheme deprecation warning.
- Fixed multi-VM advertised-route labeling in `list-routes-local`: BGP peers are
  now matched to connections/tunnels using both peer IP and owning gateway VM,
  so reused APIPA ranges on different instances no longer cross-label output.
- Added regression coverage for representative multi-connection topologies,
  including the example 3-site single-VM and 3-site three-VM YAML layouts plus
  the explicit tunnel-selection behavior of `failover`/`failback`.
- Clarified live CLI `--help` text for multi-connection operation so
  `list-routes-remote`, `restart-tunnel`, `failover`, and `failback` now
  describe owning-VM scoping and when explicit tunnel selection is required.
- Aligned the `vpngw` GitHub Actions workflows with the service release path:
  CI now self-validates `vpngw` workflow YAML and exercises the wheel-build
  regression test before release publication, and both workflows use explicit
  Bash defaults for consistency with the monorepo pattern.

## [nebius-vpngw-v0.5.5] - 2026-03-31

- Added regression coverage proving `publish-release.sh --prep` remains
  idempotent for unreleased versions: reruns for the same version now stay
  no-op once `Unreleased` is empty and the tag has not been created.

- Fixed `publish-release.sh --prep` changelog formatting so moving
  `Unreleased` notes into a dated release section preserves a blank line before
  the next `##` heading, keeping the file markdownlint-safe in editors.

- Changed `publish-release.sh --prep` to fail before editing `CHANGELOG.md` if
  the target tag already exists locally or on `origin`, so duplicate release
  preparation for an already-published version stops immediately.
- Fixed source-checkout runtime version fallback for release tagging without
  `setuptools-scm` installed: `nebius_vpngw.__version__` now derives from
  `git describe` before consulting a generated `_version.py`, so
  `publish-release.sh --publish` no longer rejects a fresh exact tag because of
  a stale local dev-version cache.

- Fixed `add-routes-local` for pinned multi-VM topologies: remote prefixes are
  now routed through the gateway VM that owns each connection, and BGP route
  discovery is scoped to the owning VM(s) instead of querying every gateway VM.
- Fixed `restart-tunnel <name>` for multi-VM topologies: it now targets only
  the gateway VM that owns the selected tunnel, fails fast when the tunnel name
  is unknown, and has regression coverage alongside the existing manual
  `failover`/`failback` command paths.
- Simplified manual `failover` and `failback` tunnel selection: both commands
  now take the tunnel name as an optional positional argument instead of
  `--tunnel-failover` / `--tunnel-failback`, which matches `restart-tunnel` and
  relies on schema-enforced global tunnel-name uniqueness.
- Clarified manual failover semantics in both UX and docs: `failover` now
  explicitly remains an operational override that preserves configured YAML
  roles, and `status` now reports configured role separately from current
  traffic state with a `Traffic Override` panel when runtime behavior differs
  from the configured active/passive preference.
- Aligned `publish-release.sh --prep` with the shared release-template behavior:
  it now requires a named branch and auto-configures `origin/<current-branch>`
  as upstream on the first push instead of failing with Git's default upstream
  error.
- Tightened local release gating in `publish-release.sh`: the clean-worktree
  check now includes untracked files, and `--publish` now fails before tagging
  if the target release section exists but is empty.

- Pinned `Pygments>=2.20.0,<3.0.0` directly in project metadata and refreshed
  `uv.lock` so runtime installs, dev/test environments, and generated wheel
  metadata no longer permit the vulnerable transitive version.
- Fixed `apply` agent deployment for wheel-based installs: when a fresh local build is unavailable,
  SSH push now falls back to the originally installed wheel recorded in pip
  `direct_url.json` (including direct GitHub release URLs and local wheel files)
  instead of requiring `python -m build`.
- Cleaned up version packaging/runtime wiring: source checkouts now pass the
  non-deprecated nested `scm.git.describe_command` config to `setuptools-scm`,
  and wheel builds now use a package-local `version_file` so release artifacts
  no longer include a duplicate repo-relative `_version.py`.
- Changed the local developer `make build`/`make all` path to reuse the prepared
  project virtualenv (`python -m build --wheel --no-isolation`), which avoids
  noisy isolated-build `vcs_versioning` warnings while keeping local artifacts
  deterministic.
- Fixed runtime version resolution for source/editable checkouts so `nebius-vpngw` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified BFD documentation and comments: support is now described as vendor/platform specific, the template/README no longer imply generic cloud-VPN support, and the misleading GCP HA VPN BFD note was removed.
- Added concise Nebius Managed Kubernetes routing guidance covering `gateway.local_prefixes`, Pod-vs-ClusterIP expectations, and the common Cilium routing/masquerade defaults operators should account for over VPN.

## [nebius-vpngw-v0.5.4] - 2026-03-16

- Tightened multi-connection validation and template guidance: tunnel names must now be globally unique, APIPA tunnel ranges and BGP inner IPs must be unique per gateway instance, and the generated config/docs now clarify the supported multi-site active/passive workflow.
- Improved `status` for multi-connection gateways: `Carrying Traffic` is now computed per connection, and live FRR multipath across overlapping prefixes is surfaced as an `ECMP Warning` that names the prefix and the active tunnels carrying it.

## [nebius-vpngw-v0.5.3] - 2026-03-10

- Made the output path optional for `create-from-peer-config`; when omitted, the generated config now defaults to `./nebius-vpngw.config.yaml`.
- Added `--local-config-file` as an output-file alias for `create-from-peer-config`, with fail-fast validation if it conflicts with the positional output path.

## [nebius-vpngw-v0.5.2] - 2026-03-08

- Expanded the pytest-based test suite, split unit/integration coverage, centralized test config in `pyproject.toml`, and added `Makefile` targets plus service-scoped CI.
- Hardened operational CLI commands: `restart-tunnel` now performs a full IPsec and matching-BGP reset, and `failover`/`failback` were tightened and validated against the active/passive HA flow.
- Improved route management for Nebius workload subnets that inherit parent network pools, and added live BGP advertisement reconciliation so route commands reflect the current YAML instead of stale FRR state.
- Switched releases to the monorepo service pattern: `publish-release.sh` now handles prep/tagging, `vpngw-ci.yml` is PR/manual only, and `vpngw-release.yml` is the dedicated tag-driven GitHub Release workflow for `nebius-vpngw-v*`.

## [nebius-vpngw-v0.5.1] - 2026-02-04

- Fail fast when `--local-config-file` is provided but the config path does not exist.
- Fail fast for `list-routes-local` when gateway VMs are missing, and avoid traceback leaks on route listing errors.
- Inline `inner_cidr` `/30` guidance in the generated config template.
- Added `prep-network` command to create `vpngw-subnet`, reserve public IPs, and write them into `gateway_group.external_ips` (or allocate requested IPs when provided).
- `prep-network` now waits briefly and retries when a requested IP is still releasing.
- Status now uses BGP session uptime (from `show bgp summary`) when available.

## [nebius-vpngw-v0.4.9] - 2026-02-02

- Updated the release tagging to the prefixed format so tags include the app name (e.g., nebius-vpngw-v0.4.9), matching the multi-project release style.

## [v0.4.8] - 2026-01-20

- Adjusted SSH deploy to avoid rebuilding wheels for pipx/release installs and to prefer local release wheels when applying.
- Made SSH usage more Windows-friendly with OpenSSH presence checks and OS-aware null device handling.
- Updated install docs to emphasize downloading release wheels (Windows) and local wheel usage for pipx installs.

## [v0.4.7] - 2026-01-12

- Added `defaults.ha_mode` (active-passive default) and schema validation enforcing exactly one active tunnel per connection per gateway instance.
- Ensured passive tunnels include `gateway.local_prefixes` in traffic selectors so failover carries data, backed by swanctl/VICI `if_id` binding.
- Switched strongSwan rendering to swanctl (VICI) for deterministic XFRM interface binding and updated docs accordingly.
- Added manual `failover` and `failback` commands with BGP confirmation + elapsed time reporting; status now displays admin-down neighbors as `Down (Admin)`.
- Status output now includes tunnel role, carrying-traffic indicator, encryption, and d:h:m:s uptime, with swanctl de-duplication; list-routes-local shows role labels.
- Improved swanctl/VICI load reliability with socket readiness checks and retries.
- Updated defaults/template: IKEv1 disabled, SHA1/MODP1024 removed, BFD support kept optional (bfdd toggled when enabled) with default disabled, DPD/BGP timers set to 5/15 and 2/6, health monitor interval 10s with ping disabled, and `gateway.ipsec_mode` explicit; condensed template comments.
- Health monitor defaults/docs now reflect 10s checks and faster detection timing; ping checks remain optional.

## [v0.4.6] - 2026-01-10

- Added health monitor improvements: respect `health_monitoring.ping_enabled`, detect stale XFRM tunnels via error-counter deltas, and guard against duplicate monitor instances.
- Updated health monitor systemd unit to use a runtime directory and writable path for the lock under `/run/nebius-vpngw`.
- Fixed `restart-tunnel` ImportError by using the resolved plan merge path in the CLI.
- Avoided overlapping XFRM policies in HA by excluding local prefixes from passive tunnel `leftsubnet`.
- Stabilized FRR installation on Ubuntu 24.04 by removing the pinned package version and adding an apply-time install fallback.

## [v0.4.5] - 2026-01-07

- Added Active/Passive HA support with BGP MED (Multi-Exit Discriminator) and local-preference for bidirectional path control.
- Active tunnels use MED=0 and local-pref=200; passive tunnels use MED=100 and local-pref=100 for deterministic routing.
- Disabled `ensure_local_prefix_routes()` in frr_renderer.py and routing_guard.py to prevent routes that break packet forwarding.
- Added `no bgp network import-check` to BGP configuration to allow prefix advertisement without kernel routes.
- Comprehensive MED documentation added to design.md with verification commands for both Nebius and peer sides.
- Enhanced Project Structure documentation in design.md and README.md with all agent modules and systemd components.
- Added MTU/MSS hardening for XFRM gateways: TCP MSS clamp, TCP MTU probing, ICMP frag-needed allowances, and explicit XFRM MTU calculation.
- Ensured XFRM interfaces and local prefix routes are enforced even when config is unchanged; state tracking now uses render version.
- Routing guard now canonicalizes internal CIDR routes (dedup + onlink metric), flushes route cache after fixes, and uses a shared lock to prevent concurrent enforcement.
- Fix-routes timer now runs the Python entrypoint with systemd ordering/conditions and config path; legacy fix-routes shell script removed.
- Deployment updates: firewall setup script externalized, systemd assets staged via SSH push, and agent restart/reload logic refined.
- XFRM IP assignment made idempotent via `ip addr replace`.

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
