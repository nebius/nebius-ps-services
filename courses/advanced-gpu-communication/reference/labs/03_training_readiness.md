# Lab 03: Verify the distributed training allocation

Training correctness depends on each process owning the intended device and participating in the same communication group. This lab checks one participating GPU rank on each of two eight-H100 workers, using one participating rank per worker before DDP, FSDP2, or parallelism experiments.

## Run

```bash
# Normal execution
sbatch slurm/03_training_readiness.sbatch --workload small

# Optional profiling
sbatch slurm/03_training_readiness.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/03_training_readiness/jobs/<JOB_ID>/result.json
```

Platform readiness is necessary but does not validate a training algorithm.
