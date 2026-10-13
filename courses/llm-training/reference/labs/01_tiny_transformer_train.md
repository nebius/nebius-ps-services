# Lab 01: Trace a complete tiny-transformer training step

A training step transforms token IDs into predictions, evaluates their error, propagates gradients, and updates parameters. This lab makes that entire cycle small enough to inspect on one H100.

## Run

```bash
# Normal execution
sbatch slurm/01_tiny_transformer_train.sbatch --workload small

# Optional profiling
sbatch slurm/01_tiny_transformer_train.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/01_tiny_transformer_train/jobs/<JOB_ID>/result.json
```

Training acceptance needs observable learning signals and state changes, not merely a completed loop.
