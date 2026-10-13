# Lab 07: Separate submission time from device completion

GPU work is normally queued asynchronously, so a short Python call does not mean a short GPU operation. This lab compares host submission time, serial device execution, and independent-stream execution for matrix work.

## Run

```bash
# Normal execution
sbatch slurm/07_async_streams.sbatch --workload small

# Optional profiling
sbatch slurm/07_async_streams.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/07_async_streams/jobs/<JOB_ID>/result.json
```

Streams express independent work; they do not manufacture additional hardware capacity.
