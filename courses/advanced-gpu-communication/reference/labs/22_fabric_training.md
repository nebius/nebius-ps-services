# Lab 22: Tune microbatching at fixed global batch

Data parallelism replicates the model and partitions examples. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/22_fabric_training.sbatch --workload small --microbatch 1

# Optional profiling
sbatch slurm/22_fabric_training.sbatch --workload small --microbatch 1 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/22_fabric_training/jobs/<JOB_ID>/result.json
```

Read step duration and correctness for the selected microbatch; compare only runs with the same useful training work.
