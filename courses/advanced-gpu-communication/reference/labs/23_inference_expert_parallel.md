# Lab 23: Route inference tokens to expert owners

Expert-parallel inference sends tokens to the ranks that own their selected experts and returns outputs to the original order. This lab implements that round trip on two H100 nodes and reports expert load balance.

## Run

```bash
# Normal execution
sbatch slurm/23_inference_expert_parallel.sbatch --workload small

# Optional profiling
sbatch slurm/23_inference_expert_parallel.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/23_inference_expert_parallel/jobs/<JOB_ID>/result.json
```

EP performance combines routing cost and expert balance.
