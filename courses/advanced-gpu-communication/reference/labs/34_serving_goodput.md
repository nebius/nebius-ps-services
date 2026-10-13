# Lab 34: Tune serving goodput under latency objectives

Goodput is the rate of successfully completed requests that also meet declared service-level objectives. A server can increase raw throughput while violating latency limits.

## Run

```bash
# Normal execution
sbatch slurm/34_serving_goodput.sbatch --workload small --concurrency 8

# Optional profiling
sbatch slurm/34_serving_goodput.sbatch --workload small --concurrency 8 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/34_serving_goodput/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
