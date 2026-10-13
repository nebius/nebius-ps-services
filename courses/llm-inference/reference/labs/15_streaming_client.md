# Lab 15: Observe first content and streaming gaps

Users experience streamed generation as a wait for first content followed by a sequence of arrivals. This lab records those client-side events from a loopback service.

## Run

```bash
# Normal execution
sbatch slurm/15_streaming_client.sbatch --workload small

# Optional profiling
sbatch slurm/15_streaming_client.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/15_streaming_client/jobs/<JOB_ID>/result.json
```

Measure what the client actually observes and name it accurately.
