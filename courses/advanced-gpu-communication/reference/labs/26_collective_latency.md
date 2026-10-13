# Lab 26: Separate collective latency from payload throughput

An all-reduce combines values from every rank and returns the result to every participant. For a four-byte payload, launch, protocol and synchronization costs dominate.

## Run

```bash
# Normal execution
sbatch slurm/26_collective_latency.sbatch --workload small --algorithm auto

# Optional profiling
sbatch slurm/26_collective_latency.sbatch --workload small --algorithm auto --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/26_collective_latency/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
