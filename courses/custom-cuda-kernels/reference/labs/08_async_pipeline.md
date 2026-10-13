# Lab 08: Double-buffer global-to-shared copies

Asynchronous copies can prepare the next tile while the current tile is being processed, but correct staging and enough independent computation are essential. This lab compares a serial tiled transform with a double-buffered Cooperative Groups copy pipeline.

## Run

```bash
# Normal execution
sbatch slurm/08_async_pipeline.sbatch --workload small

# Optional profiling
sbatch slurm/08_async_pipeline.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/08_async_pipeline/jobs/<JOB_ID>/result.json
```

Pipelining requires an explicit producer/consumer contract.
