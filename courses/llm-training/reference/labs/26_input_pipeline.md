# Lab 26: Diagnose a slow training-data producer

A training accelerator can wait because its next batch is not ready, even when the model itself is efficient. This lab compares a serial producer with worker-prefetched loading using deterministic sample identities and a configurable producer delay.

## Run

```bash
# Normal execution
sbatch slurm/26_input_pipeline.sbatch --workload small --workers 2 --prefetch 2

# Optional profiling
sbatch slurm/26_input_pipeline.sbatch --workload small --workers 2 --prefetch 2 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/26_input_pipeline/jobs/<JOB_ID>/result.json
```

Input optimization must preserve data completeness and order semantics.
