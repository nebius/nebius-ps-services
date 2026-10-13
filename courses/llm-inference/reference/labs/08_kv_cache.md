# Lab 08: Compare cached attention with full recomputation

Autoregressive generation repeatedly needs keys and values for earlier positions. A KV cache preserves those tensors so each new step can reuse them instead of recomputing the entire prefix.

## Run

```bash
# Normal execution
sbatch slurm/08_kv_cache.sbatch --workload small

# Optional profiling
sbatch slurm/08_kv_cache.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/08_kv_cache/jobs/<JOB_ID>/result.json
```

Caching exchanges repeated computation for persistent state and growing reads.
