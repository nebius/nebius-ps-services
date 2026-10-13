# Lab 33: Check greedy equivalence in a speculative engine campaign

Real-engine speculation must preserve the intended target output before a throughput comparison is accepted. This optional lab sends a fixed greedy workload to one engine variant and records private response digests.

## Run

```bash
# Normal execution
sbatch slurm/33_speculative_engine_client.sbatch --workload small

# Optional profiling
sbatch slurm/33_speculative_engine_client.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/33_speculative_engine_client/jobs/<JOB_ID>/result.json
```

Speculative speedup is conditional on workload, acceptance, and overhead.
