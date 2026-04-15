# nccl-test

This chart renders an ephemeral Kubeflow `MPIJob` that runs NVIDIA
`all_reduce_perf` from the upstream
[`NVIDIA/nccl-tests`](https://github.com/NVIDIA/nccl-tests) project.

It is intended for `nebius-cxcli` deploy-time validation, not for a long-running
application release. The Kubeflow Training Operator must already be installed in
the target cluster before this chart is applied.

## Validation

```bash
helm lint ./helm-charts/nccl-test
helm template smoke ./helm-charts/nccl-test \
  --namespace nccl-test \
  --set worker.replicas=2 >/dev/null
```

## Key Values

- `image.repository`, `image.tag`, `image.digest`: NCCL runtime image reference.
  Use a digest for immutable production pinning.
- `benchmark.mpiBaseArgs`: shared `mpirun` arguments applied on every platform.
- `benchmark.mpiExtraArgs`: platform-specific extra `mpirun` arguments.
- `benchmark.args`: arguments passed directly to `all_reduce_perf`.
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
  --set image.repository=cr.eu-north1.nebius.cloud/nebius-benchmarks/nccl-tests \
  --set image.tag=2.23.4-ubu22.04-cu12.4
```

## Notes

- The chart intentionally keeps the launcher and worker containers privileged.
  NCCL and RDMA validation on Nebius GPU clusters depends on that runtime
  contract.
- `nebius-cxcli` owns platform-aware overrides such as the B200 image tag and
  extra MPI flags. The chart values remain generic and reusable.
