# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before release tagging, move `Unreleased` into a dated release section with
  `./publish-image.sh --prep X.Y.Z`.
- After changelog PR merge to `main`, run
  `./publish-image.sh --publish X.Y.Z` on clean synced `main`.
- Release section format:
  `## [nccl-test-vX.Y.Z] - YYYY-MM-DD`

## [Unreleased]

- Added an Ubuntu 24.04 based NCCL test container image build with MPI support,
  a Nebius registry publish workflow, and a local image build helper script.
- Aligned local `build-image.sh --push` output with CI by disabling default
  Buildx provenance and SBOM attestations, avoiding extra untagged registry
  artifacts under the public `images/nccl-test` repository.
- Documented `0.2.0` as the current first-party image release consumed by the
  bundled `nebius-cxcli` NCCL validation path.
- Aligned the first-party image and chart source contracts so the local
  `helm-charts/nccl-test` defaults now point at the same `0.2.0` image/tag and
  practical benchmark profile, and the built image now exposes its CUDA base
  tag plus upstream `NCCL_TESTS_REF` in runtime metadata for easier inspection.
- Aligned the portable NCCL chart release contract with that source-owned
  default model: the next chart publish moves to `0.2.5`, and
  `nebius-cxcli` now reads the shared image/tag plus benchmark defaults
  directly from `helm-charts/nccl-test/values.yaml` instead of duplicating
  them in its app catalog.
- Hardened `publish-image.sh` so `--prep` now requires a strictly clean
  worktree including untracked files, auto-sets upstream on a first branch
  push, and rejects duplicate release tags before editing the changelog;
  `--publish` now also fails before tagging if the target release section is
  missing or empty.
