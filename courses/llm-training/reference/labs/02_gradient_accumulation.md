# Lab 02: Trade microbatch size for peak memory

When a full batch does not fit, gradient accumulation can split its work across smaller microbatches before an update. This lab compares full-batch and accumulated gradients on a fixed-shape MLP regression problem.

## Run

```bash
# Normal execution
sbatch slurm/02_gradient_accumulation.sbatch --workload small

# Optional profiling
sbatch slurm/02_gradient_accumulation.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/02_gradient_accumulation/jobs/<JOB_ID>/result.json
```

Accumulation preserves an objective only with correct weighting and update boundaries.
