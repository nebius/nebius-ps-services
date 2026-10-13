# Lab 33: Test KV-cache-aware request routing

A key/value cache stores attention state for tokens already processed. When requests reuse prefixes, a router can send work to a worker holding useful cached blocks.

## Run

```bash
# Normal execution
sbatch slurm/33_dynamo_routing.sbatch --workload small --router round-robin

# Optional profiling
sbatch slurm/33_dynamo_routing.sbatch --workload small --router round-robin --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/33_dynamo_routing/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
