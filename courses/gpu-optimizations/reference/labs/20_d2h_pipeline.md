# Lab 20: Drain a bounded D2H output pipeline

A GPU can finish producing an output while a copy or CPU consumer still owns its data. This lab builds the missing completion path.

## Run

```bash
# Normal execution
sbatch slurm/20_d2h_pipeline.sbatch --mode serial --slots 2 --workers 2 --sink-ms 2 --workload small

# Optional profiling
sbatch slurm/20_d2h_pipeline.sbatch --mode serial --slots 2 --workers 2 --sink-ms 2 --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/20_d2h_pipeline/jobs/<JOB_ID>/result.json
```

The measured operation ends only when all required outputs have been consumed.
