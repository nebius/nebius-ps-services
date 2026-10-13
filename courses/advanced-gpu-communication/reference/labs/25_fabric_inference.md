# Lab 25: Compare request replicas with tensor-parallel decoding

Request replication assigns different requests to independent full-weight replicas. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/25_fabric_inference.sbatch --workload small --placement replicated

# Optional profiling
sbatch slurm/25_fabric_inference.sbatch --workload small --placement replicated --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/25_fabric_inference/jobs/<JOB_ID>/result.json
```

Read the selected placement’s latency and correctness; retain the same workload for any replicated-versus-sharded comparison.
