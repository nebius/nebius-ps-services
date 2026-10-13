# Lab 36: Model KV retention and restore decisions

A reusable prefix is valuable only if compatible cached state survives until the next request and restoring it is worthwhile. This CPU lab models those decisions explicitly.

## Run

```bash
# Normal execution
sbatch slurm/36_kv_tiering.sbatch --host-prefixes 0 --storage-prefixes 0 --workload small

# Optional profiling
sbatch slurm/36_kv_tiering.sbatch --host-prefixes 0 --storage-prefixes 0 --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/36_kv_tiering/jobs/<JOB_ID>/result.json
```

A useful cache policy balances valid reuse, capacity, transfer and write costs.
