# Lab 04: Evaluate strided access and the cost of repacking

Tensor shape does not reveal how neighboring values are arranged in memory. This lab compares a contiguous tensor, a transposed strided view, and a contiguous copy of that view while preserving each case's mathematical meaning.

## Run

```bash
# Normal execution
sbatch slurm/04_layout_and_coalescing.sbatch --workload small

# Optional profiling
sbatch slurm/04_layout_and_coalescing.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/04_layout_and_coalescing/jobs/<JOB_ID>/result.json
```

Optimize the full data path, not just the operation after a free-looking conversion.
