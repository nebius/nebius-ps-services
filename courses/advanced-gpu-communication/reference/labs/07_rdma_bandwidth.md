# Lab 07: Validate host and GPU memory over InfiniBand

RDMA lets the NIC access registered memory without the CPU copying each payload. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/07_rdma_bandwidth.sbatch --workload small --memory host --server-device "${SERVER_HCA:?qualified mlx5 device}" --client-device "${CLIENT_HCA:?qualified mlx5 device}"

# Optional profiling
sbatch slurm/07_rdma_bandwidth.sbatch --workload small --memory host --server-device "${SERVER_HCA:?qualified mlx5 device}" --client-device "${CLIENT_HCA:?qualified mlx5 device}" --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/07_rdma_bandwidth/jobs/<JOB_ID>/result.json
```

Read validated perftest throughput for the selected memory path and NIC pair; CUDA tracing does not measure RDMA wire bandwidth.
