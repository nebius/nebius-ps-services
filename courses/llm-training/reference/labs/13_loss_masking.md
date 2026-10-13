# Lab 13: Decide which tokens contribute to the training loss

Prompt text and padding may appear in a batch without being intended training targets. This lab constructs two short causal sequences and explicitly masks selected labels.

## Run

```bash
# Normal execution
sbatch slurm/13_loss_masking.sbatch --workload small

# Optional profiling
sbatch slurm/13_loss_masking.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/13_loss_masking/jobs/<JOB_ID>/result.json
```

Supervision boundaries are part of the objective.
