# Lab 17: Compare greedy and seeded stochastic generation

Generation settings change both output behavior and the amount of work performed, so a performance comparison must declare them. This lab compares greedy decoding with a seeded stochastic profile on a pinned model.

## Run

```bash
# Normal execution
sbatch slurm/17_sampling_semantics.sbatch --workload small --max-new-tokens 32

# Optional profiling
sbatch slurm/17_sampling_semantics.sbatch --workload small --max-new-tokens 32 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/17_sampling_semantics/jobs/<JOB_ID>/result.json
```

Fix sampling and stopping semantics before timing comparisons.
