# Lab 20: Follow pipeline and context partitions through backward

Pipeline parallelism divides layers, while context parallelism divides sequence positions. This lab demonstrates their ownership and gradient mechanics on two ranks with deliberately small computations.

## Run

```bash
# Normal execution
sbatch slurm/20_parallelism_mechanics.sbatch --workload small

# Optional profiling
sbatch slurm/20_parallelism_mechanics.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/20_parallelism_mechanics/jobs/<JOB_ID>/result.json
```

Parallelism begins with explicit state and tensor ownership.
