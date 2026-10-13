# Lab 25: Explore input length and recurrent operator work

Long inputs and long output sequences stress different parts of an inference workload, but a small operator model must not be mislabeled as a language model. A recurrent update feeds the previous hidden state (the intermediate values retained between steps) into the next computation.

## Run

```bash
# Normal execution
sbatch slurm/25_workload_metrics.sbatch --workload small --concurrency 1

# Optional profiling
sbatch slurm/25_workload_metrics.sbatch --workload small --concurrency 1 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/25_workload_metrics/jobs/<JOB_ID>/result.json
```

Synthetic operators are useful only with honest boundaries.
