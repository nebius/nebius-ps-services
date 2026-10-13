# Lab 14: Correlate PyTorch and Systems traces across ranks

Independent compute can overlap an asynchronous all-reduce only until a dependent consumer needs its result. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/14_distributed_profiling.sbatch --workload small --overlap off

# Optional profiling
sbatch slurm/14_distributed_profiling.sbatch --workload small --overlap off --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/14_distributed_profiling/jobs/<JOB_ID>/result.json
```

Use the trace to locate communication relative to compute; diagnostic duration is not clean benchmark evidence.
