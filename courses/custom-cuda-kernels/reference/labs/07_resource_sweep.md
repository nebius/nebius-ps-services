# Lab 07: Relate block size, live state, and occupancy limits

More resident warps can help hide latency, but a kernel's registers, local storage, and block geometry constrain residency. This lab sweeps thread-block sizes and separately increases per-thread state.

## Run

```bash
# Normal execution
sbatch slurm/07_resource_sweep.sbatch --workload small

# Optional profiling
sbatch slurm/07_resource_sweep.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/07_resource_sweep/jobs/<JOB_ID>/result.json
```

Tune resources to the bottleneck rather than chasing a maximum occupancy percentage.
