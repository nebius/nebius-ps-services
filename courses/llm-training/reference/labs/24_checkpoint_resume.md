# Lab 24: Reproduce the next update after checkpoint restoration

Saving weights alone does not guarantee that resumed training follows the same trajectory. Optimizer state and random-number generators also affect the next batch and update.

## Run

```bash
# Normal execution
sbatch slurm/24_checkpoint_resume.sbatch --workload small

# Optional profiling
sbatch slurm/24_checkpoint_resume.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/24_checkpoint_resume/jobs/<JOB_ID>/result.json
```

Checkpoint correctness is a reproduced state transition, not merely a saved file.
