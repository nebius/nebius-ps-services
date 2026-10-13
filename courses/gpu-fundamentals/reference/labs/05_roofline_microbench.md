# Lab 05: Contrast memory-oriented and compute-oriented work

Roofline reasoning starts by counting work and data movement before looking at achieved speed. This lab provides two contrasting points: an elementwise vector expression and a matrix multiplication.

## Run

```bash
# Normal execution
sbatch slurm/05_roofline_microbench.sbatch --workload small

# Optional profiling
sbatch slurm/05_roofline_microbench.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/05_roofline_microbench/jobs/<JOB_ID>/result.json
```

This is a two-point classification exercise.
