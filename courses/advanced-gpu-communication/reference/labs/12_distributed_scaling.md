# Lab 12: Compare one GPU with two-node DDP

Adding a GPU adds both compute capacity and communication work. This lab runs a bounded training step on one GPU and through two-node DistributedDataParallel while preserving global batch size. Normal execution uses one GPU; --experiment two-node and Systems use two nodes.

## Run

```bash
# Normal execution
sbatch slurm/12_distributed_scaling.sbatch --workload small --global-batch 64

# Optional profiling
sbatch slurm/12_distributed_scaling.sbatch --workload small --global-batch 64 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/12_distributed_scaling/jobs/<JOB_ID>/result.json
```

Scaling is an end-to-end property with an explicit workload contract.
