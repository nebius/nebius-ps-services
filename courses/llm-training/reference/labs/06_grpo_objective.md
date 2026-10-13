# Lab 06: Work through a group-relative policy objective

GRPO uses rewards from multiple completions of the same prompt to construct a relative training signal. This lab isolates the objective on small GPU tensors without loading a language model.

## Run

```bash
# Normal execution
sbatch slurm/06_grpo_objective.sbatch --workload small

# Optional profiling
sbatch slurm/06_grpo_objective.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/06_grpo_objective/jobs/<JOB_ID>/result.json
```

The objective converts relative reward into a constrained update signal.
