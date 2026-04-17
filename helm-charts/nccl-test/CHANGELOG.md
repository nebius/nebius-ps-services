# Changelog

All notable changes to this chart are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before release tagging, move `Unreleased` into a dated release section with
  `./publish-helm.sh --prep X.Y.Z`.
- After the prep branch is merged to `main`, run
  `./publish-helm.sh --publish X.Y.Z` on clean synced `main`.
- The prep step updates both this chart-local changelog and `Chart.yaml`.
- The publish step only creates and pushes tag `nccl-test-chart-vX.Y.Z`; the
  GitHub Actions workflow then packages and publishes the chart to OCI.
- Release section format:
  `## [nccl-test-chart-vX.Y.Z] - YYYY-MM-DD`

## [Unreleased]

### Added

- Added chart-local release metadata and `publish-helm.sh` so future chart
  releases follow one canonical prep/publish path.
- Added `.helmignore` so local release-helper files do not change packaged
  chart bytes.

### Changed

- Simplified the chart publish workflow to the tag-only OCI path driven by the
  release helper and anonymous public-pull verification.
- Switched the default NCCL runtime image to
  `cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/images/nccl-test:0.2.0` and
  aligned the source-chart `values.yaml` benchmark defaults with the practical
  `nebius-cxcli` deploy-time profile (`NCCL_DEBUG=WARN`, bounded iteration
  count, warmups, and timeout) so the first-party chart owns the shared
  runtime baseline directly.
- Bumped the chart to `0.2.5` so the portable OCI release can become the
  single source of truth for those shared NCCL runtime defaults, letting
  `nebius-cxcli` keep only the B200-specific MPI overlay in its catalog.

## [nccl-test-chart-v0.2.4] - 2026-04-16

### Changed

- Hardened the chart metadata, values schema, and image pull-secret handling so
  the default NCCL benchmark payload, launcher pods, and worker pods render
  consistently for Nebius GPU validation.
- Switched publication to the fixed shared Nebius Professional Services
  registry `nebius-proserv` and verified that the published OCI chart is
  anonymously pullable.
