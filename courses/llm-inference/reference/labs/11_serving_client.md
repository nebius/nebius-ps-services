# Lab 11: Measure concurrent completion requests

A serving system's performance includes request handling and completion delivery, not just GPU kernels. This lab sends bounded concurrent requests to a local OpenAI-compatible endpoint and measures the entire client campaign.

## Run

```bash
# Normal execution
sbatch slurm/11_serving_client.sbatch --workload small

# Optional profiling
sbatch slurm/11_serving_client.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/11_serving_client/jobs/<JOB_ID>/result.json
```

Client measurements need declared arrival/load and output semantics.
