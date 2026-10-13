# Lab 11: Compare flat and hierarchical all-reduce

All-reduce returns the sum to every participant. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/11_collective_layout.sbatch --workload small --layout flat

# Optional profiling
sbatch slurm/11_collective_layout.sbatch --workload small --layout flat --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/11_collective_layout/jobs/<JOB_ID>/result.json
```

Read the reported flat or hierarchical collective duration only after its distributed correctness checks pass.
