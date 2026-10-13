# Lab 06: Group unequal work while preserving output order

Neighboring lanes that execute different amounts of work can leave some lanes inactive while others continue. This lab compares alternating long/short work with a grouped arrangement that preserves every input's assigned calculation.

## Run

```bash
# Normal execution
sbatch slurm/06_divergence_tail.sbatch --workload small

# Optional profiling
sbatch slurm/06_divergence_tail.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/06_divergence_tail/jobs/<JOB_ID>/result.json
```

Regrouping is useful only when its complete cost is justified.
