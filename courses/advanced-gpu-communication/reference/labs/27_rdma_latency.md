# Lab 27: Measure GPU-memory RDMA read completion latency

Remote direct memory access, or RDMA, lets a network adapter access registered memory without copying every message through the remote CPU. This experiment measures completion latency for reliable-connected RDMA reads.

## Run

Start with host memory, then replace `--memory host` with your qualified `--memory cuda-dmabuf` or `--memory cuda-peermem` path. Compare the same workload and payload size to isolate the memory path.

```bash
# Normal execution
sbatch slurm/27_rdma_latency.sbatch --workload small --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA" --memory host

# Optional profiling
sbatch slurm/27_rdma_latency.sbatch --workload small --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA" --memory host --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/27_rdma_latency/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
