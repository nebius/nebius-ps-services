# Lab 20: Investigate reusable prompt prefixes in a live engine

Repeated prompt prefixes can reuse previously computed KV state, but visible text repetition alone does not prove a cache hit. This lab compares repeated-prefix and unique-prefix request cohorts against a local engine.

## Run

```bash
# Normal execution
sbatch slurm/20_prefix_cache_client.sbatch --workload small

# Optional profiling
sbatch slurm/20_prefix_cache_client.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/20_prefix_cache_client/jobs/<JOB_ID>/result.json
```

Cache claims require identity, workload, and engine evidence together.
