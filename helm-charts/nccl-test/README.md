# nccl-test

This chart renders an ephemeral Kubeflow `MPIJob` that runs NVIDIA
`all_reduce_perf` from the shared Nebius runtime image built from the upstream
[`NVIDIA/nccl-tests`](https://github.com/NVIDIA/nccl-tests) project.

It is intended for `nebius-cxcli` deploy-time validation, not for a long-running
application release. The Kubeflow Training Operator must already be installed in
the target cluster before this chart is applied.

## Requirements

- Kubernetes `1.28+`
- Kubeflow Training Operator with the `MPIJob` CRD installed in the target
  cluster
- GPU worker nodes with the expected NCCL/RDMA runtime support

## Install

Minimal smoke render:

```bash
helm template smoke ./helm-charts/nccl-test \
  --namespace nccl-test \
  --set worker.replicas=2 >/dev/null
```

Minimal install:

```bash
helm upgrade --install nccl-test ./helm-charts/nccl-test \
  --namespace nccl-test \
  --create-namespace \
  --set worker.replicas=2
```

## OCI Publish

This chart can be published to Nebius Container Registry as an OCI Helm chart.
The validated repository shape for this chart is:

```text
oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test
```

The `<registry-short-id>` path segment is the registry ID without the leading
`registry-` prefix.

For GitHub Actions, use the environment `nccl-test-chart-publish` and set:

- Variables:
  `NB_TENANT_ID`, `NB_PROJECT_ID`, `NB_REGION_ID`, `NB_REGISTRY_ID`,
  `NB_SERVICE_ACCOUNT_ID`,
  `NB_SERVICE_ACCOUNT_PUBLIC_KEY_ID`
- Secret:
  `NB_SERVICE_ACCOUNT_PRIVATE_KEY`

Use placeholder values in docs and replace them with your own registry values:

```text
tenant_id: tenant-id-example
project_id: project-id-example
registry_id: registry-id-example
registry_name: registry-name-example
oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test
```

The publish workflow uses the registry ID you configure in the GitHub
environment. It does not create tenants, projects, or registries.

The canonical release flow is:

1. Add release notes to [CHANGELOG.md](CHANGELOG.md).
2. Run `./publish-helm.sh --prep X.Y.Z` from this chart directory.
3. Merge the prep branch to `main`.
4. Run `./publish-helm.sh --publish X.Y.Z` on clean synced `main`.
5. The tag push triggers `.github/workflows/nccl-test-chart-publish.yml`.

The prep step updates the chart-local changelog and `Chart.yaml`, then runs:

```bash
helm lint ./helm-charts/nccl-test
helm template smoke ./helm-charts/nccl-test --namespace nccl-test >/dev/null
```

Anonymous public pull is enabled on the shared registry. A clean unauthenticated
`helm pull` against the live Nebius registry succeeded for this chart path, so
public callers can pull the chart without a Nebius login.

Public pull example:

```bash
helm pull \
  oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test \
  --version <chart-version>
```

The OCI repository root for `helm push` is `.../charts`; Helm appends the
packaged chart name automatically. Pushing to `.../charts/nccl-test` creates
the wrong nested path `.../charts/nccl-test/nccl-test:<version>`.

The GitHub publish workflow now probes that nested path before every publish
attempt and fails fast if it already exists, so an old duplicate repository has
to be deleted before another chart release can proceed. An empty nested path
with no tags is treated as already cleaned up and does not block publication.

Direct OCI publish commands for debugging only:

```bash
if grep -q '^dependencies:' ./helm-charts/nccl-test/Chart.yaml; then
  helm dependency update ./helm-charts/nccl-test
fi
helm lint ./helm-charts/nccl-test
helm package ./helm-charts/nccl-test -d dist
nebius iam get-access-token | \
  helm registry login cr.eu-north1.nebius.cloud \
    --username iam \
    --password-stdin
helm push dist/nccl-test-<chart-version>.tgz \
  oci://cr.eu-north1.nebius.cloud/<registry-short-id>/charts
helm pull \
  oci://cr.eu-north1.nebius.cloud/<registry-short-id>/charts/nccl-test \
  --version <chart-version>
```

## Production Overrides

- Pin the runtime image immutably with `image.digest` whenever you need a
  reproducible benchmark payload.
- Use `imagePullSecrets` for chart-local registry credentials or
  `global.imagePullSecrets` when the surrounding release tooling injects shared
  credentials.
- Keep `job.launcherPrivileged` and `job.workerPrivileged` enabled unless you
  have already validated a non-privileged NCCL/RDMA runtime for your cluster.
- Override `benchmark.mpiExtraArgs` for platform-specific MPI flags instead of
  mutating the shared base arguments in the chart.
- The chart itself assumes the `MPIJob` CRD already exists. In the
  `nebius-cxcli` validation flow, Kubeflow Training Operator is treated as a
  transient prerequisite and installed/removed around the NCCL run instead of
  being managed as a persistent app release.

## Validation

```bash
helm lint ./helm-charts/nccl-test
helm template smoke ./helm-charts/nccl-test \
  --namespace nccl-test \
  --set worker.replicas=2 >/dev/null
helm template smoke ./helm-charts/nccl-test \
  --namespace nccl-test \
  --set worker.replicas=2 \
  --set 'imagePullSecrets[0]=registry-creds' \
  --set 'global.imagePullSecrets[0]=shared-registry-creds' >/dev/null
```

## Key Values

- `imagePullSecrets`, `global.imagePullSecrets`: image pull secrets applied to
  both launcher and worker pods. Use the chart-local key for per-release
  overrides and `global.*` for shared platform wiring.
- `image.repository`, `image.tag`, `image.digest`: NCCL runtime image reference.
  The default repository is
  `cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test`, and the tag
  defaults to `0.2.0`. Use a digest for immutable production pinning.
- `benchmark.mpiBaseArgs`: shared `mpirun` arguments applied on every platform.
- `benchmark.mpiExtraArgs`: platform-specific extra `mpirun` arguments. Keep
  the shared chart default empty. In the official Nebius NCCL guide, the B200
  example adds `-mca coll ^hcoll` while the H100/H200 example omits it, so
  `nebius-cxcli` injects that flag only for B200 platforms instead of baking
  it into global chart defaults. See:
  https://docs.nebius.com/kubernetes/gpu/nccl-test.
- `benchmark.args`: arguments passed directly to `all_reduce_perf`.
- The source chart carries the shared first-party image/tag and the practical
  deploy-time benchmark args directly in `values.yaml`. In the bundled
  `nebius-cxcli` flow, the chart defaults are consumed directly from this chart
  and only the B200-specific `benchmark.mpiExtraArgs` overlay remains
  catalog-owned.
- `worker.replicas`: number of MPI workers. `nebius-cxcli` derives this from
  the ready GPU node count at deploy time.
- `worker.gpus`: GPUs requested per worker pod.
- `report.jsonBeginMarker` and `report.jsonEndMarker`: log markers used by
  `nebius-cxcli` to extract the benchmark JSON payload.

## Example

```bash
helm template smoke ./helm-charts/nccl-test \
  --namespace nccl-test \
  --set worker.replicas=2 \
  --set image.repository=cr.<region>.nebius.cloud/<registry-short-id>/\
images/nccl-test \
  --set image.tag=0.2.0 \
  --set 'imagePullSecrets[0]=registry-creds'
```

## Verify and Troubleshoot

- Confirm the Training Operator is present before installing:

```bash
kubectl get deployment -n kubeflow training-operator
```

- Inspect the rendered `MPIJob` and its launcher pod:

```bash
kubectl get mpijob -n nccl-test nccl-test
kubectl get pods -n nccl-test \
  -l training.kubeflow.org/job-name=nccl-test,training.kubeflow.org/replica-type=launcher
```

- Follow launcher logs and capture the JSON markers consumed by
  `nebius-cxcli`:

```bash
kubectl logs -n nccl-test -f <launcher-pod-name>
```

## Notes

- The chart intentionally keeps the launcher and worker containers privileged.
  NCCL and RDMA validation on Nebius GPU clusters depends on that runtime
  contract.
- `nebius-cxcli` owns deploy-time image overrides and platform-specific MPI
  flags such as the B200 `-mca coll ^hcoll` overlay. The shared runtime image
  and common benchmark shape now live in the chart defaults; only
  platform-specific overlays stay catalog-owned. The official Nebius NCCL
  guide shows that flag only in the B200 example, not in the H100/H200 one:
  https://docs.nebius.com/kubernetes/gpu/nccl-test.
- The canonical release helper is
  [publish-helm.sh](publish-helm.sh).
  The tag-driven publish workflow lives at
  `.github/workflows/nccl-test-chart-publish.yml` and triggers only on tags
  matching `nccl-test-chart-vMAJOR.MINOR.PATCH`.
