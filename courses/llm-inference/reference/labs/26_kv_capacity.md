# Lab 26: Calculate ideal KV storage for MHA, GQA, and MQA

KV-cache capacity depends on stored key/value heads, not simply the number of query heads. This lab calculates ideal storage for multi-head, grouped-query, and multi-query attention and checks a small tensor allocation's byte arithmetic.

## Run

```bash
# Normal execution
sbatch slurm/26_kv_capacity.sbatch --workload small --sequence 4096 --concurrency 8

# Optional profiling
sbatch slurm/26_kv_capacity.sbatch --workload small --sequence 4096 --concurrency 8 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/26_kv_capacity/jobs/<JOB_ID>/result.json
```

Capacity arithmetic is a planning tool, not a measured fit result.
