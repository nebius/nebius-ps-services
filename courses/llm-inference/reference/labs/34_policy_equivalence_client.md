# Lab 34: Preserve outputs while changing a serving policy

A scheduling or caching policy should not silently change the intended greedy response while appearing to improve latency. This lab collects greedy response digests for one policy variant.

## Run

```bash
# Normal execution
sbatch slurm/34_policy_equivalence_client.sbatch --workload small

# Optional profiling
sbatch slurm/34_policy_equivalence_client.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/34_policy_equivalence_client/jobs/<JOB_ID>/result.json
```

Serving-policy optimization needs an explicit semantic gate as well as timing.
