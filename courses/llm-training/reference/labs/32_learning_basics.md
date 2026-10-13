# Lab 32: Learn a weight and separate training from inference

This first exercise makes learning visible without a transformer, downloaded model or service. A scalar model predicts weight × input. Normal execution defaults to CPU; use --device cuda for the optional GPU path, which external diagnostics capture.

## Run

```bash
# Normal execution
sbatch slurm/32_learning_basics.sbatch --device cpu --workload small

# Optional profiling
sbatch slurm/32_learning_basics.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/32_learning_basics/jobs/<JOB_ID>/result.json
```

Training changes parameters through forward, loss, backward and optimizer steps.
