# Lab 04: Compare atomic, hierarchical, and CUB reductions

A reduction combines many inputs into a small output, so contention and aggregation strategy matter. This lab sums a vector using one atomic per element, warp/block aggregation followed by one atomic per block, and CUB's maintained device reduction.

## Run

```bash
# Normal execution
sbatch slurm/04_reduction.sbatch --workload small

# Optional profiling
sbatch slurm/04_reduction.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/04_reduction/jobs/<JOB_ID>/result.json
```

Use hierarchical aggregation and compare against maintained primitives before writing more custom reduction code.
