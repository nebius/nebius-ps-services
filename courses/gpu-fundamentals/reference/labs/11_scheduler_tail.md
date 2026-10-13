# Lab 11: Separate lane utilization from grid-tail behavior

Uneven work can waste execution capacity at more than one level. This lab deliberately separates a Python model of loop work within warps from an H100 probe of partial grid waves.

## Run

```bash
# Normal execution
sbatch slurm/11_scheduler_tail.sbatch --workload small

# Optional profiling
sbatch slurm/11_scheduler_tail.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/11_scheduler_tail/jobs/<JOB_ID>/result.json
```

Treat the simulated makespan as an optimistic estimate when interpreting the measured scheduler tail.
