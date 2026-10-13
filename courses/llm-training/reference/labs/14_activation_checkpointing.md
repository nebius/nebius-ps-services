# Lab 14: Compare checkpointing on alternate blocks and all blocks

Activation checkpointing saves memory by recomputing selected forward work during backward instead of retaining every intermediate. This lab compares three matched tiny-transformer paths and checks loss plus all trainable parameter gradients.

## Run

```bash
# Normal execution
sbatch slurm/14_activation_checkpointing.sbatch --workload small

# Optional profiling
sbatch slurm/14_activation_checkpointing.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/14_activation_checkpointing/jobs/<JOB_ID>/result.json
```

This matched forward/backward comparison excludes optimizer work and optimizer state; optimizer-update equivalence is not tested.
