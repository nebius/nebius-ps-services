# Lab 01: Launch and validate a bounds-safe vector kernel

Vector addition is simple enough to expose the essential CUDA program structure without hiding it behind an algorithm. This lab allocates device buffers, transfers inputs, launches a one-dimensional kernel, and checks every output against a CPU reference.

## Run

```bash
# Normal execution
sbatch slurm/01_vector_add.sbatch --workload small

# Optional profiling
sbatch slurm/01_vector_add.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/01_vector_add/jobs/<JOB_ID>/result.json
```

Correct indexing, ownership, and checks precede tuning.
