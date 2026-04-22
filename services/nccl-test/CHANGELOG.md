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
- Clarified the image/chart transport contract: the runtime image remains
  transport-agnostic for both Socket/TCPIP and RDMA-capable NCCL runs, while the shared `helm-charts/nccl-test` chart now owns a structured
  `benchmark.transport.*` contract so callers can select `auto`, `socket`, or `rdma` without baking HCA/interface names into the image.
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
  default model: the next chart publish moves to `0.2.7`, and
  `nebius-cxcli` now reads the shared image/tag plus benchmark defaults
  directly from `helm-charts/nccl-test/values.yaml` instead of duplicating
  them in its app catalog.
- Clarified the service README so it distinguishes the NVIDIA CUDA Ubuntu base
  images from the MPI layer installed on top: the runtime uses Ubuntu Open MPI
  packages rather than NVIDIA HPC-X, while the B200-only `-mca coll ^hcoll`
  note remains a catalog-level Nebius benchmark override rather than an
  image-baked HCOLL default.
- Clarified `build-image.sh` so the script and README now say explicitly that
  it builds from the selected NVIDIA CUDA tag and `NVIDIA/nccl-tests` ref,
  loads into local Docker by default, and only pushes to the registry encoded
  in `--tag` when `--push` is used.
- Tightened and reformatted `build-image.sh --help` so it now states the local
  `--load` default, tells users to pass a full remote image name in `--tag`
  for `--push`, and keeps the examples and column alignment consistent with
  that contract.
- Added Nebius registry login to `build-image.sh --push`: when the target tag
  points at `cr.<region>.nebius.cloud`, the script now reuses
  `NEBIUS_IAM_TOKEN` when present or fetches a token with
  `nebius iam get-access-token`, then runs `docker login` before pushing.
- Hardened `publish-image.sh` so `--prep` now requires a strictly clean
  worktree including untracked files, auto-sets upstream on a first branch
  push, and rejects duplicate release tags before editing the changelog;
  `--publish` now also fails before tagging if the target release section is
  missing or empty.
