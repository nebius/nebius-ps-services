# Lab 09: Build a causal compilation experiment

This capstone turns a promising optimization into a controlled comparison. You will evaluate eager versus compiled execution for the same BF16 matmul-and-activation pipeline, preserving shapes, inputs, residency, and mathematical work.

## Run

```bash
# Normal execution
sbatch slurm/09_capstone.sbatch --workload small

# Optional profiling
sbatch slurm/09_capstone.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/09_capstone/jobs/<JOB_ID>/result.json
```

A causal report combines controlled work, correctness, repeated observations, and a mechanism.
