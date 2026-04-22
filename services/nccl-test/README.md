# nccl-test

`services/nccl-test` builds and publishes a first-party NCCL benchmark image
for Nebius GPU clusters. The image compiles the current upstream
[`NVIDIA/nccl-tests`](https://github.com/NVIDIA/nccl-tests) sources with
`MPI=1` so it can run both the single-node quick checks from upstream and the
multi-node `MPIJob` flow used by Kubeflow Training Operator.

## Why this base image

- Base OS: Ubuntu 24.04, matching the current Nebius preference.
- CUDA base: official `nvidia/cuda` Ubuntu 24.04 images.
- Current default in this service:
  - `CUDA_IMAGE_TAG=13.2.0`
  - `NCCL_TESTS_REF=v2.18.3`
- Build strategy:
  - compile in `nvidia/cuda:<tag>-devel-ubuntu24.04`
  - run in `nvidia/cuda:<tag>-runtime-ubuntu24.04`

That keeps the runtime image smaller than a single-stage build while preserving
the CUDA, NCCL, Ubuntu Open MPI, SSH, and RDMA userspace pieces needed by an
`MPIJob`. This image uses NVIDIA's official CUDA Ubuntu base images and
installs the Ubuntu Open MPI packages instead of bundling NVIDIA HPC-X.

## Runtime contract

- NCCL test binaries live in `/opt/nccl_tests/build`.
- Binaries are also symlinked into `/usr/local/bin`, for example:
  - `all_reduce_perf`
  - `all_gather_perf`
  - `reduce_scatter_perf`
- The image includes `openssh-server` because Kubeflow MPI operator injects
  `/usr/sbin/sshd -De` for worker pods when no worker command is specified.
- The image stays root-compatible because MPI operator mounts SSH auth to
  `/root/.ssh` by default and the chart launcher already runs `mpirun` as root.
- The image now bakes `CUDA_IMAGE_TAG` and `NCCL_TESTS_REF` into the runtime
  environment, and exposes OCI labels that identify the Nebius source repo plus
  the upstream NVIDIA docs used by the build.
- The runtime stays transport-agnostic: it carries the userspace needed for
  both Socket/TCPIP NCCL runs and RDMA-capable runs, while the chart or caller decides whether to force `socket`, force `rdma`, or leave NCCL transport selection on `auto`.

## Chart alignment

- `helm-charts/nccl-test/values.yaml` now carries the shared first-party image
  tag `0.2.0` and the practical deploy-time benchmark defaults directly in the
  source chart.
- `services/nebius-cxcli/component_sources.yaml` now points portable consumers
  at chart version `0.2.7`, and the NCCL validation path reads the shared
  image/tag plus benchmark defaults directly from the chart instead of
  duplicating them in the catalog.
- The shared chart now owns a structured NCCL transport contract
  (`benchmark.transport.*`) so `nebius-cxcli` can switch cleanly between
  Ethernet-only Socket/TCPIP runs and RDMA-oriented runs without baking HCA or
  interface names into the image itself.
- The only NCCL chart behavior that remains catalog-owned is the
  platform-specific B200 `-mca coll ^hcoll` overlay.
- That B200 overlay reflects the Nebius benchmark recipe, not an HCOLL toggle
  baked into this image: the local runtime currently ships Ubuntu Open MPI with
  UCX-capable components, but not the HPC-X `coll:hcoll` / `coll:ucc`
  component stack used by the Nebius reference benchmark images.

## Files

- `Dockerfile`: multi-stage Ubuntu 24.04 CUDA build.
- `build-image.sh`: local Docker Buildx helper for day-to-day image iteration.
- `publish-image.sh`: release helper for changelog prep and Git tag publishing.
- `.github/workflows/nccl-test-image-publish.yml`: Nebius registry publish
  workflow triggered by `nccl-test-v*`.

## Local build

Use the local script for development builds instead of the release helper:

```bash
cd services/nccl-test

./build-image.sh
./build-image.sh --tag nccl-test:cuda13.2.0-v2.18.3
./build-image.sh --cuda-image-tag 13.2.0 --nccl-tests-ref v2.18.3
```

By default, `./build-image.sh` runs a local `docker buildx build --load` and
saves the result in your local Docker image cache under the selected tag. It
only pushes when you add `--push`, and in that case `--tag` should be the full
remote image name you want to publish, for example
`cr.eu-north1.nebius.cloud/<short-id>/images/nccl-test:dev`.
When the target registry is Nebius Container Registry, the script uses
`NEBIUS_IAM_TOKEN` when it is already set or fetches a token with
`nebius iam get-access-token`, then runs `docker login` before the push. Public
pulls from this registry can still remain anonymous.

Useful smoke checks after a local build:

```bash
docker run --rm --entrypoint /bin/bash nccl-test:dev -lc '
  set -euo pipefail
  which all_reduce_perf
  which mpirun
  test -x /usr/sbin/sshd
  printf "CUDA_IMAGE_TAG=%s\n" "$CUDA_IMAGE_TAG"
  printf "NCCL_TESTS_REF=%s\n" "$NCCL_TESTS_REF"
'
```

If you have GPUs locally and want a single-node quick test:

```bash
docker run --rm --gpus all nccl-test:dev \
  all_reduce_perf -b 8 -e 128M -f 2 -g 1
```

When you push with `./build-image.sh --push`, the script disables default
Buildx provenance and SBOM attestations so the pushed artifact shape matches
the CI workflow: a single tagged image manifest under
`.../images/nccl-test:<tag>` without extra untagged attestation artifacts.

## Publish workflow

The release flow matches the repo’s other tag-driven publish helpers:

```bash
cd services/nccl-test

# Move Unreleased notes into a dated release section and commit the changelog.
./publish-image.sh --prep 0.2.0

# After merge to main, push the release tag that triggers the workflow.
./publish-image.sh --publish 0.2.0
```

`--prep` now uses a strict clean-worktree check, including untracked files, and
auto-configures `origin/<current-branch>` as upstream on the first push when
needed. `--publish` also fails locally if the target release section is missing
or empty, so a release tag cannot bypass an incomplete changelog.

Tag format:

```text
nccl-test-vMAJOR.MINOR.PATCH
```

The GitHub workflow publishes immutable tags:

- `sha-<shortsha>`
- `<MAJOR.MINOR.PATCH>`
- `<MAJOR.MINOR.PATCH>-g<shortsha>`

`latest` is intentionally not published on normal tag pushes.

The workflow uses the same no-default-attestations publish contract as the
local build helper (`provenance: false`, `sbom: false`) so local `--push`
results and CI releases land in Nebius Container Registry with the same OCI
artifact shape.

## Nebius registry target

The workflow uses the same registry resolution pattern as the chart publish
flow:

- GitHub environment: `nccl-test-image-publish`
- Required variables:
  - `NB_TENANT_ID`
  - `NB_PROJECT_ID`
  - `NB_REGION_ID`
  - `NB_REGISTRY_ID`
  - `NB_SERVICE_ACCOUNT_ID`
  - `NB_SERVICE_ACCOUNT_PUBLIC_KEY_ID`
- Required secret:
  - `NB_SERVICE_ACCOUNT_PRIVATE_KEY`

The actual OCI repository shape is:

```text
cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test
```

Local pushes to that repository need authentication, while public pulls from
the published repository can remain anonymous.

Example placeholder:

```text
cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test
```

## Manual publish override

The workflow also exposes `workflow_dispatch` for explicit rebuilds. Manual
runs can publish a `sha-*` tag from a chosen ref that is already in `main`
history, and can optionally add a SemVer tag when you provide
`release_version`.
