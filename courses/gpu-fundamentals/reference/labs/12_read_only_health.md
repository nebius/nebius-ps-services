# Lab 12: Read GPU health and sharing signals safely

A slow result may coincide with thermal, power, error, or sharing conditions that deserve investigation. This lab collects a bounded read-only snapshot and states what cannot be inferred from it.

## Run

```bash
# Normal execution
sbatch slurm/12_read_only_health.sbatch --workload small

# Optional profiling
sbatch slurm/12_read_only_health.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/12_read_only_health/jobs/<JOB_ID>/result.json
```

Operational evidence helps qualify a measurement but does not establish causality by itself.
