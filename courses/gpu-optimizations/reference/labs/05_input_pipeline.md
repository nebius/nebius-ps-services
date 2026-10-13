# Lab 05: Locate waiting in a DataLoader pipeline

GPU utilization can be limited by preparing and delivering the next batch rather than by the device computation itself. This lab compares zero and four DataLoader workers on deterministic synthetic samples.

## Run

```bash
# Normal execution
sbatch slurm/05_input_pipeline.sbatch --workload small

# Optional profiling
sbatch slurm/05_input_pipeline.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/05_input_pipeline/jobs/<JOB_ID>/result.json
```

Compare zero-worker and four-worker throughput for this synthetic dataset, after checking the delivered batches.
